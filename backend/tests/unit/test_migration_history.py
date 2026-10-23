"""Every shipped database revision must upgrade through a single head."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_legacy_revision_can_upgrade_to_head() -> None:
    root = Path(__file__).resolve().parents[2]
    scripts = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert scripts.get_current_head() == "0008"
    assert scripts.get_revision("0002").down_revision == "0001"
    assert scripts.get_revision("0008").down_revision == "0007"
    assert [r.revision for r in scripts.iterate_revisions("head", "0002")] == [
        "0008", "0007", "0006", "0005", "0004", "0003"
    ]
    assert [r.revision for r in scripts.walk_revisions()] == [
        "0008", "0007", "0006", "0005", "0004", "0003", "0002", "0001"
    ]
