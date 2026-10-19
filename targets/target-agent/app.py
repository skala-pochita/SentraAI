"""샘플 사내 CS 에이전트 cs-helper (감사 대상, 명세 6.2).

POST /chat {session_id, message, kb_docs?} → {reply, tool_calls, latency_ms}
매니페스트·시스템 프롬프트는 요청마다 AGENT_CONFIG_DIR 에서 다시 읽는다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from policy import TOOL_PARAMS, execute_tool

log = logging.getLogger("target-agent")
logging.basicConfig(level=logging.INFO)

HERE = Path(__file__).resolve().parent
MAX_TOOL_ROUNDS = 4
MAX_HISTORY = 12

app = FastAPI(title="target-agent (cs-helper)")
_sessions: dict[str, list[BaseMessage]] = defaultdict(list)
_call_log: list[dict[str, Any]] = []


class ChatIn(BaseModel):
    session_id: str
    message: str
    kb_docs: list[str] | None = None


class ToolCallOut(BaseModel):
    name: str
    args: dict[str, Any]
    executed: bool


class ChatOut(BaseModel):
    reply: str
    tool_calls: list[ToolCallOut]
    latency_ms: int


def _expand_env(text: str) -> str:
    return re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: os.environ.get(m.group(1), ""), text)


def load_agent_config() -> tuple[dict[str, Any], str]:
    """(manifest, system_prompt). /workspace/agent-config 우선, 없으면 로컬 variant 파일."""
    cfg_dir = Path(os.environ.get("AGENT_CONFIG_DIR", "/workspace/agent-config"))
    manifest_path = cfg_dir / "manifest.yaml"
    prompt_path = cfg_dir / "system_prompt.md"
    if not manifest_path.is_file():
        variant = os.environ.get("AGENT_VARIANT", "vuln")
        manifest_path = HERE / "manifests" / f"{variant}.yaml"
        prompt_path = None  # type: ignore[assignment]
    manifest = yaml.safe_load(_expand_env(manifest_path.read_text(encoding="utf-8")))
    if prompt_path is None or not prompt_path.is_file():
        prompt_path = HERE / manifest["system_prompt_file"]
    return manifest, prompt_path.read_text(encoding="utf-8")


def _tools_for_llm(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", t["name"]),
                "parameters": TOOL_PARAMS.get(t["name"], {"type": "object", "properties": {}}),
            },
        }
        for t in manifest.get("tools", [])
    ]


def _llm(model: str) -> Any:
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {"model": model, "timeout": 25, "max_retries": 1}
    temp = os.environ.get("AGENT_TEMPERATURE")
    if temp:
        kwargs["temperature"] = float(temp)
    return ChatOpenAI(**kwargs)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/manifest")
async def manifest() -> dict[str, Any]:
    m, _ = load_agent_config()
    return m


@app.get("/calls")
async def calls(limit: int = 50) -> list[dict[str, Any]]:
    return _call_log[-limit:]


@app.post("/chat", response_model=ChatOut)
async def chat(body: ChatIn) -> ChatOut:
    started = time.monotonic()
    manifest, system_prompt = load_agent_config()
    model = manifest.get("model") or os.environ.get("LLM_SMALL_MODEL", "")
    if not model or not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(503, "LLM not configured (OPENAI_API_KEY / LLM_SMALL_MODEL)")
    llm = _llm(model).bind_tools(_tools_for_llm(manifest))

    history = _sessions[body.session_id]
    messages: list[BaseMessage] = [SystemMessage(system_prompt), *history, HumanMessage(body.message)]
    tool_calls: list[ToolCallOut] = []
    reply = ""
    for _ in range(MAX_TOOL_ROUNDS + 1):
        try:
            ai: AIMessage = await llm.ainvoke(messages)
        except Exception as e:  # noqa: BLE001 - 상위(probe 실행기)에 구조화된 5xx 로 전달
            log.warning("llm error: %s", type(e).__name__)
            raise HTTPException(502, f"LLM error: {type(e).__name__}") from e
        messages.append(ai)
        if not ai.tool_calls:
            reply = ai.content if isinstance(ai.content, str) else json.dumps(ai.content, ensure_ascii=False)
            break
        for tc in ai.tool_calls:
            result, executed = execute_tool(manifest, tc["name"], tc.get("args") or {}, body.kb_docs)
            tool_calls.append(ToolCallOut(name=tc["name"], args=tc.get("args") or {}, executed=executed))
            messages.append(ToolMessage(result, tool_call_id=tc["id"]))
    else:
        reply = "(도구 호출 한도 초과)"

    history.extend([HumanMessage(body.message), AIMessage(reply)])
    del history[:-MAX_HISTORY]
    latency = int((time.monotonic() - started) * 1000)
    _call_log.append({"session_id": body.session_id, "tool_calls": [t.model_dump() for t in tool_calls],
                      "latency_ms": latency, "ts": time.time()})
    del _call_log[:-500]
    return ChatOut(reply=reply, tool_calls=tool_calls, latency_ms=latency)
