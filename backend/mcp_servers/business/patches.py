"""수정안 편집(edits) → unified diff 계산, git apply --check, 패치 후 규칙 사전 재검사.

edits 는 마스킹되지 않은 실제 파일 위에서 서버가 적용한다 (워커·LLM 은 마스킹된 내용만 봄, DECISIONS #8):
  {"op": "replace", "path", "old", "new"}               old 가 파일에 정확히 1번 있어야 함
  {"op": "line_regex", "path", "line", "pattern", "replacement"}  해당 라인에만 정규식 치환
  {"op": "delete_line", "path", "line"}
  {"op": "append", "path", "text"}
  {"op": "delete_file", "path"}
"""

from __future__ import annotations

import difflib
import re
import tempfile
from pathlib import Path
from typing import Any

from core.errors import AppError, ErrorCode
from core.rules.engine import scan_files
from core.rules.models import RuleHit, load_rules
from mcp_servers.business.workspace import git, safe_path


def _apply_one(content: str | None, e: dict[str, Any]) -> str | None:
    op = e.get("op")
    if op == "delete_file":
        return None
    if content is None:
        raise AppError(ErrorCode.NOT_FOUND, f"file not found: {e.get('path')}")
    if op == "replace":
        old, new = str(e["old"]), str(e["new"])
        n = content.count(old)
        if n != 1:
            raise AppError(ErrorCode.VALIDATION, f"edit.old must occur exactly once in {e['path']} (found {n})")
        return content.replace(old, new)
    lines = content.split("\n")
    if op in ("line_regex", "delete_line"):
        idx = int(e["line"]) - 1
        if not 0 <= idx < len(lines):
            raise AppError(ErrorCode.VALIDATION, f"line {e['line']} out of range in {e['path']}")
        if op == "delete_line":
            del lines[idx]
        else:
            new_line, n = re.subn(str(e["pattern"]), str(e["replacement"]), lines[idx])
            if n == 0:
                raise AppError(ErrorCode.VALIDATION, f"pattern not found at {e['path']}:{e['line']}")
            lines[idx] = new_line
        return "\n".join(lines)
    if op == "append":
        sep = "" if content.endswith("\n") else "\n"
        return content + sep + str(e["text"]).rstrip("\n") + "\n"
    raise AppError(ErrorCode.VALIDATION, f"unknown edit op {op}")


def apply_edits(root: Path, edits: list[dict[str, Any]]) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """(원본 {path: content|None}, 결과 {path: content|None}). 같은 파일의 edit 는 순서대로 누적."""
    before: dict[str, str | None] = {}
    after: dict[str, str | None] = {}

    # 같은 파일의 라인 기반 edit 는 아래쪽부터 적용해 라인 번호가 밀리지 않게 한다.
    def order(e: dict[str, Any]) -> tuple[int, int]:
        op = e.get("op")
        if op in ("line_regex", "delete_line"):
            return 0, -int(e.get("line", 0))
        return {"replace": 1, "append": 2, "delete_file": 3}.get(str(op), 4), 0

    for e in sorted(edits, key=order):
        path = str(e.get("path", ""))
        full = safe_path(root, path)
        if path not in before:
            before[path] = full.read_text(encoding="utf-8") if full.is_file() else None
            after[path] = before[path]
        after[path] = _apply_one(after[path], e)
    return before, after


def unified_diff(before: dict[str, str | None], after: dict[str, str | None]) -> str:
    chunks: list[str] = []
    for path in sorted(before):
        a, b = before[path], after[path]
        if a == b:
            continue
        a_lines = (a or "").splitlines(keepends=True)
        b_lines = (b or "").splitlines(keepends=True)
        if a_lines and not a_lines[-1].endswith("\n"):
            a_lines[-1] += "\n"
        if b_lines and not b_lines[-1].endswith("\n"):
            b_lines[-1] += "\n"
        from_name = f"a/{path}" if a is not None else "/dev/null"
        to_name = f"b/{path}" if b is not None else "/dev/null"
        header = f"diff --git a/{path} b/{path}\n"
        if b is None:
            header += "deleted file mode 100644\n"
        chunks.append(header + "".join(difflib.unified_diff(a_lines, b_lines, from_name, to_name, n=3)))
    return "".join(chunks)


async def git_apply_check(root: Path, diff: str) -> tuple[bool, str]:
    if not diff.strip():
        return False, "empty diff"
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
        f.write(diff)
        patch = f.name
    try:
        res = await git(root, "apply", "--check", patch, check=False)
    finally:
        Path(patch).unlink(missing_ok=True)
    return res.returncode == 0, (res.stderr or "")[-500:]


async def git_apply(root: Path, diff: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
        f.write(diff)
        patch = f.name
    try:
        await git(root, "apply", "--index", patch)
    finally:
        Path(patch).unlink(missing_ok=True)


def precheck(after: dict[str, str | None], rule_ids: list[str]) -> list[RuleHit]:
    """패치된 파일에 해당 규칙 재실행 (fix_generate 의 사전 확인)."""
    files = {p: c for p, c in after.items() if c is not None}
    rules = [r for r in load_rules("code") if r.id in rule_ids]
    return scan_files(files, rules) if rules else []
