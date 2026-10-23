"""Assistant review of the 28 initial mappings, never human confirmation.

Pinned input snapshot; emits per-link decisions and applies conservative changes.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from core.compliance import Link, load_catalog
from core.compliance_cli import atomic_yaml
from core.regulation_sources import compact, evidence_candidate, sha256
from prepare_compliance_evidence import exact_span


def main():
    cfg = ROOT / "config/compliance"
    policy_path = ROOT / "knowledge/sources/evidence_policy.yaml"
    target = cfg / "links/generated.yaml"
    catalog = load_catalog()
    selected = [
        l
        for l in catalog.links
        if l.relevance == "ai_estimated"
        and evidence_candidate(catalog.clauses[l.clause])
    ]
    if len(selected) != 28:
        raise ValueError(
            "Expected untouched 28-link review snapshot; inspect existing review instead of reapplying"
        )
    policy = yaml.safe_load(policy_path.read_text())
    items = [Link.model_validate(i) for i in yaml.safe_load(target.read_text())]
    by_id = {l.identity: l for l in items}
    decisions = []
    candidates = []
    generic = {
        "INFO_EXPOSURE": "현재 규칙은 일반 스택트레이스 노출도 탐지하므로 AI 모델 정보 노출이라고 가정할 수 없음.",
        "SECRET_LEAK": "현재 규칙은 AWS 키·JWT·개인키도 탐지하므로 AI API 키 보관 항목과 일괄 연결할 수 없음.",
        "HARDCODED_SECRET": "비밀 변수의 고엔트로피 문자열만으로 API 키인지 AI 시스템 자격증명인지 확인할 수 없음.",
        "HARDCODED_CREDENTIAL": "현재 규칙은 일반 DB 비밀번호도 탐지하므로 AI API 키 전용 문구와 일괄 연결하지 않음.",
        "PROMPT_SECRET_EMBEDDED": "프롬프트 안의 임의 기밀 문자열이 API 키라는 전제가 확인되지 않음.",
        "AGENT_SHARED_SECRET": "코드와 에이전트에서 같은 환경변수 이름을 쓴다는 사실만으로 서비스별 키 분리 위반을 확정할 수 없음.",
        "TOOL_EXFIL_CHANNEL": "도구의 사전 도입 승인과 수신자 도메인 제한은 다른 통제이므로 직접 연결을 보류함.",
    }
    for link in selected:
        clause = catalog.clauses[link.clause]
        category = link.key.get("category", "")
        action = "keep"
        reason = "문제 유형과 원문 통제의 기술적 관련성을 확인. 적용 조건과 통제 효과는 별도 검토 필요."
        updated = link
        if category in generic or (
            category == "TOOL_MISUSE" and clause.id == "FSC-AI-GL:7-CHK-24"
        ):
            action = "hold"
            reason = generic.get(
                category,
                "도구 오남용이 항상 권한 과다를 의미하지 않아 최소 권한 항목과의 일괄 연결을 보류함.",
            )
        elif clause.id in {
            "FSC-AI-GL:7-CHK-05",
            "FSC-AI-GL:7-CHK-06",
            "FSC-AI-GL:7-CHK-07",
            "FSC-AI-GL:7-CHK-08",
        }:
            action = "revise"
            reason = "대상 설명만 인용하던 부분을 실제 방어·탐지·차단 조치까지 포함하도록 보완."
            quote = clause.text.split("YES")[0].strip()
            updated = link.model_copy(
                update={"quote": quote, "quote_sha256": sha256(quote)}
            )
        elif clause.id in {
            "FSC-AI-GL:7-CHK-13",
            "FSC-AI-GL:7-CHK-10",
            "FSC-AI-GL:7-CHK-29",
        }:
            action = "revise"
            reason = "정기 모의훈련·운영 검증은 조직 운영 증적이 필요하며 이번 단발 감사로 이행 여부를 확인할 수 없음."
            for entry in policy["classifications"]:
                if entry["clause"] == clause.id:
                    entry["evidence_scope"] = "organization"
                    if clause.id != "FSC-AI-GL:7-CHK-29":
                        entry["condition"] = "finance_high_risk"
        elif clause.id.endswith("EVIDENCE-TECH-5") and category == "TOOL_NO_APPROVAL":
            action = "revise"
            reason = "고객 영향도가 큰 금융 의사결정에 한정된 통제임을 명시하며 모든 도구 호출에 승인을 요구하지 않음."
            for entry in policy["excerpts"]:
                if entry["parent_id"] + ":EVIDENCE-" + entry["slug"] == clause.id:
                    entry["condition"] = "finance_customer_impact"
        elif (category == "TOOL_EXCESSIVE_SCOPE" and clause.source == "FSC-AI-SEC") or (
            category == "TOOL_MISUSE" and clause.source == "FSC-AI-SEC"
        ):
            action = "revise"
            parent = catalog.clauses["FSC-AI-SEC:PAGE-052-01"]
            if category == "TOOL_EXCESSIVE_SCOPE":
                phrase = "연결 사용 범위 및 권한 제한: AI 시스템이 외부 서비스와 연결할 때, 서비스별로 구체적인 사용 범위(특정 데이터, 기능)를 설정하여 연결 권한을 엄격히 제한하고 관리, 주기적 점검 및 재평가 수행"
                slug = "TOOL-SCOPE"
                reason = "개발자·운영자 역할 권한 문구 대신 외부 AI 서비스 연결 범위·권한 제한 문구로 교체."
            else:
                phrase = "오남용 가능성 평가: 외부 서비스 및 도구가 AI 시스템에 연결될 경우, 데이터 오남용 또는 의도하지 않은 행동의 가능성을 사전 평가하고 위험 완화 조치 시행"
                slug = "TOOL-MISUSE"
                reason = "사전 도입 승인 문구 대신 연결된 외부 도구의 오남용·의도하지 않은 행동에 대한 위험 완화 문구로 교체."
            quote = exact_span(parent.text, phrase)
            policy["excerpts"].append(
                {
                    "parent_id": parent.id,
                    "slug": slug,
                    "source_sha256": parent.source_sha256,
                    "text": quote,
                    "heading": phrase.split(":")[0],
                    "content_kind": "recommendation",
                    "evidence_scope": "finding",
                    "condition": "finance",
                }
            )
            updated = link.model_copy(
                update={
                    "clause": parent.id + ":EVIDENCE-" + slug,
                    "quote": quote,
                    "quote_sha256": sha256(quote),
                }
            )
        elif clause.evidence_scope == "organization":
            reason = (
                link.reason
                + " 코드 Findings 및 보고서 생성만으로 조직 의무 이행을 확인하지 않습니다."
            )
        elif clause.source == "KR-AIBA-HIGH-GL":
            reason = (
                link.reason
                + " 고영향 AI 적용 여부와 개입이 필요한 상황인지 확인해야 합니다."
            )
        elif category == "PROMPT_NO_INSTRUCTION_HIERARCHY":
            reason = "지시 계층 부재는 인젝션 방어 검토의 단서이며, 특정 프롬프트 문구 유무만으로 방어 전체의 이행을 판정하지 않습니다."
        elif category == "PRIVILEGE_ESCALATION":
            reason = "AI 에이전트의 권한 상승 시나리오와 최소 권한 통제의 관련성을 검토하는 후보. 일반 웹 권한 문제로 확대 적용하지 않습니다."
        note = {
            "action": action,
            "reviewer_kind": "assistant",
            "human_confirmed": False,
            "reason": reason,
        }
        if action == "hold":
            by_id[link.identity] = link.model_copy(
                update={
                    "relevance": "review_required",
                    "reason": reason,
                    "generated_by": {**link.generated_by, "assistant_review": note},
                }
            )
        else:
            updated = updated.model_copy(
                update={
                    "reason": reason,
                    "generated_by": {**updated.generated_by, "assistant_review": note},
                }
            )
            if updated.identity != link.identity:
                by_id[link.identity] = link.model_copy(
                    update={
                        "relevance": "review_required",
                        "reason": reason,
                        "generated_by": {
                            **link.generated_by,
                            "assistant_review": {**note, "action": "superseded"},
                        },
                    }
                )
            by_id[updated.identity] = updated
            candidates.append(
                {
                    "key": updated.key,
                    "source": clause.source,
                    "clause": updated.clause,
                    "quote": updated.quote,
                    "reason": reason,
                }
            )
        decisions.append(
            {
                "link_id": link.identity,
                "key": link.key,
                "before_clause": link.clause,
                "after_clause": updated.clause,
                "source_sha256": clause.source_sha256,
                "quote_verified": compact(link.quote) in compact(clause.text),
                "decision": action,
                "reason": reason,
                "scope_before": clause.evidence_scope,
            }
        )
    atomic_yaml(policy_path, policy)
    atomic_yaml(
        target,
        [l.model_dump() for l in sorted(by_id.values(), key=lambda l: l.identity)],
    )
    atomic_yaml(cfg / "initial_candidates.yaml", candidates)
    updated_catalog = load_catalog()
    summary = {
        "reviewer_kind": "assistant",
        "human_confirmed": False,
        "reviewed": len(decisions),
        "decisions": dict(Counter(d["decision"] for d in decisions)),
        "active_links": sum(
            l.relevance in {"ai_estimated", "human_confirmed"}
            and evidence_candidate(updated_catalog.clauses[l.clause])
            for l in updated_catalog.links
        ),
        "human_confirmed_links": sum(
            l.relevance == "human_confirmed" for l in updated_catalog.links
        ),
        "items": decisions,
    }
    (ROOT / "workHistory/COMPLIANCE_MAPPING_REVIEW_28.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    rows = [
        "# 28개 매핑 원문 대조 결과",
        "",
        "AI 검토 결과이며 사람 확인 상태로 변경하지 않았습니다.",
        "",
        "|유형|기존 조항|결정|이유|",
        "|---|---|---|---|",
    ]
    for d in decisions:
        rows.append(
            "|"
            + next(iter(d["key"].values()))
            + "|"
            + d["before_clause"]
            + "|"
            + d["decision"]
            + "|"
            + d["reason"]
            + "|"
        )
    (ROOT / "workHistory/COMPLIANCE_MAPPING_REVIEW_28.md").write_text(
        "\n".join(rows) + "\n"
    )
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "items"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
