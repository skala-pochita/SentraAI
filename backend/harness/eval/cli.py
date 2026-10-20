"""python -m harness.eval.cli --configs A_nano_single,B_nano_harness --repeat 1"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from harness.eval.runner import run_eval


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="A_nano_single,B_nano_harness")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    res = asyncio.run(run_eval([c.strip() for c in a.configs.split(",") if c.strip()], a.repeat))
    print(res["report_md"])
    print(json.dumps({"eval_run_id": res["eval_run_id"]}))


if __name__ == "__main__":
    main()
