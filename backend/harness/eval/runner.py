"""H5 평가기 — A/B/C 비교 실험 러너 (명세 17장).

A_nano_single / C_large_single: 단일 프롬프트 audit_single@v1 (모든 파일 + 매니페스트 + 시스템 프롬프트 → JSON).
  실제 probe 실행 없이 매니페스트만 보고 인젝션 취약 여부를 예측한다. Harness H2~H4·H6~H8 효과는 받지 않는다.
B_nano_harness (D_large_harness): 8장 그래프 전체 (eval_config 설정 → 승인·수정 없이 report 까지).
같은 데이터셋 버전, 같은 파일 집합, 프롬프트 버전 고정. 결과는 그대로 기록한다 (수치 조작 금지).
"""

from __future__ import annotations

import logging
import statistics
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.db.models import Audit, AuditTarget, EvalResult, EvalRun, Target, User
from core.db.session import session_scope
from core.errors import AppError
from core.graph.build import build_graph
from core.graph.deps import Deps
from core.mcp_client import CallCtx, Gateway, http_gateway
from core.probes import load_probe_set
from core.rules.models import load_rules
from core.settings import get_settings, load_config
from harness.eval.scorer import AgentF, AuditOutput, CodeF, load_truth, score, with_cost
from harness.llm import make_chat_model, structured_call
from harness.masking import mask_text
from harness.observability import cost_usd, langsmith_config, set_trace_context
from harness.prompts import get_prompt

log = logging.getLogger("eval")

CONFIGS = ("A_nano_single", "B_nano_harness", "C_large_single", "D_large_harness")
SINGLE_MAX_CHARS = 200_000  # A/C 입력 상한 (초과분은 잘림을 기록)
AGENT_CATEGORIES = [
    "TOOL_NO_APPROVAL",
    "TOOL_EXCESSIVE_SCOPE",
    "TOOL_EXFIL_CHANNEL",
    "INDIRECT_INJECTION_SURFACE",
    "PROMPT_SECRET_EMBEDDED",
    "PROMPT_NO_INSTRUCTION_HIERARCHY",
]

Progress = Callable[[dict[str, Any]], Awaitable[None]]


class _SingleCode(BaseModel):
    file: str
    line: int
    category: str
    severity: str = "MEDIUM"
    reason: str = ""


class _SingleAgent(BaseModel):
    target: str
    category: str
    severity: str = "MEDIUM"


class _SingleProbe(BaseModel):
    probe_id: str
    vulnerable: bool


class SingleAuditOutput(BaseModel):
    code_findings: list[_SingleCode] = Field(default_factory=list)
    agent_findings: list[_SingleAgent] = Field(default_factory=list)
    probe_predictions: list[_SingleProbe] = Field(default_factory=list)


def config_model(cfg: str) -> str:
    m = str(load_config("models")["eval_configs"][cfg].get("model") or "")
    if not m:
        raise ValueError(f"{cfg}: model not configured (LLM_SMALL_MODEL / LLM_LARGE_MODEL)")
    return m


def dataset_version() -> str:
    return (get_settings().datasets_dir / "VERSION").read_text(encoding="utf-8").strip()


async def _targets() -> tuple[str, str, str]:
    """(repo_id, agent_id, admin_id) — 시드 대상."""
    async with session_scope() as db:
        repo = (
            await db.execute(select(Target).where(Target.type == "repo", Target.name == "vuln-spring-app"))
        ).scalar_one()
        agent = (
            await db.execute(select(Target).where(Target.type == "agent", Target.name == "cs-helper"))
        ).scalar_one()
        admin = (await db.execute(select(User).where(User.role == "ADMIN").order_by(User.created_at))).scalars().first()
        assert admin is not None
        return str(repo.id), str(agent.id), str(admin.id)


async def measure_probes(gw: Gateway, agent_id: str, admin_id: str) -> dict[str, bool]:
    """실제 값: vuln 버전 실측 ASR>0 (기본 probe, PROBE_ATTEMPTS 회)."""
    ps = load_probe_set()
    out: dict[str, bool] = {}
    ctx = CallCtx("probe_run", "ADMIN", admin_id)
    for p in ps.probes:
        if p.category == "BENIGN_CONTROL":
            out[p.id] = False  # 공격이 아님: '취약 아님'이 정답
            continue
        res = await gw.call(
            "run_injection_probe",
            {"target_id": agent_id, "probe_id": p.id, "attempts": int(get_settings().probe_attempts)},
            ctx,
        )
        out[p.id] = any(a["verdict"] == "SUCCESS" for a in res["attempts"])
    return out


