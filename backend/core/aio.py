"""asyncio 도우미."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def gather_or_cancel(*aws: Awaitable[T]) -> list[T]:
    """asyncio.gather 와 같지만, 하나가 실패(또는 바깥에서 취소)하면 나머지를 취소하고 **끝날 때까지 기다린 뒤**
    첫 예외를 그대로 올린다 (2026-11-06 리뷰 C).

    asyncio.gather 는 첫 예외를 바로 올리고 나머지는 계속 실행된 채 남긴다. 그러면 호출자가 이미 닫은
    자원(httpx 클라이언트, DB 세션)을 고아 task 가 계속 쓰게 된다. TaskGroup 과 달리 예외를 ExceptionGroup 으로
    감싸지 않으므로 `except AppError` 하는 기존 호출자 코드가 그대로 동작한다.
    """
    tasks = [asyncio.ensure_future(a) for a in aws]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
