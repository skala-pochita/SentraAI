"""임베딩. OPENAI_API_KEY 가 있으면 OpenAI(EMBEDDING_MODEL), 없으면 결정론적 해시 임베딩(오프라인용).

해시 임베딩은 토큰·문자 n-gram 을 1536 차원에 feature hashing 한 것으로, 의미 검색 품질은 낮지만
키워드 검색(tsvector)과 합쳐 하이브리드 검색이 오프라인에서도 동작하게 한다 (DECISIONS #4).
"""

from __future__ import annotations

import hashlib
import math
import re

from core.errors import AppError, ErrorCode
from core.settings import get_settings

DIM = 1536
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]+|\d+|[가-힣]+")


def provider() -> str:
    return "openai" if get_settings().openai_api_key else "hash"


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    feats = list(tokens)
    for t in tokens:  # 한국어 조사 변형에 덜 민감하도록 2-gram 문자 특징 추가
        if len(t) >= 2 and not t.isascii():
            feats.extend(t[i : i + 2] for i in range(len(t) - 1))
    for f in feats:
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
        vec[h % DIM] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if provider() == "hash":
        return [_hash_embed(t) for t in texts]
    from langchain_openai import OpenAIEmbeddings

    s = get_settings()
    client = OpenAIEmbeddings(model=s.embedding_model, request_timeout=30, max_retries=2)
    try:
        return await client.aembed_documents(texts)
    except Exception as e:  # noqa: BLE001 - SDK 예외를 구조화된 오류로 변환
        name = type(e).__name__
        code = (
            ErrorCode.RATE_LIMITED
            if "RateLimit" in name
            else (ErrorCode.UPSTREAM_TIMEOUT if "Timeout" in name else ErrorCode.UPSTREAM_ERROR)
        )
        raise AppError(code, f"embedding failed: {name}") from e
