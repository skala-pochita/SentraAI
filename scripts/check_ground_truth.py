"""정답셋 위치 검증 (Phase 2 완료 조건).

datasets/ground_truth/code.yaml 의 모든 항목(findings, decoys, auditor_injections)에 대해
targets/<target>/<file> 의 <line> 번째 줄에 match 문자열이 실제로 있는지 확인한다.

  python scripts/check_ground_truth.py          # 검증만 (불일치 시 exit 1)
  python scripts/check_ground_truth.py --fill   # match 가 유일하게 나오는 라인으로 line 갱신
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GT = ROOT / "datasets" / "ground_truth" / "code.yaml"
SECTIONS = ("findings", "decoys", "auditor_injections")


def check(fill: bool) -> list[str]:
    data = yaml.safe_load(GT.read_text(encoding="utf-8"))
    base = ROOT / "targets" / data["target"]
    errors: list[str] = []
    text = GT.read_text(encoding="utf-8")
    for section in SECTIONS:
        for item in data.get(section, []):
            path = base / item["file"]
            if not path.is_file():
                errors.append(f"{item['id']}: file not found {item['file']}")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            line = int(item["line"])
            if 1 <= line <= len(lines) and item["match"] in lines[line - 1]:
                continue
            hits = [i + 1 for i, s in enumerate(lines) if item["match"] in s]
            if fill and len(hits) == 1:
                # id 블록 안의 line: 값만 치환
                pat = re.compile(rf"(- id: {item['id']}\n(?:    .*\n)*?    line: )\d+")
                text = pat.sub(rf"\g<1>{hits[0]}", text, count=1)
                print(f"{item['id']}: line {line} -> {hits[0]}")
            else:
                errors.append(f"{item['id']}: '{item['match']}' not at {item['file']}:{line} (found at {hits})")
    if fill:
        GT.write_text(text, encoding="utf-8")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill", action="store_true")
    errors = check(ap.parse_args().fill)
    for e in errors:
        print("ERROR", e)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
