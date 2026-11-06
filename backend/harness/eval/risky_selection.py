"""위험 파일 선택률 비교: 이전 방식(파일 이름) vs 내용 신호 (S2·PY4).

  cd backend && uv run python -m harness.eval.risky_selection

대상별로 '취약 코드 파일'(정답셋·fixture expected 의 코드 파일) 중 위험 파일 상위 K 개에 들어간 비율을 잰다.
데모 앱은 정적 규칙을 피하는 C19·C20 파일만 본다 (정적 후보가 없어 위험 파일로 뽑혀야만 조사된다).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from core.graph.nodes.intake import risky_files
from core.risk_signals import CODE_EXT, rank_files
from core.rules.engine import iter_repo_files, read_text
from core.settings import REPO_ROOT, load_config


def _targets() -> list[tuple[str, Path, set[str]]]:
    out = []
    gt = yaml.safe_load((REPO_ROOT / "datasets/ground_truth/code.yaml").read_text(encoding="utf-8"))
    evasion = {f["file"] for f in gt["findings"] if f["id"] in ("C19", "C20")}
    out.append(("vuln-spring-app (정적 회피 C19·C20)", REPO_ROOT / "targets" / gt["target"], evasion))
    for fx in sorted((REPO_ROOT / "datasets/rule_fixtures").iterdir()):
        if (fx / "expected.yaml").is_file():
            exp = yaml.safe_load((fx / "expected.yaml").read_text(encoding="utf-8"))["expected"]
            out.append((f"fixture/{fx.name}", fx, {e["file"] for e in exp if e["file"].endswith(CODE_EXT)}))
    return out


def measure(top_k: int = 8) -> list[dict[str, Any]]:
    kw = load_config("budget").get("react", {}).get("risky_name_keywords", [])
    rows = []
    for name, root, vuln in _targets():
        files = [(rel, read_text(p) or "") for rel, p in iter_repo_files(root)]
        old = set(risky_files([{"path": r} for r, _ in files], kw, top_k))
        new = {r.path for r in rank_files(files, top_k, kw)}
        rows.append(
            {
                "target": name,
                "vulnerable_code_files": len(vuln),
                "name_based": len(vuln & old),
                "content_based": len(vuln & new),
                "missed_by_name": sorted(vuln - old),
                "missed_by_content": sorted(vuln - new),
            }
        )
    return rows


if __name__ == "__main__":
    print(json.dumps(measure(), ensure_ascii=False, indent=2))
