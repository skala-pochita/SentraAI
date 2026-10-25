"""Package an explicitly prepared local corpus and offline index for fresh installs.

Run with backend Python from the repository root. No downloads or model calls.
Existing local originals and reviewed links are validated before packaging.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.compliance import load_catalog  # noqa: E402
from core.compliance_cli import atomic_yaml  # noqa: E402
from core.regulation_sources import bundled_root, read_manifest, sha256, sources_root  # noqa: E402
from mcp_servers.rag.local_regulations import ingest  # noqa: E402


async def main() -> None:
    source = sources_root()
    bundle = bundled_root()
    config = ROOT / "config/compliance"
    catalog = load_catalog(config, source)
    if catalog.missing_sources or not catalog.links:
        raise RuntimeError("All required originals and verified links must be present")
    destination = bundle / "sources"
    destination.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(source)
    atomic_yaml(destination / "MANIFEST.yaml", manifest)
    for record in manifest["sources"]:
        if record.get("status") != "AVAILABLE":
            continue
        for key in ("raw_file", "text_file", "markdown_file", "clauses_file"):
            relative = record[key]
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
    shutil.copyfile(source / "evidence_policy.yaml", destination / "evidence_policy.yaml")
    # Keep the official HTML used to transcribe the main body of each law.
    for file in (source / "raw").glob("*-body-current.html"):
        shutil.copyfile(file, destination / "raw" / file.name)
    (bundle / "config/links").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config / "catalog.yaml", bundle / "config/catalog.yaml")
    atomic_yaml(bundle / "config/links/bundled.yaml", [link.model_dump() for link in catalog.links])
    packaged = load_catalog(bundle / "config", destination)
    assert not packaged.missing_sources and len(packaged.links) == len(catalog.links)
    index = await ingest(bundle / "regulations.sqlite3")
    inventory = {
        "schema": 1,
        "prepared_on": "2026-10-25",
        "clauses": len(packaged.clauses),
        "links": len(packaged.links),
        "embedding": "hash-v1 (offline keyword approximation)",
        "files": {
            p.relative_to(bundle).as_posix(): sha256(p.read_bytes())
            for p in sorted(bundle.rglob("*"))
            if p.is_file() and p.name not in {"INVENTORY.json", "README.md"}
        },
    }
    (bundle / "INVENTORY.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {"index": index, "links": len(packaged.links), "missing": packaged.missing_sources}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
