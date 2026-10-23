"""Collect bounded source snapshots without executing uploaded code.

A matching implementation is a candidate, not proof of runtime binding or missing authorization.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from core.rules.engine import iter_repo_files, read_text
from core.schemas import Evidence
from harness.masking import mask_text

MAX_IMPLEMENTATIONS = 3
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_FILES = 100
MAX_SNIPPET_LINES = 32


def manifest_evidence(text: str, path: str, name: str) -> list[Evidence]:
    """Use YAML node marks, not a search for an unrelated occurrence of a tool name."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return []
    if not isinstance(root, MappingNode):
        return []
    for key, value in root.value:
        if not isinstance(key, ScalarNode) or key.value != "tools" or not isinstance(value, SequenceNode):
            continue
        for tool in value.value:
            if not isinstance(tool, MappingNode):
                continue
            fields = {k.value: (k, v) for k, v in tool.value if isinstance(k, ScalarNode)}
            declared = fields.get("name")
            if not declared or not isinstance(declared[1], ScalarNode) or declared[1].value != name:
                continue
            # Include only relevant declaration fields; a long description cannot hide a policy line.
            out = []
            for field in ("name", "side_effect", "requires_approval", "scopes"):
                if field not in fields:
                    continue
                k, v = fields[field]
                start, end = (
                    k.start_mark.line + 1,
                    max(k.start_mark.line + 1, v.end_mark.line + (v.end_mark.column > 0)),
                )
                end = min(end, start + MAX_SNIPPET_LINES - 1)
                out.append(
                    Evidence(
                        kind="manifest",
                        file_path=path,
                        line_start=start,
                        line_end=end,
                        snippet_masked=mask_text("\n".join(text.splitlines()[start - 1 : end])),
                        label=f"도구 설정: {field}",
                        relation="declared_policy",
                    )
                )
            return out
    return []


def _dispatch_name(node: ast.If) -> str | None:
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return None
    left, right = test.left, test.comparators[0]
    if (
        isinstance(left, ast.Name)
        and left.id in {"name", "tool_name"}
        and isinstance(right, ast.Constant)
        and isinstance(right.value, str)
    ):
        return right.value
    return None


def implementation_evidence(root: Path, names: set[str]) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = {name: [] for name in names}
    total = 0
    count = 0
    for rel, path in iter_repo_files(root):
        if not rel.endswith(".py") or any(p in {"tests", "test", "fixtures", "examples"} for p in Path(rel).parts):
            continue
        count += 1
        total += path.stat().st_size
        if count > MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
            break
        text = read_text(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, RecursionError):
            continue
        # Dispatch branches take precedence over a broad dispatcher function span.
        matches: list[tuple[str, ast.AST, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and (name := _dispatch_name(node)) in names:
                # elif belongs to another tool; never include it in this tool's source span.
                matches.append((name, node, "tool_dispatch_candidate"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                matches.append((node.name, node, "tool_function_candidate"))
        for name, node, method in matches:
            if len(out[name]) >= MAX_IMPLEMENTATIONS:
                continue
            start = node.lineno  # type: ignore[attr-defined]
            end = node.body[-1].end_lineno if isinstance(node, ast.If) and node.body else node.end_lineno  # type: ignore[attr-defined]
            end = min(end or start, start + MAX_SNIPPET_LINES - 1)
            out[name].append(
                Evidence(
                    kind="code",
                    file_path=rel,
                    line_start=start,
                    line_end=end,
                    snippet_masked=mask_text("\n".join(text.splitlines()[start - 1 : end])),
                    label=f"{name} 구현 후보",
                    relation=method,
                )
            )
    # Duplicate names are intentionally retained as candidates, never resolved by guessing.
    return out


def collect_tool_evidence(
    root: Path, manifest: dict[str, Any], text: str, path: str
) -> dict[str, list[dict[str, Any]]]:
    names = {t["name"] for t in manifest.get("tools", []) if isinstance(t, dict) and isinstance(t.get("name"), str)}
    implementations = implementation_evidence(root, names)
    return {
        name: [e.model_dump() for e in [*manifest_evidence(text, path, name), *implementations[name]]]
        for name in sorted(names)
    }
