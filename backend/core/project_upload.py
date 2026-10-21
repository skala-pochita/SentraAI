"""Bounded ZIP extraction. Uploaded source is read as data and never executed."""

from __future__ import annotations

import io
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from core.errors import AppError, ErrorCode
from core.source_paths import excluded_source_path

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_FILES = 5000


WRAPPER_KEEP = {"src", "agent", "backend", "app"}  # 이 이름의 단일 폴더는 프로젝트 루트의 일부로 본다


def _unsafe(entry: zipfile.ZipInfo, path: PurePosixPath) -> bool:
    name = entry.filename
    return (
        "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or ":" in path.parts[0]
        or stat.S_ISLNK(entry.external_attr >> 16)
        or bool(entry.flag_bits & 1)
    )


def _accepted_entries(entries: list[zipfile.ZipInfo]) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    if len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
        raise AppError(ErrorCode.VALIDATION, "압축 해제 한도 초과: 5,000개 항목 / 100MB")
    names: set[str] = set()
    accepted = []
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if _unsafe(entry, path):
            raise AppError(ErrorCode.VALIDATION, "지원하지 않는 ZIP 경로·링크·암호화 항목")
        if excluded_source_path(path.as_posix()) or entry.is_dir():
            continue
        key = path.as_posix().casefold()
        if key in names:
            raise AppError(ErrorCode.VALIDATION, "중복 파일 경로")
        names.add(key)
        accepted.append((entry, path))
    if not accepted:
        raise AppError(ErrorCode.VALIDATION, "검사할 파일이 없습니다.")
    return accepted


def _write_entries(
    archive: zipfile.ZipFile, accepted: list[tuple[zipfile.ZipInfo, PurePosixPath]], destination: Path
) -> None:
    """선언 크기를 믿지 않고 실제로 풀린 바이트를 센다 (ZIP bomb)."""
    actual = 0
    for entry, path in accepted:
        output = destination.joinpath(*path.parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(entry) as source, output.open("xb") as target:
            while chunk := source.read(64 * 1024):
                actual += len(chunk)
                if actual > MAX_EXPANDED_BYTES:
                    raise AppError(ErrorCode.VALIDATION, "압축 해제 용량 한도 초과")
                target.write(chunk)


def _project_root(destination: Path) -> Path:
    """ZIP 이 프로젝트 폴더 하나로 감싸져 있으면 그 폴더를 루트로 쓴다."""
    children = list(destination.iterdir())
    if len(children) == 1 and children[0].is_dir() and children[0].name not in WRAPPER_KEEP:
        return children[0]
    return destination


def extract_project(data: bytes, destination: Path) -> Path:
    """Extract into a new unique directory; reject traversal, links and ZIP bombs."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(ErrorCode.VALIDATION, "ZIP 파일은 20MB 이하여야 합니다.")
    created = False  # 이미 있던 경로는 실패해도 지우지 않는다
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            accepted = _accepted_entries(archive.infolist())
            destination.mkdir(parents=True, exist_ok=False)
            created = True
            _write_entries(archive, accepted, destination)
            return _project_root(destination)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError) as exc:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise AppError(ErrorCode.VALIDATION, "유효한 ZIP 프로젝트를 업로드하세요.") from exc
    except AppError:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise
