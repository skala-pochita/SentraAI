"""fix_generate: 수정안 생성 → 서버 git apply --check → 패치된 파일에 해당 규칙 재실행해 해결 여부 사전 확인.

1) 알려진 카테고리는 결정론적 수정기(core/fixes.py), 2) 그 외·실패 시 LLM(있으면) 수정안.
재시도 시 이전 실패 사유를 프롬프트에 포함한다. 반려된 CRITICAL·위험 수용 건은 제외한다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError
from core.fixes import agent_edits, code_edits, probe_edits
from core.graph.deps import get_deps, node_error
from core.graph.nodes._base import node
from core.probes import load_probe_set
from core.schemas import FindingDraft, FixProposal


def tag(edits: list[dict[str, Any]], finding_id: str | None) -> list[dict[str, Any]]:
    """edit 에 finding_id 를 붙인다 — 서버가 edit 가 모두 실패한 Finding 만 수정안에서 뺄 수 있게."""
    return [{**e, "finding_id": finding_id} for e in edits]


def select_targets(state: dict[str, Any]) -> list[FindingDraft]:
    findings = [FindingDraft.model_validate(f) for f in state.get("correlated", []) if f.get("finding_id")]
    if state.get("retry_count"):
        unresolved = set((state.get("verify_result") or {}).get("unresolved", []))
        return [f for f in findings if f.finding_id in unresolved]
    approval = state.get("approval") or {}
    approved = approval.get("decision") == "APPROVE"
    rejected = approval.get("decision") == "REJECT"
    accepted = set(state.get("accepted_risks", []))
    # TRUE_POSITIVE 는 수정 대상. NEEDS_REVIEW 는 보안관리자가 승인한 CRITICAL 일 때만 (사람이 검토한 것으로 본다)
    return [
        f
        for f in findings
        if f.finding_id not in accepted
        and not (rejected and f.severity == "CRITICAL")
        and (f.verdict == "TRUE_POSITIVE" or (approved and f.severity == "CRITICAL" and f.verdict == "NEEDS_REVIEW"))
    ]


@node("fix_generate")
async def fix_generate(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    targets = select_targets(state)
    attempt = int(state.get("retry_count", 0)) + 1
    feedback = list(state.get("fix_feedback", []))
    errors: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    types = {t["target_id"]: t for t in state.get("targets", [])}
    domain = load_probe_set().allowed_email_domain

    # (target_id, kind) → edits, finding_ids
    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"edits": [], "finding_ids": []})
    file_cache: dict[tuple[str, str], str] = {}
    manifests: dict[str, dict[str, Any]] = {}
    no_fixer: list[str] = []
    probe_findings = [f for f in targets if (f.rule_id or "").startswith("PROBE-")]
    for f in targets:
        if f.source == "correlation" or f in probe_findings:
            continue
        t = types.get(f.target_id, {})
        try:
            if t.get("type") == "agent":
                if f.target_id not in manifests:
                    manifests[f.target_id] = await deps.call(
                        state, "fix_generate", "get_agent_manifest", target_id=f.target_id
                    )
                m = manifests[f.target_id]
                res = agent_edits(f, m["manifest_text_masked"], m["system_prompt_masked"], domain)
                if res and res[1]:
                    groups[(f.target_id, res[0])]["edits"].extend(tag(res[1], f.finding_id))
                    groups[(f.target_id, res[0])]["finding_ids"].append(f.finding_id)
                    continue
            else:
                ev = f.primary
                if ev and ev.file_path:
                    key = (f.target_id, ev.file_path)
                    if key not in file_cache:
                        r = await deps.call(
                            state, "fix_generate", "read_repo_file", target_id=f.target_id, path=ev.file_path
                        )
                        file_cache[key] = r["content_masked"]
                    edits = code_edits(f, file_cache[key])
                    if edits:
                        groups[(f.target_id, "code_patch")]["edits"].extend(tag(edits, f.finding_id))
                        groups[(f.target_id, "code_patch")]["finding_ids"].append(f.finding_id)
                        continue
        except AppError as e:
            errors.append(node_error("fix_generate", e))
        no_fixer.append(f.finding_id or f.temp_id)

    llm = deps.llm("fix_generate", state.get("eval_config"))
    if llm is not None and no_fixer:
        from core.graph.nodes.llm_fixer import llm_edits

        extra = await llm_edits(state, config, llm, [f for f in targets if f.finding_id in no_fixer], feedback, usage)
        for (tid, kind), g in extra.items():
            groups[(tid, kind)]["edits"].extend(g["edits"])
            groups[(tid, kind)]["finding_ids"].extend(g["finding_ids"])
            no_fixer = [x for x in no_fixer if x not in g["finding_ids"]]

    # probe 로 확인된 취약점은 같은 에이전트의 manifest/prompt 수정안에 묶는다 (재검증 시 probe 재실행)
    for pf in probe_findings:
        if pf.target_id not in manifests:
            try:
                manifests[pf.target_id] = await deps.call(
                    state, "fix_generate", "get_agent_manifest", target_id=pf.target_id
                )
            except AppError as e:
                errors.append(node_error("fix_generate", e))
        pe = probe_edits(pf, manifests[pf.target_id]["manifest_text_masked"]) if pf.target_id in manifests else None
        if pe:  # 결정론 수정이 있는 probe (예: 다른 고객 조회 → get_order 본인 한정 스코프)
            groups[(pf.target_id, "manifest_patch")]["edits"].extend(tag(pe, pf.finding_id))
            groups[(pf.target_id, "manifest_patch")]["finding_ids"].append(pf.finding_id)
            continue
        agent_keys = [k for k in groups if k[0] == pf.target_id]
        gkey = next((k for k in agent_keys if k[1] == "manifest_patch"), agent_keys[0] if agent_keys else None)
        if gkey:
            groups[gkey]["finding_ids"].append(pf.finding_id)
        else:
            no_fixer.append(pf.finding_id or pf.temp_id)

    proposals: list[dict[str, Any]] = []
    for (tid, kind), g in groups.items():
        if not g["edits"]:
            continue
        try:
            res = await deps.call(
                state,
                "fix_generate",
                "create_fix_proposal",
                audit_id=state["audit_id"],
                finding_ids=g["finding_ids"],
                kind=kind,
                target_id=tid,
                edits=g["edits"],
                attempt=attempt,
            )
        except AppError as e:
            errors.append(node_error("fix_generate", e))
            feedback.append(f"{kind}@{tid[:8]}: {e.message}")
            continue
        if not res["patch_check_passed"]:
            feedback.append(f"{kind}@{tid[:8]}: git apply --check 실패: {res.get('check_error')}")
            continue
        for sk in res.get("skipped_edits") or []:
            feedback.append(f"{kind}@{tid[:8]}: edit 건너뜀 {sk.get('path')} ({sk.get('error')})")
        if res["precheck_remaining"]:
            feedback.append(f"{kind}@{tid[:8]}: 패치 후에도 규칙 탐지 {res['precheck_remaining']}")
        proposals.append(
            FixProposal(
                proposal_id=res["proposal_id"],
                finding_ids=res.get("finding_ids") or g["finding_ids"],
                kind=kind,
                diff=res["diff_masked"],
                patch_check_passed=True,
                target_id=tid,
            ).model_dump()
        )

    # 결합 위험은 구성 Finding 이 모두 수정 대상일 때 함께 추적
    in_fix = {fid for p in proposals for fid in p["finding_ids"]}
    corr_ids = [
        f.finding_id
        for f in targets
        if f.source == "correlation" and f.component_ids and set(f.component_ids) <= in_fix
    ]
    await deps.event(
        state,
        "fix.generated",
        {
            "attempt": attempt,
            "proposals": len(proposals),
            "kinds": [p["kind"] for p in proposals],
            "no_automated_fix": len(no_fixer),
            "feedback": feedback[-3:],
        },
        node="fix_generate",
    )
    return {
        "fix_proposals": proposals,
        "fix_targets": sorted(in_fix | set(corr_ids)),
        "fix_feedback": feedback,
        "errors": errors,
        "token_usage": usage,
    }
