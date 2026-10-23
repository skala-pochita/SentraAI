"""Explicit publisher-attachment acquisition. No generated substitutes for missing files."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from core.compliance_cli import import_source
from core.errors import AppError, ErrorCode
from core.regulation_sources import sources_root
from core.settings import get_settings

MAX_BYTES = 30 * 1024 * 1024


def download_sources(source_ids: list[str]) -> dict[str, Any]:
    settings = get_settings()
    config = settings.compliance_config_dir or settings.config_dir / "compliance"
    entries = yaml.safe_load((config / "downloads.yaml").read_text(encoding="utf-8"))["sources"]
    selected = source_ids or [item["id"] for item in entries]
    unknown = set(selected) - {item["id"] for item in entries}
    if unknown:
        raise AppError(ErrorCode.VALIDATION, "unknown download source IDs")
    root = sources_root()
    output = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for entry in entries:
            if entry["id"] not in selected:
                continue
            url = entry["attachment_url"]
            if urlparse(url).scheme != "https":
                raise AppError(ErrorCode.VALIDATION, "publisher downloads require HTTPS")
            try:
                with client.stream(
                    entry.get("method", "GET"),
                    url,
                    data=entry.get("form"),
                    headers={"Referer": entry["source_url"]},
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise AppError(ErrorCode.VALIDATION, "source PDF exceeds 30 MiB limit")
                    final_url = str(response.url)
            except httpx.HTTPError as exc:
                raise AppError(ErrorCode.UPSTREAM_ERROR, f"source download failed: {entry['id']}") from exc
            if not data.startswith(b"%PDF-"):
                raise AppError(ErrorCode.VALIDATION, "publisher attachment is not a PDF")
            target = root / "downloads" / f"{entry['id'].lower()}.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            acquisition = {
                "attachment_url": url,
                "final_url": final_url,
                "method": entry.get("method", "GET"),
                "form": entry.get("form"),
                "downloaded_at": datetime.now(UTC).isoformat(),
            }
            count = import_source(root, entry["id"], target, entry["source_url"], entry["version"], acquisition)
            output.append({"source": entry["id"], "bytes": len(data), "clauses": count})
    return {"downloaded": output}
