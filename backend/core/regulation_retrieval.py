"""Conservative discovery helpers; lexical overlap never proves relevance."""

from __future__ import annotations

import re

from core.regulation_sources import Clause, evidence_candidate

# Only known security concepts are expanded, never arbitrary legal obligations.
CONCEPTS = (
    (("tool_no_approval", "human approval", "human oversight"), "사람 승인 감독 외부 도구"),
    (("tool_excessive_scope", "least privilege", "excessive permissions"), "최소 권한 접근 통제"),
    (("tool_exfil_channel", "data exfiltration", "external api", "mcp"), "외부 도구 승인 데이터 유출"),
    (("prompt injection", "indirect_injection", "jailbreak"), "프롬프트 인젝션 탈옥 외부 입력"),
    (("pii", "personal data", "data_exfiltration"), "개인정보 민감정보 마스킹 유출"),
    (("system_prompt_leak", "system prompt leak"), "시스템 프롬프트 내부 정보 노출"),
    (("hardcoded_secret", "secret_leak", "api key"), "API 키 시크릿 하드코딩"),
    (("supply chain", "model integrity"), "공급망 모델 무결성 검증"),
    (("rag_acl", "tenant access control"), "데이터 접근 권한 통제"),
)
STOP_WORDS = {"ai", "llm", "the", "and", "for", "with", "to", "of", "is", "a", "an", "인공지능", "대한", "관련"}


def expand_query(query: str) -> str:
    lower = query.lower()
    extra = [words for aliases, words in CONCEPTS if any(alias in lower for alias in aliases)]
    return " ".join([query, *extra])


def lexical_support(query: str, clause: Clause) -> float:
    tokens = {t for t in re.findall(r"[a-z0-9_]+|[가-힣]{2,}", query.lower()) if t not in STOP_WORDS and len(t) > 1}
    body = clause.text.lower()
    return sum(t in body for t in tokens) / max(1, len(tokens))


def retrieval_candidate(query: str, clause: Clause) -> bool:
    return evidence_candidate(clause) and lexical_support(query, clause) > 0
