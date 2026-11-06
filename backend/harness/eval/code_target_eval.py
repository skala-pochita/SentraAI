"""대상별 코드 감사 평가 (EV1·EV2·EV5): 정답셋을 지정해 코드 감사를 실행하고 채점한다.

  cd backend && DATABASE_URL=...localhost.../auditor_eval REDIS_URL=redis://localhost:6379/0 \
    uv run python -m harness.eval.code_target_eval --target vuln-python-app --truth code_python.yaml \
      --configs S,SX,H --repeat 3 --out ../reports/code_eval_python.md

구성
- S  : 내장 정적 규칙만 (LLM 없음)
- SX : 내장 규칙 + 외부 SAST·SCA (LLM 없음)
- H  : 코드 감사 서브그래프 전체 — 정적 → 외부 → ReAct 조사(LLM) → 근거 검증. LLM 키가 있어야 한다

전체 A/B/C 러너(harness/eval/runner.py)는 Kafka·워커를 거친 전체 감사(코드+에이전트)를 재지만, 이 도구는
코드 감사 서브그래프만 같은 프로세스에서 실행한다 (in-process MCP). 노드·도구·프롬프트는 운영과 같다.
작업공간은 임시 디렉터리에 복사해 git 초기화하므로 원본 targets/ 는 바뀌지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from core.settings import REPO_ROOT, get_settings
from harness.eval.code_eval import score_code

METRICS = ("recall", "precision", "f1", "fp", "evasion_recall", "injection_robustness", "auditor_injections_detected")


def _truth(name: str) -> dict[str, Any]:
    data = yaml.safe_load((get_settings().datasets_dir / "ground_truth" / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _evasion_ids(truth: dict[str, Any]) -> list[str]:
    """정적 규칙 회피 케이스 (note 에 '정적 규칙 회피' 가 있는 정답)."""
    return [f["id"] for f in truth["findings"] if "정적 규칙 회피" in str(f.get("note", ""))]


def _as_findings(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for d in drafts:
        if d.get("verdict") == "FALSE_POSITIVE":
            continue
        ev = (d.get("evidence") or [{}])[0]
        out.append(
            {
                "file": ev.get("file_path"),
                "line": ev.get("line_start") or 0,
                "category": d["category"],
                "severity": d["severity"],
            }
        )
    return out


async def _static(root: Path, external: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from core.external_scanners import run_external
    from core.graph.nodes.code_static_scan import _new_external_hits
    from core.rules.engine import scan_repo

    internal = [h.model_dump() for h in scan_repo(root)]
    hits = internal
    status: dict[str, Any] = {}
    if external:
        res = await run_external(root, sorted(get_settings().external_scanners))
        hits = internal + _new_external_hits(internal, [h.model_dump() for h in res.hits])
        status = res.status
    return [
        {"file": h["path"], "line": h["line"], "category": h["category"], "severity": h["severity"]} for h in hits
    ], status


async def _harness(target_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from core.graph.build import build_code_audit
    from core.graph.deps import Deps
    from core.mcp_client import inprocess_gateway

    deps = Deps(gateway=inprocess_gateway(), emit_events=False)
    if deps.llm("code_investigate", "B_nano_harness") is None:
        raise RuntimeError("H 구성은 OPENAI_API_KEY 가 필요하다")
    st = {
        "audit_id": None,
        "eval_config": "B_nano_harness",
        "actor_role": "ADMIN",
        "target": {"target_id": target_id, "type": "repo", "name": "eval"},
        "probe_round": 0,
    }
    from core.graph.nodes.intake import intake  # 위험 파일 계획은 운영과 같은 intake 로 만든다

    plan = await intake(
        {"targets": [{"target_id": target_id}], "audit_id": str(uuid.uuid4()), "actor_role": "ADMIN"},
        {"configurable": {"deps": deps}},
    )
    st["plan"] = plan["plan"]
    out = await build_code_audit().ainvoke(st, {"configurable": {"deps": deps}, "recursion_limit": 50})
    return out.get("code_findings", []), out.get("token_usage", []), out.get("errors", [])


async def _register(root: Path) -> tuple[str, Any]:
    from api.seed import seed_users
    from core.db.models import Target
    from core.db.session import session_scope

    async with session_scope() as db:
        users = await seed_users(db)
        t = Target(
            type="repo", name=f"eval-{root.name}-{uuid.uuid4().hex[:6]}", ref=str(root), owner_user_id=users["ADMIN"].id
        )
        db.add(t)
        await db.flush()
        return str(t.id), t.id


async def _unregister(tid: Any) -> None:
    from sqlalchemy import delete

    from core.db.models import Finding, Target
    from core.db.session import session_scope

    async with session_scope() as db:
        await db.execute(delete(Finding).where(Finding.target_id == tid))
        await db.execute(delete(Target).where(Target.id == tid))


def _workspace(target: str) -> Path:
    from mcp_servers.business.demo_reset import _init_repo

    root = Path(tempfile.mkdtemp(prefix="code-eval-")) / target
    shutil.copytree(REPO_ROOT / "targets" / target, root)
    _init_repo(root, "eval import")
    return root


async def run(target: str, truth_name: str, configs: list[str], repeat: int) -> dict[str, Any]:
    truth = _truth(truth_name)
    evasion = _evasion_ids(truth)
    root = _workspace(target)
    results: dict[str, list[dict[str, Any]]] = {c: [] for c in configs}
    tid, tid_raw = await _register(root)
    try:
        for cfg in configs:
            for _ in range(repeat if cfg == "H" else 1):  # 정적 구성은 결정적이라 1회
                started = time.monotonic()
                usage: list[dict[str, Any]] = []
                errors: list[dict[str, Any]] = []
                if cfg == "H":
                    drafts, usage, errors = await _harness(tid)
                    findings = _as_findings(drafts)
                    if any(str(e.get("code")) in ("UNAUTHORIZED", "ErrorCode.UNAUTHORIZED") for e in errors):
                        # LLM 인증 실패면 결과는 사실상 정적 판정이다 → H 수치로 보고하지 않는다 (수치 조작 금지)
                        raise RuntimeError("LLM 인증 실패 (OPENAI_API_KEY 확인): H 구성 결과를 만들 수 없다")
                else:
                    findings, _status = await _static(root, external=cfg == "SX")
                s = score_code(findings, truth)
                s["evasion_recall"] = (
                    round(sum(1 for i in evasion if i not in s["fn"]) / len(evasion), 4) if evasion else None
                )
                s["latency_s"] = round(time.monotonic() - started, 2)
                s["tokens"] = sum(int(u.get("in", 0)) + int(u.get("out", 0)) for u in usage)
                s["usd"] = round(sum(float(u.get("usd", 0)) for u in usage), 6)
                s["errors"] = [f"{e.get('code')}:{e.get('file', '')}" for e in errors]
                results[cfg].append(s)
                await _clear_lock(tid)
    finally:
        await _unregister(tid_raw)
        shutil.rmtree(root.parent, ignore_errors=True)
    return {"target": target, "truth": truth_name, "evasion_ids": evasion, "results": results}


async def _clear_lock(tid: str) -> None:
    from core.redis.client import get_redis

    await get_redis().delete(f"lock:audit:target:{tid}")


def _fmt(vals: list[Any]) -> str:
    xs = [v for v in vals if isinstance(v, (int, float))]
    if not xs:
        return "-"
    m = statistics.mean(xs)
    return f"{m:.4g}" if len(xs) == 1 else f"{m:.4g} ± {statistics.stdev(xs):.2g}"


def render(rep: dict[str, Any], repeat: int) -> str:
    cfgs = list(rep["results"])
    lines = [
        f"# 코드 감사 평가 — {rep['target']}",
        "",
        f"- 정답셋: `datasets/ground_truth/{rep['truth']}` · H 반복 {repeat}회 (평균 ± 표준편차) · 정적 회피 케이스: {', '.join(rep['evasion_ids']) or '-'}",
        "- S=내장 정적 규칙, SX=+외부 SAST·SCA, H=코드 감사 서브그래프 전체(정적→외부→ReAct→근거 검증)",
        "",
        "| 지표 | " + " | ".join(cfgs) + " |",
        "|---|" + "---|" * len(cfgs),
    ]
    for k in (*METRICS, "latency_s", "tokens", "usd"):
        lines.append(f"| {k} | " + " | ".join(_fmt([r.get(k) for r in rep["results"][c]]) for c in cfgs) + " |")
    lines += ["", "## 미탐 (첫 실행)", ""]
    for c in cfgs:
        first = rep["results"][c][0]
        lines.append(
            f"- {c}: FN {first['fn'] or '없음'} · 함정 오탐 {first['decoy_false_positives'] or '없음'} · 기타 오탐 {first['unmatched_findings'][:6] or '없음'}"
        )
        if first.get("errors"):
            lines.append(f"  - 오류: {first['errors'][:5]}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="vuln-python-app")
    ap.add_argument("--truth", default="code_python.yaml")
    ap.add_argument("--configs", default="S,SX,H")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rep = asyncio.run(run(a.target, a.truth, [c.strip() for c in a.configs.split(",") if c.strip()], a.repeat))
    md = render(rep, a.repeat)
    print(md)
    if a.out:
        a.out.write_text(md, encoding="utf-8")
    if a.json:
        a.json.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
