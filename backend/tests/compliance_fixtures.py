from pathlib import Path
from shutil import copyfile

import pytest

from core.compliance_cli import atomic_yaml, import_source
from core.regulation_sources import load_clauses
from core.settings import get_settings, load_config

TEXT = '7. 보안성 원칙 점검 항목\n⑨ 입력 공격에 대한 방어 체계를 구성했는가?\n⑩ 모의 공격을 실행했는가?'

@pytest.fixture
def originals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "knowledge/sources"
    root.mkdir(parents=True)
    cfg = tmp_path / "config/compliance"
    (cfg / "links").mkdir(parents=True)
    source = {
        "id": "TEST",
        "title": "합성 테스트 안내서",
        "issuer": "테스트",
        "source_type": "kr_guideline",
        "format": "checklist",
        "condition": "finance",
        "status": "MISSING",
    }
    atomic_yaml(root / "MANIFEST.yaml", {"schema": 1, "sources": [source]})
    atomic_yaml(cfg / "catalog.yaml", {"version": "test-v2"})
    atomic_yaml(cfg / "links/default.yaml", [])
    copyfile(Path(__file__).resolve().parents[2] / "config/rag.yaml", cfg.parent / "rag.yaml")
    input_file = tmp_path / "synthetic.txt"
    input_file.write_text(TEXT, encoding="utf-8", newline="\n")
    assert import_source(root, "TEST", input_file) == 2
    clauses, missing = load_clauses(root)
    assert not missing
    monkeypatch.setenv("KNOWLEDGE_DIR", str(root.parent))
    monkeypatch.setenv("CONFIG_DIR", str(cfg.parent))
    get_settings.cache_clear()
    load_config.cache_clear()
    yield root, cfg, clauses
    get_settings.cache_clear()
    load_config.cache_clear()
