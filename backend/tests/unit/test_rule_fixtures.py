"""규칙 fixture 회귀 테스트 (datasets/rule_fixtures/*/expected.yaml): 양성은 전부 탐지, 음성은 하나도 탐지하지 않는다."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.rules.engine import scan_repo
from core.settings import REPO_ROOT

FIXTURES = sorted(p for p in (REPO_ROOT / "datasets" / "rule_fixtures").iterdir() if (p / "expected.yaml").is_file())


@pytest.mark.parametrize("root", FIXTURES, ids=[p.name for p in FIXTURES])
def test_rule_fixture_exact_hits(root: Path) -> None:
    expected = {
        (e["rule_id"], e["file"], int(e["line"]))
        for e in yaml.safe_load((root / "expected.yaml").read_text())["expected"]
    }
    actual = {(h.rule_id, h.path, h.line) for h in scan_repo(root) if not h.path.endswith((".md", "expected.yaml"))}
    assert not (expected - actual), f"미탐: {sorted(expected - actual)}"
    assert not (actual - expected), f"오탐: {sorted(actual - expected)}"
