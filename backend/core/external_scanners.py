"""외부 SAST·SCA 연동 (E1~E3): Semgrep, Bandit, 의존성 취약점(로컬 권고 DB + 선택적 OSV).

- 결과는 규칙 엔진과 같은 RuleHit 으로 바꿔 정적 후보(NEEDS_REVIEW)로 넣는다. 판정은 기존 ReAct·근거 검증이 한다.
- 카테고리는 우리 카테고리(core.categories)로 매핑되는 것만 받는다 (자유 카테고리 금지 원칙 유지).
- 도구가 없으면 NOT_INSTALLED, 꺼져 있으면 DISABLED 로 상태만 돌려준다. 외부 도구 실패가 감사를 멈추지 않는다.
- 모든 외부 호출은 타임아웃이 있고 오류는 구조화한다 (CLAUDE.md 규칙 3). snippet 은 mask_text (규칙 5).
- OSV 온라인 조회는 감사 대상의 패키지 이름·버전을 외부로 보내므로 기본 꺼짐 (SCA_OSV_ONLINE=true 일 때만).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from core.rules.engine import iter_repo_files, read_text
from core.rules.models import RuleHit
from core.settings import get_settings
from harness.masking import mask_text

log = logging.getLogger("external_scanners")

SCANNER_TIMEOUT_S = 180.0
OSV_TIMEOUT_S = 10.0

CWE_CATEGORY = {
    "89": "SQL_INJECTION",
    "77": "COMMAND_INJECTION",
    "78": "COMMAND_INJECTION",
    "94": "COMMAND_INJECTION",
    "95": "COMMAND_INJECTION",
    "502": "INSECURE_DESERIALIZATION",
    "918": "SSRF",
    "79": "XSS",
    "611": "XXE",
    "22": "PATH_TRAVERSAL",
    "23": "PATH_TRAVERSAL",
    "295": "INSECURE_TRANSPORT",
    "297": "INSECURE_TRANSPORT",
    "327": "WEAK_CRYPTO",
    "328": "WEAK_CRYPTO",
    "916": "WEAK_CRYPTO",
    "798": "HARDCODED_CREDENTIAL",
    "259": "HARDCODED_CREDENTIAL",
    "352": "SECCONFIG_CSRF",
    "284": "BROKEN_ACCESS_CONTROL",
    "285": "BROKEN_ACCESS_CONTROL",
    "639": "BROKEN_ACCESS_CONTROL",
    "862": "BROKEN_ACCESS_CONTROL",
    "489": "DEBUG_ENABLED",
    "532": "SENSITIVE_LOGGING",
    "942": "SECCONFIG_CORS",
}
# Bandit test id → 카테고리 (CWE 가 넓게 잡힌 항목은 test id 로 직접)
BANDIT_CATEGORY = {
    "B102": "COMMAND_INJECTION",
    "B307": "COMMAND_INJECTION",
    "B602": "COMMAND_INJECTION",
    "B604": "COMMAND_INJECTION",
    "B605": "COMMAND_INJECTION",
    "B609": "COMMAND_INJECTION",
    "B301": "INSECURE_DESERIALIZATION",
    "B302": "INSECURE_DESERIALIZATION",
    "B506": "INSECURE_DESERIALIZATION",
    "B614": "INSECURE_DESERIALIZATION",
    "B608": "SQL_INJECTION",
    "B610": "SQL_INJECTION",
    "B611": "SQL_INJECTION",
    "B501": "INSECURE_TRANSPORT",
    "B502": "INSECURE_TRANSPORT",
    "B503": "INSECURE_TRANSPORT",
    "B504": "INSECURE_TRANSPORT",
    "B323": "INSECURE_TRANSPORT",
    "B303": "WEAK_CRYPTO",
    "B304": "WEAK_CRYPTO",
    "B305": "WEAK_CRYPTO",
    "B324": "WEAK_CRYPTO",
    "B701": "XSS",
    "B702": "XSS",
    "B703": "XSS",
    "B704": "XSS",
    "B201": "DEBUG_ENABLED",
    "B310": "SSRF",
    "B313": "XXE",
    "B314": "XXE",
    "B315": "XXE",
    "B316": "XXE",
    "B317": "XXE",
    "B318": "XXE",
    "B319": "XXE",
    "B320": "XXE",
    "B105": "HARDCODED_CREDENTIAL",
    "B106": "HARDCODED_CREDENTIAL",
    "B107": "HARDCODED_CREDENTIAL",
}
_LEVEL = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "INFO": 0, "WARNING": 1, "ERROR": 2, "UNDEFINED": 0}
_SEVERITY = {0: "LOW", 1: "MEDIUM", 2: "HIGH"}


@dataclass
class ScanResult:
    hits: list[RuleHit] = field(default_factory=list)
    status: dict[str, str] = field(default_factory=dict)  # scanner → COMPLETED|NOT_INSTALLED|DISABLED|ERROR|TIMEOUT
    detail: dict[str, str] = field(default_factory=dict)


def _hit(
    rule_id: str, category: str, severity: str, path: str, line: int, snippet: str, refs: list[str], **extra: Any
) -> RuleHit:
    return RuleHit(
        rule_id=rule_id,
        category=category,
        severity=severity,  # type: ignore[arg-type]
        path=path,
        line=max(1, line),
        snippet_masked=mask_text(snippet.strip())[:300],
        needs_llm_review=True,
        references=refs,
        extra={"external": True, **extra},
    )


async def _run(cmd: list[str], cwd: Path, timeout: float = SCANNER_TIMEOUT_S) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _line_text(root: Path, rel: str, line: int) -> str:
    text = read_text(root / rel) or ""
    lines = text.splitlines()
    return lines[line - 1] if 1 <= line <= len(lines) else ""


# ---------------- Bandit ----------------


def parse_bandit(data: dict[str, Any], root: Path, min_level: int = 1) -> list[RuleHit]:
    out: list[RuleHit] = []
    for r in data.get("results", []):
        cat = BANDIT_CATEGORY.get(r.get("test_id", ""))
        sev, conf = _LEVEL.get(r.get("issue_severity", "LOW"), 0), _LEVEL.get(r.get("issue_confidence", "LOW"), 0)
        if cat is None or sev < min_level or conf < min_level:
            continue  # import 경고·LOW 등급 같은 잡음은 후보로 넣지 않는다
        try:
            # bandit 은 cwd(root) 기준 상대 경로(./app/x.py)를 준다 → root 기준으로 해석
            rel = (root / r["filename"]).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        line = int(r.get("line_number", 1))
        cwe = str((r.get("issue_cwe") or {}).get("id", ""))
        out.append(
            _hit(
                f"EXT-BANDIT-{r['test_id']}",
                cat,
                _SEVERITY[sev],
                rel,
                line,
                _line_text(root, rel, line) or r.get("code", ""),
                [f"CWE-{cwe}"] if cwe else [],
                scanner="bandit",
                message=str(r.get("issue_text", ""))[:200],
            )
        )
    return out


async def run_bandit(root: Path, res: ScanResult) -> None:
    exe = shutil.which("bandit")
    if exe is None:
        res.status["bandit"] = "NOT_INSTALLED"
        return
    if not any(rel.endswith(".py") for rel, _ in iter_repo_files(root)):
        res.status["bandit"] = "SKIPPED_NO_PYTHON"
        return
    try:
        _code, out = await _run([exe, "-r", ".", "-f", "json", "-q", "-x", ".git,node_modules,build,target"], root)
        res.hits += parse_bandit(json.loads(out or "{}"), root)
        res.status["bandit"] = "COMPLETED"
    except TimeoutError:
        res.status["bandit"] = "TIMEOUT"
    except (OSError, json.JSONDecodeError) as e:
        res.status["bandit"], res.detail["bandit"] = "ERROR", type(e).__name__


# ---------------- Semgrep ----------------


def parse_semgrep(data: dict[str, Any]) -> list[RuleHit]:
    out: list[RuleHit] = []
    for r in data.get("results", []):
        meta = (r.get("extra") or {}).get("metadata") or {}
        cwes = meta.get("cwe") or []
        cwes = [cwes] if isinstance(cwes, str) else cwes
        ids = [m.group(1) for c in cwes if (m := re.search(r"CWE-(\d+)", str(c)))]
        cat = next((CWE_CATEGORY[i] for i in ids if i in CWE_CATEGORY), None)
        if cat is None:
            continue
        sev = _LEVEL.get(str((r.get("extra") or {}).get("severity", "WARNING")).upper(), 1)
        line = int((r.get("start") or {}).get("line", 1))
        out.append(
            _hit(
                f"EXT-SEMGREP-{str(r.get('check_id', 'rule')).rsplit('.', 1)[-1]}"[:80],
                cat,
                _SEVERITY[sev],
                str(r.get("path", "")),
                line,
                str((r.get("extra") or {}).get("lines", "")),
                [f"CWE-{i}" for i in ids],
                scanner="semgrep",
                check_id=str(r.get("check_id", ""))[:200],
            )
        )
    return out


async def run_semgrep(root: Path, res: ScanResult) -> None:
    exe = shutil.which("semgrep")
    if exe is None:
        res.status["semgrep"] = "NOT_INSTALLED"
        return
    config = get_settings().semgrep_config
    try:
        _code, out = await _run([exe, "scan", "--config", config, "--json", "--metrics=off", "--quiet", "."], root)
        res.hits += parse_semgrep(json.loads(out or "{}"))
        res.status["semgrep"] = "COMPLETED"
    except TimeoutError:
        res.status["semgrep"] = "TIMEOUT"
    except (OSError, json.JSONDecodeError) as e:
        res.status["semgrep"], res.detail["semgrep"] = "ERROR", type(e).__name__


# ---------------- SCA (의존성) ----------------


@dataclass(frozen=True)
class Dependency:
    ecosystem: str  # PyPI | Maven
    name: str
    version: str
    path: str
    line: int


_REQ = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+?)\s*==\s*([\w.\-+]+)")
_GRADLE = re.compile(
    r"""(?:implementation|api|compile|runtimeOnly|compileOnly)\s*\(?\s*["']([\w.\-]+):([\w.\-]+):([\w.\-]+)["']"""
)
_PYPROJECT_DEP = re.compile(r"""["']([A-Za-z0-9_.\-]+)\s*==\s*([\w.\-+]+)["']""")


def parse_dependencies(root: Path) -> list[Dependency]:
    out: list[Dependency] = []
    for rel, p in iter_repo_files(root):
        name = rel.rsplit("/", 1)[-1]
        text = read_text(p) or ""
        lines = text.splitlines()
        if re.fullmatch(r"requirements[\w\-.]*\.txt", name):
            for i, ln in enumerate(lines, 1):
                if m := _REQ.match(ln.split("#", 1)[0]):
                    out.append(Dependency("PyPI", m.group(1).split("[")[0].lower(), m.group(2), rel, i))
        elif name == "pyproject.toml":
            for i, ln in enumerate(lines, 1):
                for m in _PYPROJECT_DEP.finditer(ln):
                    out.append(Dependency("PyPI", m.group(1).lower(), m.group(2), rel, i))
        elif name in ("build.gradle", "build.gradle.kts"):
            for i, ln in enumerate(lines, 1):
                if m := _GRADLE.search(ln):
                    out.append(Dependency("Maven", f"{m.group(1)}:{m.group(2)}", m.group(3), rel, i))
        elif name == "pom.xml":
            out += _pom_dependencies(rel, text)
    return out


def _pom_dependencies(rel: str, text: str) -> list[Dependency]:
    pm_block = re.search(r"<properties>(.*?)</properties>", text, re.S)
    props = dict(re.findall(r"<([\w.\-]+)>([^<]+)</\1>", pm_block.group(1))) if pm_block else {}
    out: list[Dependency] = []
    for m in re.finditer(r"<dependency>(.*?)</dependency>", text, re.S):
        block = m.group(1)
        g, a, v = (re.search(rf"<{t}>\s*([^<]+?)\s*</{t}>", block) for t in ("groupId", "artifactId", "version"))
        if not (g and a and v):
            continue
        ver = v.group(1)
        if pm := re.fullmatch(r"\$\{([\w.\-]+)\}", ver):
            ver = props.get(pm.group(1), "")
        if not ver:
            continue
        line = text.count("\n", 0, m.start(1) + block.find("<version>")) + 1
        out.append(Dependency("Maven", f"{g.group(1)}:{a.group(1)}", ver.strip(), rel, line))
    return out


def _vtuple(v: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for x in re.split(r"[.\-+]", v):
        parts.append((0, int(x)) if x.isdigit() else (-1, x))  # 숫자 > 문자(RC·SNAPSHOT 은 정식보다 앞)
    while parts and parts[-1] == (0, 0):
        parts.pop()
    return tuple(parts)


def in_range(version: str, ranges: list[dict[str, str]]) -> bool:
    v = _vtuple(version)
    for r in ranges:
        lo, hi = r.get("introduced"), r.get("fixed")
        if (lo is None or v >= _vtuple(lo)) and (hi is None or v < _vtuple(hi)):
            return True
    return False


def load_advisories() -> list[dict[str, Any]]:
    path = get_settings().datasets_dir / "advisories" / "advisories.yaml"
    return (
        list((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("advisories", [])) if path.is_file() else []
    )


def match_local(deps: list[Dependency], advisories: list[dict[str, Any]], root: Path) -> list[RuleHit]:
    out: list[RuleHit] = []
    for d in deps:
        for adv in advisories:
            if adv["ecosystem"] != d.ecosystem or adv["package"].lower() != d.name.lower():
                continue
            if in_range(d.version, adv["ranges"]):
                out.append(
                    _hit(
                        f"SCA-{adv['id']}",
                        "VULNERABLE_DEPENDENCY",
                        adv.get("severity", "HIGH"),
                        d.path,
                        d.line,
                        _line_text(root, d.path, d.line) or f"{d.name} {d.version}",
                        [adv["id"], *adv.get("refs", [])],
                        scanner="sca-local",
                        package=d.name,
                        version=d.version,
                        fixed=adv["ranges"][-1].get("fixed"),
                        summary=adv.get("summary", ""),
                    )
                )
    return out


async def query_osv(deps: list[Dependency], root: Path) -> list[RuleHit]:
    """OSV querybatch (선택). 패키지 이름·버전만 보낸다 (코드·경로는 보내지 않음)."""
    if not deps:
        return []
    body = {"queries": [{"package": {"name": d.name, "ecosystem": d.ecosystem}, "version": d.version} for d in deps]}
    async with httpx.AsyncClient(timeout=OSV_TIMEOUT_S) as client:
        r = await client.post("https://api.osv.dev/v1/querybatch", json=body)
        r.raise_for_status()
        results = r.json().get("results", [])
    out: list[RuleHit] = []
    for d, res in zip(deps, results, strict=False):
        for v in (res or {}).get("vulns", [])[:5]:
            out.append(
                _hit(
                    f"SCA-{v['id']}",
                    "VULNERABLE_DEPENDENCY",
                    "HIGH",
                    d.path,
                    d.line,
                    _line_text(root, d.path, d.line),
                    [v["id"]],
                    scanner="osv",
                    package=d.name,
                    version=d.version,
                )
            )
    return out


async def run_sca(root: Path, res: ScanResult) -> None:
    deps = parse_dependencies(root)
    local = match_local(deps, load_advisories(), root)
    res.hits += local
    res.status["sca_local"] = "COMPLETED"
    res.detail["sca_local"] = f"dependencies={len(deps)} matched={len(local)}"
    if not get_settings().sca_osv_online:
        res.status["sca_osv"] = "DISABLED"
        return
    try:
        seen = {(h.path, h.line, h.rule_id) for h in res.hits}
        res.hits += [h for h in await query_osv(deps, root) if (h.path, h.line, h.rule_id) not in seen]
        res.status["sca_osv"] = "COMPLETED"
    except (httpx.HTTPError, ValueError) as e:
        res.status["sca_osv"], res.detail["sca_osv"] = "ERROR", type(e).__name__


async def run_external(root: Path, enabled: list[str]) -> ScanResult:
    res = ScanResult()
    runners = {"bandit": run_bandit, "semgrep": run_semgrep, "sca": run_sca}
    for name in runners:
        if name not in enabled:
            res.status[name] = "DISABLED"
    await asyncio.gather(*(runners[n](root, res) for n in runners if n in enabled))
    res.hits.sort(key=lambda h: (h.path, h.line, h.rule_id))
    return res