async def _repo_text(gw: Gateway, repo_id: str, admin_id: str) -> tuple[str, bool]:
    ctx = CallCtx("eval_single", "ADMIN", admin_id)
    files = await gw.call("list_repo_files", {"target_id": repo_id}, ctx)
    parts: list[str] = []
    for f in files:
        start, lines = 1, []
        while True:
            r = await gw.call("read_repo_file", {"target_id": repo_id, "path": f["path"], "start_line": start}, ctx)
            lines.append(r["content_masked"])
            if r["end_line"] >= r["total_lines"]:
                break
            start = r["end_line"] + 1
        body = "\n".join(lines)
        numbered = "\n".join(f"{i:>4}| {ln}" for i, ln in enumerate(body.splitlines(), start=1))
        parts.append(f"=== {f['path']} ===\n{numbered}")
    text = "\n\n".join(parts)
    return (text[:SINGLE_MAX_CHARS], True) if len(text) > SINGLE_MAX_CHARS else (text, False)


async def run_single(cfg: str, gw: Gateway, repo_id: str, agent_id: str, admin_id: str) -> dict[str, Any]:
    model_name = config_model(cfg)
    files, truncated = await _repo_text(gw, repo_id, admin_id)
    m = await gw.call("get_agent_manifest", {"target_id": agent_id}, CallCtx("eval_single", "ADMIN", admin_id))
    ps = load_probe_set()
    probes = "\n".join(
        f"- {p.id} [{p.category}/{p.channel}] {' / '.join(p.turns)}"
        + (f" (KB 문서: {mask_text(' '.join(p.kb_docs))[:300]})" if p.kb_docs else "")
        for p in ps.probes
    )
    usage: list[dict[str, Any]] = []
    started = time.monotonic()
    out = await structured_call(
        make_chat_model(model_name),
        get_prompt("audit_single"),
        SingleAuditOutput,
        node="eval_single",
        model_name=model_name,
        budget=None,
        usage=usage,
        code_categories=", ".join(sorted({r.category for r in load_rules("code")})),
        agent_categories=", ".join(AGENT_CATEGORIES),
        probes=probes,
        manifest=m["manifest_text_masked"],
        system_prompt=m["system_prompt_masked"],
        files=files,
    )
    norm = AuditOutput(
        code=[
            CodeF(file=c.file.lstrip("./"), line=c.line, category=c.category, severity=c.severity.upper())
            for c in out.code_findings
        ],
        agent=[AgentF(target=a.target, category=a.category, severity=a.severity.upper()) for a in out.agent_findings],
        probe_vulnerable={p.probe_id: p.vulnerable for p in out.probe_predictions},
    )
    return {
        "output": norm,
        "usage": usage,
        "latency_s": time.monotonic() - started,
        "audit_id": None,
        "input_truncated": truncated,
    }


def harness_output(values: dict[str, Any]) -> AuditOutput:
    code, agent = [], []
    for f in values.get("correlated", []):
        ev = (f.get("evidence") or [{}])[0]
        if f["source"] == "code" and ev.get("file_path"):
            code.append(
                CodeF(
                    file=ev["file_path"],
                    line=int(ev.get("line_start") or 0),
                    category=f["category"],
                    severity=f["severity"],
                )
            )
        elif f["source"] == "agent" and not (f.get("rule_id") or "").startswith("PROBE-") and ": " in f["title"]:
            agent.append(AgentF(target=f["title"].split(": ", 1)[1], category=f["category"], severity=f["severity"]))
    vuln: dict[str, bool] = {}
    for r in values.get("probe_results", []):
        base = r.get("base_probe_id") or r["probe_id"]
        if r.get("attempts"):
            vuln[base] = vuln.get(base, False) or r["successes"] > 0
    return AuditOutput(code=code, agent=agent, probe_vulnerable=vuln)


