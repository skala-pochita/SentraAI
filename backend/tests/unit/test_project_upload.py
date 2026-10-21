from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from core.errors import AppError
from core.project_upload import extract_project


def archive(files: dict[str, str]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return stream.getvalue()


def test_extract_strips_single_wrapper_and_ignores_git(tmp_path: Path) -> None:
    root = extract_project(
        archive({"project/app.py": "print(1)", "project/.git/config": "secret"}), tmp_path / "upload"
    )
    assert root.name == "project"
    assert (root / "app.py").read_text() == "print(1)"
    assert not (root / ".git").exists()


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/drive", "x\\escape"])
def test_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(AppError):
        extract_project(archive({path: "bad"}), tmp_path / "upload")
    assert not (tmp_path / "upload").exists()


def test_rejects_symlink(tmp_path: Path) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
    with pytest.raises(AppError):
        extract_project(stream.getvalue(), tmp_path / "upload")


def test_rejects_invalid_empty_and_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.project_upload as upload

    for data in [b"not zip", archive({})]:
        with pytest.raises(AppError):
            extract_project(data, tmp_path / "upload")
    monkeypatch.setattr(upload, "MAX_EXPANDED_BYTES", 3)
    with pytest.raises(AppError):
        extract_project(archive({"app.py": "1234"}), tmp_path / "upload")
    monkeypatch.setattr(upload, "MAX_FILES", 1)
    with pytest.raises(AppError):
        extract_project(archive({"a": "", "b": ""}), tmp_path / "upload")


def test_failed_extract_never_deletes_existing_directory(tmp_path: Path) -> None:
    existing = tmp_path / "keep"
    existing.mkdir()
    (existing / "data.txt").write_text("keep me")
    for payload in (b"not a zip", archive({"a.txt": "x"})):  # 형식 오류, 이미 있는 경로
        with pytest.raises(AppError):
            extract_project(payload, existing)
        assert (existing / "data.txt").read_text() == "keep me"
