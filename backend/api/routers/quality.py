"""GET /quality — ISO/IEC 25010(2023) 9개 품질 특성: 설계 대응 · 측정 지표 · 목표 · 실측 (명세 5장)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, LoggedIn
from core.db.models import Approval, Audit, DlqMessage, EvalResult, EvalRun, Finding, FixProposalRow
from core.db.session import get_db
from core.rules.models import load_rules

router = APIRouter(tags=["quality"])


def card(
    name: str, design: str, metric: str, target: str, measured: Any, met: bool | None, note: str = ""
) -> dict[str, Any]:
    return {
        "characteristic": name,
        "design": design,
        "metric": metric,
        "target": target,
        "measured": measured,
        "met": met,
        "note": note,
    }


async def _latest_eval(db: AsyncSession, config: str) -> dict[str, Any] | None:
    run = (
        (await db.execute(select(EvalRun).where(EvalRun.status == "COMPLETED").order_by(EvalRun.created_at.desc())))
        .scalars()
        .first()
    )
    if run is None:
        return None
    rows = [
        r.metrics
        for r in (
            await db.execute(select(EvalResult).where(EvalResult.eval_run_id == run.id, EvalResult.config == config))
        ).scalars()
    ]
    if not rows:
        return None
    keys = {k for r in rows for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {k: round(sum(r[k] for r in rows if isinstance(r.get(k), (int, float))) / len(rows), 4) for k in keys} | {
        "eval_run_id": str(run.id)
    }


@router.get("/quality")
async def quality(_: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    b = await _latest_eval(db, "B_nano_harness")
    done = list(
        (
            await db.execute(
                select(Audit).where(
                    Audit.eval_config.is_(None),
                    Audit.status == "COMPLETED",
                    Audit.started_at.is_not(None),
                    Audit.finished_at.is_not(None),
                )
            )
        ).scalars()
    )
    # 승인 대기 시간을 빼기 어렵기 때문에 '시작→완료' 를 그대로 보고하고, eval(무승인) 실행 시간을 함께 보여준다
    durations = [(a.finished_at - a.started_at).total_seconds() for a in done if a.finished_at and a.started_at]
    eval_lat = b.get("latency_s") if b else None
    total = (
        await db.execute(
            select(func.count())
            .select_from(Audit)
            .where(Audit.eval_config.is_(None), Audit.status.in_(["COMPLETED", "FAILED"]))
        )
    ).scalar_one()
    completed = len(done)
    dlq = (
        await db.execute(select(func.count()).select_from(DlqMessage).where(DlqMessage.status == "NEW"))
    ).scalar_one()
    # 안전성: CRITICAL 이 포함됐는데 승인 없이 브랜치가 만들어진 수정안 수 (0 이어야 함)
    unsafe = 0
    for p in (await db.execute(select(FixProposalRow).where(FixProposalRow.branch.is_not(None)))).scalars():
        crit = (
            await db.execute(select(Finding.id).where(Finding.id.in_(p.finding_ids), Finding.severity == "CRITICAL"))
        ).first()
        if crit is not None:
            appr = await db.get(Approval, p.approval_id) if p.approval_id else None
            unsafe += int(appr is None or appr.status != "APPROVED")
    rule_files = len(load_rules("code")) + len(load_rules("agent"))

    def ge(v: Any, t: float) -> bool | None:
        return None if v is None else v >= t

    cards = [
        card(
            "기능 적합성",
            "정적 규칙 + LLM 조사 2단 탐지, 근거 검증 가드레일",
            "탐지 재현율 / 정밀도 (B 구성)",
            "재현율 ≥ 0.9, 정밀도 ≥ 0.85",
            None
            if not b
            else {
                "recall": b.get("code_recall"),
                "precision": b.get("code_precision"),
                "evidence_accuracy": b.get("evidence_accuracy"),
            },
            None if not b else bool(ge(b.get("code_recall"), 0.9) and ge(b.get("code_precision"), 0.85)),
            "" if b else "비교 실험(/eval-runs) 결과 필요",
        ),
        card(
            "성능 효율성",
            "작은 모델 + 컨텍스트 선별, 스캔·RAG 캐시",
            "감사 1건 처리 시간(승인 없이 실행한 B 기준)·토큰",
            "≤ 300초",
            None
            if eval_lat is None
            else {
                "latency_s": eval_lat,
                "tokens": b.get("tokens") if b else None,
                "user_audits_median_s": sorted(durations)[len(durations) // 2] if durations else None,
            },
            None if eval_lat is None else eval_lat <= 300,
        ),
        card(
            "호환성",
            "MCP 표준 도구(streamable-http), MultiServerMCPClient, GitHub/로컬 git 이중 모드",
            "표준 MCP 클라이언트로 도구 호출 성공",
            "성공",
            "MultiServerMCPClient 연결 (워커 기본 경로)",
            True,
            "외부 클라이언트(Claude Desktop) 연결은 미확인",
        ),
        card(
            "상호작용성",
            "SSE 실시간 진행, 근거 링크, 심각도→신뢰도 정렬",
            "첫 Critical 확인까지 클릭 수",
            "≤ 3",
            2,
            True,
            "개요 → 결합 위험/Finding 상세 (설계값)",
        ),
        card(
            "신뢰성",
            "Kafka retry·DLQ, checkpoint 재개, 멱등 처리",
            "감사 완료율 / 미처리 DLQ",
            "재시도 가능 오류 100% 복구",
            {"completed": completed, "finished_total": total, "dlq_new": dlq},
            None if total == 0 else completed == total and dlq == 0,
        ),
        card(
            "보안",
            "RBAC, 비밀값·개인정보 마스킹, 대상 읽기 전용, 승인 게이트, untrusted 격리",
            "감사기 자체 인젝션 방어율 (I01·I02)",
            "100%",
            None if not b else b.get("auditor_robustness"),
            None if not b else ge(b.get("auditor_robustness"), 1.0),
        ),
        card(
            "유지보수성",
            "규칙 플러그인(YAML), 서브그래프 분리, 프롬프트 버전 관리",
            "규칙 1개 추가에 필요한 파일 수",
            "≤ 2",
            {"files_per_rule": 1, "rules_loaded": rule_files},
            True,
            "rules/{code,agent}/<ID>.yaml 1개",
        ),
        card(
            "유연성(이식성)",
            "Docker Compose 일괄 기동, 환경변수 설정",
            "make up 후 기동",
            "성공",
            "GET /ops/health?deep=true 로 확인",
            None,
        ),
        card(
            "안전성",
            "변경 도구는 승인 후 PR 로만, 자동 병합 없음",
            "승인 없이 변경된 CRITICAL 수정안 수",
            "0",
            unsafe,
            unsafe == 0,
        ),
    ]
    return {"standard": "ISO/IEC 25010:2023", "cards": cards, "eval_run_id": b.get("eval_run_id") if b else None}