async def run_harness(cfg: str, repo_id: str, agent_id: str, admin_id: str) -> dict[str, Any]:
    config_model(cfg)
    audit_id = uuid.uuid4()
    async with session_scope() as db:
        db.add(
            Audit(
                id=audit_id,
                requested_by=uuid.UUID(admin_id),
                mode="full",
                status="RUNNING",
                eval_config=cfg,
                thread_id=f"audit:{audit_id}",
                started_at=datetime.now(UTC),
            )
        )
        await db.flush()
        db.add_all(
            [
                AuditTarget(audit_id=audit_id, target_id=uuid.UUID(repo_id)),
                AuditTarget(audit_id=audit_id, target_id=uuid.UUID(agent_id)),
            ]
        )
    aid = str(audit_id)
    set_trace_context(aid)
    graph = build_graph(InMemorySaver())
    config = {
        "configurable": {"thread_id": f"audit:{aid}", "deps": Deps(gateway=http_gateway())},
        "recursion_limit": 200,
        **langsmith_config(aid, {"eval_config": cfg}),
    }
    started = time.monotonic()
    init = {
        "audit_id": aid,
        "thread_id": f"audit:{aid}",
        "requested_by": admin_id,
        "actor_role": "ADMIN",
        "mode": "full",
        "eval_config": cfg,
        "targets": [{"target_id": repo_id}, {"target_id": agent_id}],
        "errors": [],
        "token_usage": [],
    }
    status = "COMPLETED"
    try:
        await graph.ainvoke(init, config)  # type: ignore[arg-type,call-overload]
    except Exception:
        status = "FAILED"
        raise
    finally:
        values = (await graph.aget_state(config)).values  # type: ignore[arg-type]
        async with session_scope() as db:
            a = await db.get(Audit, audit_id)
            if a is not None:
                a.status, a.finished_at, a.current_node = status, datetime.now(UTC), "report"
                a.summary = {"eval": True, "total": len(values.get("correlated", []))}
    return {
        "output": harness_output(values),
        "usage": values.get("token_usage", []),
        "latency_s": time.monotonic() - started,
        "audit_id": aid,
        "input_truncated": False,
        "errors": values.get("errors", []),
    }


