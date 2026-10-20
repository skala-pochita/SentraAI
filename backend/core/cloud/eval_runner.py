"""Cloud 평가 실행기 (기획서 10~11장): 묶음 × 구성(A/B) × 반복을 새 taskId 로 실행하고 채점한다.

- 구성(A/B)·모델은 config/models.yaml 의 eval_configs (환경변수로 모델 고정). 두 구성에 같은 예산·도구·마스킹·스키마.
- 모의 저장소(MemoryBackend)는 서버 측에서 고정한다. 모델은 평가 모드/저장소를 고를 수 없다.
- LLM 키/모델이 없으면 A/B 는 실행하지 않고 "미측정(skipped)" 으로 기록한다. 모의 출력으로 대체하지 않는다.
- `rules` 구성은 LLM 없는 결정론 점검(core.cloud.checks)으로, 정답·채점기 자체 점검과 참고용 기준선이다 (A/B 아님).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.cloud.backend_mem import GUIDANCE_URI, MemoryBackend
from core.cloud.budget import CloudBudget
from core.cloud.bundle import parse_bundle
from core.cloud.llm import StageRunner
from core.cloud.pipeline import CloudCtx, run_cloud_audit
from core.cloud.scoring import BundleScore, aggregate, load_input, score_run
from core.cloud.tools import ToolHost
from core.settings import get_settings, load_config
from harness.llm import llm_available, model_name_for

FINAL = ["T01", "T02", "T03", "T04", "T05", "T06", "T07", "T08"]
DEV = ["D01", "D02", "D03"]
ATTACK = ["T01A", "T06A"]
REVISION = ["T01R", "T06R"]
CONFIGS = {"A": "A_nano_single", "B": "B_nano_harness"}


def make_runner(config_key: str, budget: CloudBudget) -> tuple[StageRunner | None, str]:
    """config_key: 'A' | 'B' | 'rules'. 반환 (러너 또는 None, 모델명)."""
    if config_key == "rules":
        return None, "rules"
    if not llm_available():
        return None, ""
    name = model_name_for("cloud", CONFIGS[config_key])
    if not name:
        return None, ""
    cfg = load_config("models")["defaults"]
    kwargs: dict[str, Any] = {"model": name, "timeout": float(cfg.get("timeout_s", 60)), "max_retries": 0}
    from langchain_openai import ChatOpenAI

    max_out = load_config("budget").get("cloud_audit", {}).get("max_output_tokens")
    if max_out:
        kwargs["max_tokens"] = int(max_out)
    if not name.startswith(("gpt-5", "o")) and cfg.get("temperature") is not None:
        kwargs["temperature"] = float(cfg["temperature"])
    return StageRunner(ChatOpenAI(**kwargs), name, budget), name


def build_ctx(bid: str, runner_factory: Any, config_key: str) -> tuple[CloudCtx, str]:
    bundle = parse_bundle(load_input(bid))
    budget = CloudBudget.from_config()
    runner, model = runner_factory(config_key, budget)
    task_id = f"eval-{config_key}-{bid}-{uuid.uuid4().hex[:8]}"
    uris = {GUIDANCE_URI}
    ctx = CloudCtx(
        task_id=task_id,
        bundle=bundle,
        host=None,  # type: ignore[arg-type]
        budget=budget,
        runner=runner,
        allowed_uris=sorted(uris),
        enable_action=False,  # 정확도 평가에서는 후속 조치 없이 종료 (사람 승인 시간 제거)
    )
    ctx.host = ToolHost(
        MemoryBackend(bundle), budget, task_id, allowed_resource_uris=uris, on_event=lambda t, d: ctx.emit(t, d)
    )
    return ctx, model


async def run_one(bid: str, config_key: str, rep: int, runner_factory: Any = make_runner) -> dict[str, Any]:
    ctx, model = build_ctx(bid, runner_factory, config_key)
    if config_key in CONFIGS and ctx.runner is None:
        return {
            "bundle_id": bid,
            "config": config_key,
            "rep": rep,
            "skipped": "LLM 미설정(OPENAI_API_KEY/모델)",
            "status": "SKIPPED",
        }
    started = time.monotonic()
    kind = "A" if config_key == "A" else "B"
    res = await run_cloud_audit(ctx, kind)  # type: ignore[arg-type]
    if (res.get("failure") or {}).get("code") == "UNAUTHORIZED":  # 키가 거부되면 성능이 아니라 환경 문제다
        return {
            "bundle_id": bid,
            "config": config_key,
            "rep": rep,
            "skipped": "LLM 인증 실패(401): 키 확인 필요",
            "status": "SKIPPED",
        }
    res.update({"config": config_key, "rep": rep, "model": model, "wall_ms": int((time.monotonic() - started) * 1000)})
    return res


async def run_eval(
    bundles: list[str],
    configs: list[str],
    reps: int = 1,
    concurrency: int = 2,
    runner_factory: Any = make_runner,
    reviews: dict[str, str] | None = None,
) -> dict[str, Any]:
    """동일 병렬도를 모든 구성에 적용한다. 일시 오류(UPSTREAM_*/RATE_LIMITED)만 한 번 재실행하고 원 기록은 보존한다."""
    sem = asyncio.Semaphore(concurrency)
    jobs = [(b, c, r) for r in range(1, reps + 1) for b in bundles for c in configs]
    runs: list[dict[str, Any]] = []

    async def go(b: str, c: str, r: int) -> None:
        async with sem:
            res = await run_one(b, c, r, runner_factory)
            if (res.get("failure") or {}).get("code") in ("UPSTREAM_ERROR", "UPSTREAM_TIMEOUT", "RATE_LIMITED"):
                res["superseded"] = True
                runs.append(res)
                res = await run_one(b, c, r, runner_factory)
                res["retry_of_transient_failure"] = True
            runs.append(res)

    await asyncio.gather(*(go(*j) for j in jobs))
    return finalize_eval(runs, bundles, configs, reps, concurrency, reviews)


def finalize_eval(
    runs: list[dict[str, Any]],
    bundles: list[str],
    configs: list[str],
    reps: int,
    concurrency: int,
    reviews: dict[str, str] | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    detail: list[dict[str, Any]] = []
    for c in configs:
        scored: list[BundleScore] = []
        for r in runs:
            if r["config"] != c or r.get("superseded") or r.get("status") == "SKIPPED":
                continue
            sc = score_run(r, reviews)
            scored.append(sc)
            detail.append({"config": c, "rep": r["rep"], "bundle": r["bundle_id"], "score": sc.to_dict()})
        skipped = [r for r in runs if r["config"] == c and r.get("status") == "SKIPPED"]
        summary[c] = {
            "executed": len(scored),
            "skipped": len(skipped),
            "metrics": aggregate(scored) if scored else None,  # None = 미측정
            "skip_reason": skipped[0]["skipped"] if skipped else None,
        }
    return {
        "eval_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%S"),
        "dataset_version": (get_settings().datasets_dir / "cloud" / "VERSION").read_text().strip(),
        "bundles": bundles,
        "configs": configs,
        "repetitions": reps,
        "concurrency": concurrency,
        "budget": load_config("budget").get("cloud_audit"),
        "models": sorted({r.get("model", "") for r in runs if r.get("model")}),
        "summary": summary,
        "detail": detail,
        "superseded_runs": [f"{r['config']}:{r['bundle_id']}:{r['rep']}" for r in runs if r.get("superseded")],
        "note": "소규모 탐색적 평가다. 통계적 우열이나 일반 성능을 주장하지 않는다. 미실행 지표는 미측정.",
    }


def render_markdown(ev: dict[str, Any]) -> str:
    rows = [
        "precision",
        "recall",
        "f1",
        "path_detection_rate",
        "path_precision",
        "evidence_accuracy",
        "hold_accuracy",
        "reverify_accuracy",
        "injection_failure_rate",
        "completion_rate_normal",
        "completion_rate_attack",
        "usd_per_bundle",
        "active_s_per_bundle",
        "needs_review",
    ]
    cfgs = ev["configs"]
    lines = [
        f"# Cloud 감사 평가 {ev['eval_id']}",
        "",
        f"- 데이터셋 {ev['dataset_version']} · 반복 {ev['repetitions']} · 병렬도 {ev['concurrency']}",
        f"- 모델: {', '.join(ev['models']) or '(미실행)'}",
        f"- {ev['note']}",
        "",
        "| 지표 | " + " | ".join(cfgs) + " |",
        "|---|" + "---|" * len(cfgs),
    ]
    for k in rows:
        vals = []
        for c in cfgs:
            m = ev["summary"][c]["metrics"]
            vals.append("미측정" if m is None else ("해당 없음" if m.get(k) is None else str(m[k])))
        lines.append(f"| {k} | " + " | ".join(vals) + " |")
    lines += [
        "",
        "실행 수: "
        + ", ".join(f"{c}={ev['summary'][c]['executed']}(스킵 {ev['summary'][c]['skipped']})" for c in cfgs),
    ]
    return "\n".join(lines) + "\n"


def save_eval(ev: dict[str, Any]) -> Path:
    out = get_settings().reports_dir
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"cloud_eval_{ev['eval_id']}.json"
    path.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"cloud_eval_{ev['eval_id']}.md").write_text(render_markdown(ev), encoding="utf-8")
    return path


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Cloud 감사 평가 (A/B/rules)")
    ap.add_argument("--set", choices=["dev", "final", "attack", "revision", "all"], default="final")
    ap.add_argument("--configs", default="A,B")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=2)
    a = ap.parse_args()
    sets = {"dev": DEV, "final": FINAL, "attack": ATTACK, "revision": REVISION, "all": [*FINAL, *ATTACK, *REVISION]}
    ev = asyncio.run(run_eval(sets[a.set], a.configs.split(","), a.reps, a.concurrency))
    path = save_eval(ev)
    print(render_markdown(ev))
    print(f"saved {path}")


if __name__ == "__main__":
    main()
