"""Portable project transfer to a separate execution server; no shared host path."""

import base64
import hashlib
import io
import zipfile
from pathlib import Path

from core.errors import AppError, ErrorCode
from core.project_upload import MAX_UPLOAD_BYTES


def encode_project(root: Path) -> dict[str, str]:
    stream = io.BytesIO()
    root = root.resolve()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                archive.write(path, "project/" + path.relative_to(root).as_posix())
    data = stream.getvalue()
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(ErrorCode.VALIDATION, "실행 서버 전송 ZIP이 20MiB를 초과했습니다.")
    return {"project_zip": base64.b64encode(data).decode(), "sha256": hashlib.sha256(data).hexdigest()}
