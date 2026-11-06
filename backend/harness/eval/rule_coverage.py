"""규칙 fixture 탐지율 측정 (개선 전후 비교용).

  cd backend && uv run python -m harness.eval.rule_coverage                       # 현재 규칙
  RULES_DIR=/path/to/old/rules uv run python -m harness.eval.rule_coverage        # 이전 규칙 (git archive 로 꺼낸 것)

fixture 마다 expected.yaml 의 양성 위치(file, line) 중 몇 개를 탐지했는지(위치 기준, 규칙 무관),
양성이 아닌 위치를 몇 개 탐지했는지(오탐)를 출력한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from core.rules.engine import scan_repo
from core.settings import REPO_ROOT, get_settings


def coverage(root: Path) -> dict[str, Any]:
    exp = yaml.safe_load((root / "expected.yaml").read_text(encoding="utf-8"))["expected"]
    positives = {(e["file"], int(e["line"])) for e in exp}
    hits = {(h.path, h.line) for h in scan_repo(root) if not h.path.endswith((".md", "expected.yaml"))}
    detected = positives & hits
    return {
        "fixture": root.name,
        "positives": len(positives),
        "detected": len(detected),
        "recall": round(len(detected) / len(positives), 4) if positives else None,
        "false_positives": len(hits - positives),
        "missed": sorted(f"{f}:{ln}" for f, ln in positives - hits),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", nargs="*")
    a = ap.parse_args()
    base = REPO_ROOT / "datasets" / "rule_fixtures"
    roots = [base / n for n in a.fixtures] if a.fixtures else sorted(p for p in base.iterdir() if (p / "expected.yaml").is_file())
    out = {"rules_dir": str(get_settings().rules_dir), "results": [coverage(r) for r in roots]}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
