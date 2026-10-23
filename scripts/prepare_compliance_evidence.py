"""Rebuild a pinned, conservative evidence policy from locally acquired originals.

Run with the backend virtualenv Python from the repository root. No API calls.
Never promote these assistant-reviewed candidates to human_confirmed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from core.compliance_cli import atomic_yaml, bootstrap_links
from core.regulation_sources import compact, load_clauses


def exact_span(text: str, phrase: str) -> str:
    positions = [i for i, c in enumerate(text) if not c.isspace()]
    offset = compact(text).find(compact(phrase))
    if offset < 0:
        raise ValueError("Required source passage missing")
    return text[positions[offset] : positions[offset + len(compact(phrase)) - 1] + 1]


def main() -> None:
    source_root = ROOT / "knowledge/sources"
    if (source_root / "evidence_policy.yaml").exists():
        # Preserve the subsequently reviewed excerpts, conditions and deferrals.
        # A source revision must be reviewed explicitly rather than auto-overwriting policy.
        load_clauses(source_root)
        print(json.dumps(bootstrap_links(source_root), ensure_ascii=False))
        return
    clauses, _ = load_clauses(source_root, include_policy=False)
    config = ROOT / "config/compliance"
    initial = yaml.safe_load((config / "initial_candidates.yaml").read_text())
    excerpts = []
    candidates = []
    decisions = []
    classifications = []
    for item in initial:
        if item["source"] == "FSC-AI-GL":
            item["reason"] = (
                "원문의 실제 점검 항목과 문제 유형을 대조한 관련성 후보. 통제 효과와 법적 적용은 별도 확인."
            )
            candidates.append(item)
            clause = clauses[item["clause"]]
            classifications.append(
                {
                    "clause": clause.id,
                    "source_sha256": clause.source_sha256,
                    "evidence_scope": "audit" if "audit" in item["key"] else "finding",
                }
            )
            decisions.append(
                {
                    "key": item["key"],
                    "action": "retain_candidate",
                    "reason": "원문의 실제 점검 항목",
                }
            )
            continue
        if item["source"] != "FSC-AI-SEC":
            continue
        if item["key"].get("category") == "PROMPT_NO_INSTRUCTION_HIERARCHY":
            decisions.append(
                {
                    "key": item["key"],
                    "action": "withhold",
                    "reason": "프롬프트 버전 관리는 지시와 데이터 분리를 직접 보장하지 않음",
                }
            )
            continue
        parents = [
            c
            for c in clauses.values()
            if c.source == item["source"]
            and c.parent_id is None
            and compact(item["quote"]) in compact(c.text)
        ]
        if len(parents) != 1:
            raise ValueError("Ambiguous source excerpt")
        parent = parents[0]
        phrase = item["quote"]
        if phrase.startswith("외부 도구 사전 승인"):
            phrase = "외부 도구 사전 승인 절차: AI 시스템이 외부 도구(API, MCP 등)와 연계될 때, 보안성 검증 및 위험성 평가를 거쳐 사전에 승인된 도구만 사용하도록 관리"
        text = exact_span(parent.text, phrase)
        slug = "TECH-" + str(len(excerpts) + 1)
        excerpt = {
            "parent_id": parent.id,
            "slug": slug,
            "source_sha256": parent.source_sha256,
            "text": text,
            "heading": phrase.replace("\n", " ")[:80],
            "content_kind": "recommendation",
            "evidence_scope": "finding",
            "condition": "finance",
        }
        # Reuse one physical excerpt for the same passage, across several categories.
        existing = next(
            (e for e in excerpts if e["parent_id"] == parent.id and e["text"] == text),
            None,
        )
        if existing:
            excerpt = existing
        else:
            excerpts.append(excerpt)
        cid = parent.id + ":EVIDENCE-" + excerpt["slug"]
        candidates.append(
            {
                "key": item["key"],
                "source": item["source"],
                "clause": cid,
                "quote": text,
                "reason": "기술 통제 문구를 원문에서 구간 대조한 관련성 후보. 적용 조건과 구현 효과는 추가 검토 필요.",
            }
        )
        decisions.append(
            {"key": item["key"], "action": "replace_page_with_excerpt", "clause": cid}
        )

    additions = [
        (
            "KR-AIBA:34",
            "HUMAN-OVERSIGHT",
            "고영향 인공지능에 대한 사람의 관리ㆍ감독",
            "requirement",
            "organization",
            {"category": "TOOL_NO_APPROVAL"},
            "사람 승인 누락은 감독 체계 검토의 단서이며, 특정 승인 절차의 법정 의무나 감독 체계 전체의 이행 여부를 확정하지 않습니다.",
        ),
        (
            "KR-AIBA:34",
            "DOCUMENTATION",
            "안전성ㆍ신뢰성 확보를 위한 조치의 내용을 확인할 수 있는 문서의 작성과 보관",
            "requirement",
            "organization",
            {"audit": "report_document"},
            "보고서는 증적 자료의 일부가 될 수 있으나 전체 조치 이행과 보관 의무를 입증하지 않습니다.",
        ),
        (
            "KR-AIBA-DECREE:27",
            "RETENTION",
            "인공지능사업자는 법 제34조제1항 각 호의 조치를 이행하고 그 근거를 문서로 5년간 보관(전자적 방법을 통한 보관을 포함한다)해야 한다.",
            "requirement",
            "organization",
            {"audit": "report_document"},
            "보고서 작성은 근거 보관 체계 검토의 시작점이며, 5년 보관과 전체 조치 이행은 별도 조직 증적이 필요합니다.",
        ),
        (
            "KR-AIBA-HIGH-GL:PAGE-055-01",
            "HUMAN-INTERVENTION",
            "인공지능이 특정 조건이나 임계값을 초과할 경우, 자동으로 경고를 발생시키고 사람의 검토 및 승인을 유도하는 제어 절차를 포함할 수 있음.",
            "recommendation",
            "finding",
            {"category": "TOOL_NO_APPROVAL"},
            "고영향 AI에서 사람의 개입을 지원하는 제어 절차의 구현 예에 해당합니다. 모든 도구 호출에 같은 승인 방식을 요구한다는 의미는 아닙니다.",
        ),
    ]
    for parent_id, slug, phrase, kind, scope, key, reason in additions:
        parent = clauses[parent_id]
        text = exact_span(parent.text, phrase)
        excerpts.append(
            {
                "parent_id": parent_id,
                "slug": slug,
                "source_sha256": parent.source_sha256,
                "text": text,
                "heading": text.replace("\n", " ")[:80],
                "content_kind": kind,
                "evidence_scope": scope,
                "condition": "high_impact_ai",
            }
        )
        candidates.append(
            {
                "key": key,
                "source": parent.source,
                "clause": parent_id + ":EVIDENCE-" + slug,
                "quote": text,
                "reason": reason,
            }
        )
    atomic_yaml(
        source_root / "evidence_policy.yaml",
        {
            "schema": 1,
            "review": "assistant_source_review; not human confirmation",
            "classifications": list({c["clause"]: c for c in classifications}.values()),
            "excerpts": excerpts,
        },
    )
    atomic_yaml(config / "initial_candidates.yaml", candidates)
    result = bootstrap_links(source_root)
    (ROOT / "workHistory/COMPLIANCE_MAPPING_REVIEW.json").write_text(
        json.dumps(
            {
                "reviewer_kind": "assistant",
                "human_confirmed": False,
                "actions": decisions,
                "new_excerpt_count": len(excerpts),
                "legacy_25_review": {
                    "retain_checklist_candidate": 15,
                    "replace_page_with_excerpt": 9,
                    "withhold_prompt_version_vs_instruction_separation": 1,
                },
                "bootstrap": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