def _agg(results: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    keys = [k for k, v in results[0]["metrics"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    out: dict[str, dict[str, float | None]] = {}
    for k in keys:
        vals = [r["metrics"][k] for r in results if isinstance(r["metrics"].get(k), (int, float))]
        out[k] = {
            "mean": round(statistics.mean(vals), 4) if vals else None,
            "std": round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0,
        }
    return out


def render_report(
    run_id: str, configs: list[str], by_cfg: dict[str, list[dict[str, Any]]], actual: dict[str, bool]
) -> str:
    cols = [
        "run_failed",
        "code_recall",
        "code_precision",
        "code_f1",
        "static_evasion_recall",
        "agent_recall",
        "probe_accuracy",
        "severity_accuracy",
        "evidence_accuracy",
        "auditor_robustness",
        "fp_count_on_decoys",
        "tokens",
        "cost_usd",
        "latency_s",
        "f1_per_dollar",
    ]
    lines = [
        f"# A/B/C 비교 실험 — {run_id[:8]}",
        "",
        f"- 데이터셋: {dataset_version()}",
        f"- 구성: {', '.join(configs)} · 반복 {len(next(iter(by_cfg.values())))}회 (평균 ± 표준편차)",
        f"- probe 실측 취약: {sorted(k for k, v in actual.items() if v)}",
        "",
        "| 지표 | " + " | ".join(configs) + " |",
        "|---|" + "---|" * len(configs),
    ]
    agg = {c: _agg(by_cfg[c]) for c in configs if by_cfg.get(c)}
    for k in cols:
        cells = []
        for c in configs:
            m = agg.get(c, {}).get(k)
            cells.append("-" if not m or m["mean"] is None else f"{m['mean']}" + (f" ± {m['std']}" if m["std"] else ""))
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 케이스별 (첫 반복)",
        "",
        "| 케이스 | " + " | ".join(configs) + " |",
        "|---|" + "---|" * len(configs),
    ]
    first = {c: by_cfg[c][0]["per_case"] for c in configs if by_cfg.get(c)}
    for case in sorted({k for pc in first.values() for k in pc}):
        row = [first.get(c, {}).get(case, "-") for c in configs]
        mark = (
            " ⭐"
            if "B_nano_harness" in first
            and first["B_nano_harness"].get(case) in ("TP", "ROBUST")
            and all(first[c].get(case) not in ("TP", "ROBUST") for c in first if c != "B_nano_harness")
            else ""
        )
        lines.append(f"| {case}{mark} | " + " | ".join(row) + " |")
    lines += ["", "⭐ = B 만 잡은 케이스", "", "## 해석 (자동 생성, 수치 그대로)"]
    if "B_nano_harness" in agg and "A_nano_single" in agg:
        b, a = agg["B_nano_harness"]["code_f1"]["mean"], agg["A_nano_single"]["code_f1"]["mean"]
        lines.append(f"- 같은 작은 모델에서 Harness 적용 시 code F1: A {a} → B {b}.")
    if "B_nano_harness" in agg and "C_large_single" in agg:
        b2, c2 = agg["B_nano_harness"]["code_f1"]["mean"], agg["C_large_single"]["code_f1"]["mean"]
        bt, ct = agg["B_nano_harness"]["tokens"]["mean"], agg["C_large_single"]["tokens"]["mean"]
        lines.append(f"- B 대 상한선 C: code F1 {b2} vs {c2}, 토큰 {bt} vs {ct}.")
    if any(by_cfg[c][0]["metrics"].get("cost_usd") == 0 for c in by_cfg):
        lines.append("- 비용 0 은 config/pricing.yaml 가격 미기입 때문이다 (토큰 수로 비교).")
    return "\n".join(lines) + "\n"


async def run_eval(
    configs: list[str], repeat: int = 1, eval_run_id: str | None = None, progress: Progress | None = None
) -> dict[str, Any]:
    for c in configs:
        if c not in CONFIGS:
            raise ValueError(f"unknown config {c}")
    repo_id, agent_id, admin_id = await _targets()
    async with session_scope() as db:
        run = await db.get(EvalRun, uuid.UUID(eval_run_id)) if eval_run_id else None
        if run is None:
            run = EvalRun(
                id=uuid.UUID(eval_run_id) if eval_run_id else uuid.uuid4(),
                requested_by=uuid.UUID(admin_id),
                configs=configs,
                dataset_version=dataset_version(),
                repeat=repeat,
                status="RUNNING",
            )
            db.add(run)
        run.status = "RUNNING"
        run_id = str(run.id)
    set_trace_context(None, run_id)
    truth = load_truth()
    try:
        if progress:
            await progress({"stage": "measure_probes"})
        actual = await measure_probes(http_gateway(), agent_id, admin_id)
        by_cfg: dict[str, list[dict[str, Any]]] = {}
        for cfg in configs:
            for i in range(repeat):
                if progress:
                    await progress({"stage": "run", "config": cfg, "repeat": i})
                started = time.monotonic()
                try:
                    res = await (
                        run_harness(cfg, repo_id, agent_id, admin_id)
                        if "harness" in cfg
                        else run_single(cfg, http_gateway(), repo_id, agent_id, admin_id)  # 실행 1회 = 게이트 1개
                    )
                    run_error = None
                except AppError as e:
                    # 출력 스키마 실패 등 실행 실패도 결과다: 빈 결과로 채점하고 실패를 기록한다 (수치 조작 금지)
                    log.warning("eval %s %s#%d failed: %s", run_id[:8], cfg, i, e.code)
                    res = {
                        "output": AuditOutput(),
                        "usage": [],
                        "latency_s": time.monotonic() - started,
                        "audit_id": None,
                        "input_truncated": False,
                    }
                    run_error = f"{e.code}: {e.message[:200]}"
                metrics, per_case = score(res["output"], truth, actual)
                tokens = sum(int(u.get("in", 0)) + int(u.get("out", 0)) for u in res["usage"])
                usd = sum(cost_usd(u.get("model"), int(u.get("in", 0)), int(u.get("out", 0))) for u in res["usage"])
                metrics = with_cost(metrics, usd, res["latency_s"], tokens)
                metrics["input_truncated"] = res["input_truncated"]
                metrics["run_failed"] = 1 if run_error else 0
                metrics["run_error"] = run_error
                async with session_scope() as db:
                    db.add(
                        EvalResult(
                            eval_run_id=uuid.UUID(run_id),
                            config=cfg,
                            repeat_idx=i,
                            audit_id=uuid.UUID(res["audit_id"]) if res["audit_id"] else None,
                            metrics=metrics,
                            per_case=per_case,
                            cost_usd=usd,
                            latency_s=res["latency_s"],
                        )
                    )
                by_cfg.setdefault(cfg, []).append({"metrics": metrics, "per_case": per_case})
                log.info("eval %s %s#%d f1=%s", run_id[:8], cfg, i, metrics.get("code_f1"))
        report = render_report(run_id, configs, by_cfg, actual)
        out_dir = get_settings().reports_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"eval_{run_id}.md").write_text(report, encoding="utf-8")
        status = "COMPLETED"
        return {
            "eval_run_id": run_id,
            "report_md": report,
            "actual_probe": actual,
            "summary": {c: _agg(v) for c, v in by_cfg.items()},
        }
    except Exception:
        status = "FAILED"
        raise
    finally:
        async with session_scope() as db:
            r = await db.get(EvalRun, uuid.UUID(run_id))
            if r is not None:
                r.status = status
