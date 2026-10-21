"""Shared artifact exclusions that preserve source packages named target/build."""

from pathlib import PurePosixPath

ALWAYS_SKIP = {
    ".git",
    "node_modules",
    "__MACOSX",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".runtime",
    ".gradle",
    ".idea",
}
BUILD_DIRS = {"target", "build", "dist", "out"}


def excluded_source_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    for i, part in enumerate(parts):
        if part in ALWAYS_SKIP:
            return True
        if part in BUILD_DIRS and "src" not in parts[:i]:
            return True
    return False
