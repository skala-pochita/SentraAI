"""파일 간 관계(import·같은 패키지 타입 참조) 색인 — ReAct 가 파일을 넘나드는 흐름을 따라가도록 관련 파일을 알려 준다 (S3).

정밀한 호출 그래프가 아니라 가벼운 정적 근사다: Java/Kotlin 은 import + 같은 패키지의 타입 이름 참조,
Python 은 import/from-import(상대 import 포함). 결과는 '먼저 읽어 볼 후보'로만 쓰고 판정 근거로 쓰지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

_PKG = re.compile(r"^\s*package\s+([\w.]+)", re.M)
_JIMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;?\s*$", re.M)
_PY_FROM = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+([\w*,\s()]+)", re.M)
_PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)", re.M)
_IDENT = re.compile(r"\b[A-Z][A-Za-z0-9_]+\b")


@dataclass
class CodeGraph:
    out_edges: dict[str, set[str]] = field(default_factory=dict)
    in_edges: dict[str, set[str]] = field(default_factory=dict)

    def related(self, path: str, limit: int = 5) -> list[dict[str, str]]:
        """이 파일이 쓰는 파일(uses) 먼저, 그다음 이 파일을 쓰는 파일(used_by)."""
        out: list[dict[str, str]] = []
        for p in sorted(self.out_edges.get(path, set())):
            out.append({"path": p, "relation": "uses"})
        for p in sorted(self.in_edges.get(path, set()) - self.out_edges.get(path, set())):
            out.append({"path": p, "relation": "used_by"})
        return out[:limit]


def _jvm_edges(files: Mapping[str, str]) -> dict[str, set[str]]:
    jvm = {p: c for p, c in files.items() if p.endswith((".java", ".kt"))}
    fqcn: dict[str, str] = {}
    by_pkg: dict[str, dict[str, str]] = {}
    for p, c in jvm.items():
        m = _PKG.search(c)
        pkg = m.group(1) if m else ""
        stem = PurePosixPath(p).stem
        fqcn[f"{pkg}.{stem}" if pkg else stem] = p
        by_pkg.setdefault(pkg, {})[stem] = p
    edges: dict[str, set[str]] = {}
    for p, c in jvm.items():
        m = _PKG.search(c)
        pkg = m.group(1) if m else ""
        targets: set[str] = set()
        for imp in _JIMPORT.findall(c):
            if imp in fqcn:
                targets.add(fqcn[imp])
            else:  # import a.b.* → 그 패키지의 타입 중 실제로 쓰인 것
                for stem, q in by_pkg.get(imp, {}).items():
                    if re.search(rf"\b{re.escape(stem)}\b", c):
                        targets.add(q)
        same = by_pkg.get(pkg, {})
        for ident in set(_IDENT.findall(c)):
            if ident in same:
                targets.add(same[ident])
        targets.discard(p)
        edges[p] = targets
    return edges


def _py_module(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _py_edges(files: Mapping[str, str]) -> dict[str, set[str]]:
    py = {p for p in files if p.endswith(".py")}
    modules: dict[str, str] = {}
    for p in py:
        mod = _py_module(p)
        modules[mod] = p
        # 소스 루트(src/, app/ 상위 등)를 모르므로 앞부분을 뗀 짧은 이름도 등록한다 (길이가 긴 쪽 우선)
        segs = mod.split(".")
        for i in range(1, len(segs)):
            modules.setdefault(".".join(segs[i:]), p)

    def resolve(name: str) -> str | None:
        return modules.get(name)

    edges: dict[str, set[str]] = {}
    for p in py:
        c = files[p]
        pkg = _py_module(p).rsplit(".", 1)[0] if "." in _py_module(p) else ""
        if p.endswith("__init__.py"):
            pkg = _py_module(p)
        targets: set[str] = set()
        for dots, mod, names in _PY_FROM.findall(c):
            base = mod
            if dots:  # 상대 import
                up = pkg.split(".") if pkg else []
                up = up[: max(0, len(up) - (len(dots) - 1))]
                base = ".".join([*up, mod] if mod else up)
            if (t := resolve(base)) is not None:
                targets.add(t)
            for n in re.split(r"[\s,()]+", names):
                if n and n != "*" and (t := resolve(f"{base}.{n}" if base else n)) is not None:
                    targets.add(t)
        for group in _PY_IMPORT.findall(c):
            for name in (x.strip() for x in group.split(",")):
                if (t := resolve(name)) is not None:
                    targets.add(t)
        targets.discard(p)
        edges[p] = targets
    return edges


def build_graph(files: Mapping[str, str]) -> CodeGraph:
    out = {**_jvm_edges(files), **_py_edges(files)}
    inc: dict[str, set[str]] = {}
    for src, dsts in out.items():
        for d in dsts:
            inc.setdefault(d, set()).add(src)
    return CodeGraph(out, inc)
