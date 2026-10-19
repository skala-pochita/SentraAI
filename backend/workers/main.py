"""워커 엔트리포인트: python -m workers.main --role audit|eval|retry."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

log = logging.getLogger("worker")

HEARTBEAT_DIR = Path("/tmp")  # noqa: S108 - 컨테이너 healthcheck 용


def heartbeat(role: str) -> None:
    (HEARTBEAT_DIR / f"worker-{role}.heartbeat").write_text(str(time.time()))


async def run(role: str) -> None:
    log.info("worker started role=%s", role)
    while True:
        heartbeat(role)
        await asyncio.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["audit", "eval", "retry"], required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(args.role))


if __name__ == "__main__":
    main()
