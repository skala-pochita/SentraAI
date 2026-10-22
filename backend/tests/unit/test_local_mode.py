"""CLI 로컬 모드 요청 검사: 이 컴퓨터의 브라우저·CLI 만 허용."""

from __future__ import annotations

import pytest

from api.local_mode import reject_reason


@pytest.mark.parametrize(
    ("method", "headers"),
    [
        ("GET", {"host": "localhost:5180"}),
        ("GET", {"host": "api:8000", "x-forwarded-host": "127.0.0.1:5180"}),
        ("GET", {"host": "[::1]:5180", "origin": "http://localhost:5180", "sec-fetch-site": "same-origin"}),
        ("POST", {"host": "localhost:5180", "origin": "http://localhost:5180", "x-sentra-local": "1"}),
        ("POST", {"host": "127.0.0.1:5180", "x-sentra-local": "1"}),  # CLI (Origin 없음)
    ],
)
def test_allows_local_requests(method: str, headers: dict[str, str]) -> None:
    assert reject_reason(method, headers) is None


@pytest.mark.parametrize(
    ("method", "headers", "reason"),
    [
        # DNS 리바인딩: 악성 도메인이 127.0.0.1 로 풀려도 Host 가 다르다
        ("GET", {"host": "evil.example:5180"}, "host"),
        ("GET", {"host": "api:8000", "x-forwarded-host": "evil.example"}, "host"),
        ("GET", {"host": "192.168.0.10:5180"}, "host"),
        # 다른 웹페이지가 보낸 요청
        ("GET", {"host": "localhost:5180", "origin": "https://evil.example"}, "origin"),
        ("GET", {"host": "localhost:5180", "origin": "null"}, "origin"),
        ("GET", {"host": "localhost:5180", "sec-fetch-site": "cross-site"}, "cross-site"),
        # 상태 변경은 전용 헤더 필요 (단순 form POST 차단)
        ("POST", {"host": "localhost:5180"}, "header"),
        ("DELETE", {"host": "localhost:5180", "x-sentra-local": "true"}, "header"),
    ],
)
def test_rejects_foreign_requests(method: str, headers: dict[str, str], reason: str) -> None:
    got = reject_reason(method, headers)
    assert got is not None and reason in got
