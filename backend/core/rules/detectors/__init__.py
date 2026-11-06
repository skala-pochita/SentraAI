"""규칙 detector 구현. detector 이름 → 함수."""

from __future__ import annotations

import ast
import math
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from core.rules.models import Rule, RuleHit
from harness.masking import mask_text

# ---------- 공통 ----------

_COMMENT_MARKERS = ("//", "#", "/*", "*", "<!--", "--")


def comment_part(line: str, lang: str) -> str | None:
    """라인의 주석 부분 (없으면 None). 문자열 안의 // 는 단순 휴리스틱으로 제외."""
    s = line.strip()
    if lang == "markdown":
        m = re.search(r"<!--(.*?)(-->|$)", line)
        return m.group(1) if m else line  # 문서는 전체를 주석으로 본다
    if lang in ("yaml", "properties", "shell", "env"):
        idx = line.find("#")
        return line[idx + 1 :] if idx >= 0 else None
    if s.startswith(("*", "/*")):
        return s.lstrip("/*").strip()
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_str = not in_str
        if not in_str and line.startswith("//", i):
            return line[i + 2 :]
    return None


def lang_of(path: str) -> str:
    p = path.lower()
    name = p.rsplit("/", 1)[-1]
    if p.endswith((".yml", ".yaml")):
        return "yaml"
    if p.endswith(".md"):
        return "markdown"
    if p.endswith(".properties"):
        return "properties"
    if name == ".env" or name.startswith(".env."):
        return "env"
    if p.endswith((".sh", ".py")):
        return "shell"
    return "java"


def hit(rule: Rule, path: str, line_no: int, line: str, **extra: Any) -> RuleHit:
    return RuleHit(
        rule_id=rule.id,
        category=rule.category,
        severity=rule.default_severity,
        path=path,
        line=line_no,
        snippet_masked=mask_text(line.strip())[:300],
        needs_llm_review=rule.needs_llm_review,
        references=rule.references,
        fix_hint=rule.fix_hint,
        extra=extra,
    )


Detector = Callable[[Rule, str, str], list[RuleHit]]


# ---------- regex ----------


def detect_regex(rule: Rule, path: str, content: str) -> list[RuleHit]:
    flags = re.IGNORECASE if rule.ignore_case else 0
    pats = [re.compile(p, flags) for p in rule.all_patterns()]
    if rule.require_also and not re.search(rule.require_also, content, flags):
        return []
    if rule.forbid_also and re.search(rule.forbid_also, content, flags):
        return []
    lang = lang_of(path)
    if rule.multiline and not rule.comment_only:
        return _detect_multiline(rule, path, content, pats)
    out: list[RuleHit] = []
    for i, line in enumerate(content.splitlines(), start=1):
        text = comment_part(line, lang) if rule.comment_only else line
        if not text:
            continue
        if any(p.search(text) for p in pats):
            out.append(hit(rule, path, i, line))
    return out


def _detect_multiline(rule: Rule, path: str, content: str, pats: list[re.Pattern[str]]) -> list[RuleHit]:
    """파일 전체에 패턴 적용 (여러 줄로 나뉜 람다 DSL·메서드 체인·문자열 결합 대응). 줄당 1건."""
    lines = content.splitlines()
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln) + 1)
    seen: set[int] = set()
    out: list[RuleHit] = []
    for p in pats:
        for m in p.finditer(content):
            line_no = max(1, _line_of(starts, m.start()))
            text = lines[line_no - 1] if line_no <= len(lines) else ""
            if line_no in seen or text.lstrip().startswith(("//", "#", "*", "/*")):
                continue
            seen.add(line_no)
            out.append(hit(rule, path, line_no, text, span_lines=m.group(0).count("\n") + 1))
    out.sort(key=lambda h: h.line)
    return out


def _line_of(starts: list[int], offset: int) -> int:
    import bisect

    return bisect.bisect_right(starts, offset)


# ---------- entropy ----------

_QUOTED = re.compile(r'"([^"\\]{8,})"|\'([^\'\\]{8,})\'')  # Python 은 작은따옴표 문자열도 많다 (PY1)


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def detect_entropy(rule: Rule, path: str, content: str) -> list[RuleHit]:
    name_re = re.compile(rule.name_pattern or r"(?i)(key|secret|token)")
    out: list[RuleHit] = []
    for i, line in enumerate(content.splitlines(), start=1):
        for m in _QUOTED.finditer(line):
            value = m.group(1) or m.group(2)
            lhs = line[: m.start()]
            ident = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*[=:(]\s*$", lhs)
            if not ident or not name_re.search(ident[-1]):
                continue
            ent = shannon_entropy(value)
            if len(value) >= rule.min_length and ent >= rule.min_entropy:
                out.append(hit(rule, path, i, line, identifier=ident[-1], entropy=round(ent, 2)))
    return out


# ---------- yaml_path ----------


def _flatten_properties(content: str) -> list[tuple[str, Any, int]]:
    """application.properties: key=value / key: value → (key, value, line). true/false 는 bool 로 (yaml 과 같게)."""
    out: list[tuple[str, Any, int]] = []
    for i, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        m = re.match(r"([^=:\s]+)\s*[=:]\s*(.*)$", line)
        if not m:
            continue
        value: Any = m.group(2).strip()
        if str(value).lower() in ("true", "false"):
            value = str(value).lower() == "true"
        out.append((m.group(1), value, i))
    return out


def _flatten_yaml(content: str) -> list[tuple[str, Any, int]]:
    """(dotted.path, scalar value, 1-based line) 목록."""
    try:
        root = yaml.compose(content)
    except yaml.YAMLError:
        return []
    out: list[tuple[str, Any, int]] = []

    def walk(node: yaml.Node, prefix: str) -> None:
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                walk(v, f"{prefix}.{k.value}" if prefix else str(k.value))
        elif isinstance(node, yaml.SequenceNode):
            for idx, v in enumerate(node.value):
                walk(v, f"{prefix}[{idx}]")
        elif isinstance(node, yaml.ScalarNode):
            value: Any = yaml.safe_load(yaml.serialize(node))
            out.append((prefix, value, node.start_mark.line + 1))

    if root is not None:
        walk(root, "")
    return out


def _path_match(pattern: str, path: str) -> bool:
    regex = "^" + re.escape(pattern).replace(r"\*", r"[^.]+(?:\.[^.]+)*") + "$"
    return re.match(regex, path) is not None


def detect_yaml_path(rule: Rule, path: str, content: str) -> list[RuleHit]:
    lines = content.splitlines()
    out: list[RuleHit] = []
    flat = _flatten_properties(content) if path.endswith(".properties") else _flatten_yaml(content)
    for key, value, line_no in flat:
        if not any(_path_match(p, key) for p in rule.paths):
            continue
        if rule.plaintext_only:
            if value is None or str(value).strip() == "" or re.search(r"\$\{[^}]+\}", str(value)):
                continue
        if rule.equals is not None and value != rule.equals and str(value) != str(rule.equals):
            continue
        out.append(hit(rule, path, line_no, lines[line_no - 1] if line_no <= len(lines) else key, key=key))
    return out


# ---------- python (파일 단위 검사) ----------


_MAPPING = re.compile(r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping\b(\([^)]*\))?")
_AUTHZ = re.compile(r"@(?:PreAuthorize|Secured|RolesAllowed|PostAuthorize)\b")
_SENSITIVE_PATH = re.compile(r'"/?(?:[\w{}-]+/)*(?:admin|manage|management|internal|ops)\b', re.IGNORECASE)
_OWNER_CHECK = re.compile(
    r"(?:@AuthenticationPrincipal|Principal\b|Authentication\b|SecurityContextHolder|getOwner|ownerId|userId\s*\.equals"
    r"|checkOwner|assertOwner|isOwner|hasPermission|currentUser)",
)
_LOOKUP_BY_ID = re.compile(r"\.(?:findById|getById|getReferenceById|findOne|deleteById)\(\s*(\w+)")


def _methods(content: str) -> list[tuple[int, str, str]]:
    """(시작 줄, 어노테이션+시그니처, 본문) — 매핑 어노테이션이 붙은 메서드만, 중괄호 균형으로 본문을 자른다."""
    out: list[tuple[int, str, str]] = []
    for m in _MAPPING.finditer(content):
        sig_start = m.start()
        brace = content.find("{", m.end())
        if brace < 0 or re.search(r"\b(?:class|interface)\s", content[m.end() : brace]):
            continue  # 클래스 레벨 @RequestMapping 은 엔드포인트가 아니다
        depth, i = 0, brace
        while i < len(content):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        # 바로 위 어노테이션 묶음(@PreAuthorize 등)까지 시그니처로 본다
        head_start = content.rfind(";", 0, sig_start)
        head_start = max(head_start, content.rfind("}", 0, sig_start), content.rfind("{", 0, sig_start)) + 1
        sig = content[head_start:brace]
        out.append((content.count("\n", 0, sig_start) + 1, sig, content[brace : i + 1]))
    return out


def _class_level(content: str) -> tuple[str, bool]:
    """(클래스 @RequestMapping 경로, 클래스에 인가 어노테이션이 있는지)."""
    ann = r"@\w+(?:\((?:[^()]|\([^()]*\))*\))?"  # 인자 안 괄호 1단계 허용: @PreAuthorize("hasRole('ADMIN')")
    cls = re.search(rf"((?:{ann}\s*)+)(?:public\s+|open\s+|internal\s+)?class\s", content)
    head = cls.group(1) if cls else ""
    base = re.search(r"@RequestMapping\(((?:[^()]|\([^()]*\))*)\)", head)
    return (base.group(1) if base else ""), bool(_AUTHZ.search(head))


def _spring_authz_hits(rule: Rule, path: str, content: str) -> list[RuleHit]:
    if not re.search(r"@(?:Rest)?Controller\b", content):
        return []
    base, class_authz = _class_level(content)
    lines = content.splitlines()
    out: list[RuleHit] = []
    for line_no, sig, body in _methods(content):
        text = lines[line_no - 1] if line_no <= len(lines) else sig
        if rule.check == "spring_sensitive_endpoint_no_authz":
            if class_authz or _AUTHZ.search(sig):
                continue
            if _SENSITIVE_PATH.search(base) or _SENSITIVE_PATH.search(sig):
                out.append(hit(rule, path, line_no, text, reason="민감 경로 엔드포인트에 메서드·클래스 인가 어노테이션 없음"))
        elif rule.check == "spring_idor_candidate":
            pv = re.findall(r"@PathVariable(?:\([^)]*\))?\s+\w+(?:<[^>]*>)?\s+(\w+)", sig)
            used = {m.group(1) for m in _LOOKUP_BY_ID.finditer(body)}
            if pv and (set(pv) & used) and not _OWNER_CHECK.search(sig + body) and not _AUTHZ.search(sig):
                out.append(hit(rule, path, line_no, text, reason="경로 변수 ID 로 조회하면서 소유자·권한 확인 없음"))
    return out


def detect_python(rule: Rule, path: str, content: str) -> list[RuleHit]:
    if rule.check in ("spring_sensitive_endpoint_no_authz", "spring_idor_candidate"):
        return _spring_authz_hits(rule, path, content)
    if rule.check == "sensitive_file_present":
        name = Path(path).name
        pats = rule.params.get("names", [".env", "*.pem", "*.p12", "*.jks", "*.key"])
        if any(Path(name).match(p) for p in pats):
            first = content.splitlines()[0] if content else name
            return [hit(rule, path, 1, first, reason="sensitive file committed")]
    return []


# ---------- ast_python (PY3) ----------


def _py_aliases(tree: ast.AST) -> dict[str, str]:
    """import 별칭 → 원래 이름. `import subprocess as sp` → {sp: subprocess}, `from os import system` → {system: os.system}."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _dotted(func: ast.expr, aliases: dict[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    head = aliases.get(func.id, func.id)
    return ".".join([head, *reversed(parts)])


def _is_const(node: ast.expr | None) -> bool:
    """문자열·숫자 상수 (또는 상수만 담은 리스트·튜플)."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_is_const(e) for e in node.elts)
    return False


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _py_call_reason(name: str, call: ast.Call, spec: dict[str, Any]) -> str | None:
    """spec: {names: [...], nonconst_arg: 0?, kw_true: 'shell'?, missing_safe_loader: bool?}."""
    if name not in spec.get("names", []):
        return None
    arg0 = call.args[0] if call.args else _kw(call, "args") or _kw(call, "cmd")
    if spec.get("kw_true"):
        kv = _kw(call, spec["kw_true"])
        if not (isinstance(kv, ast.Constant) and kv.value is True):
            return None
        if _is_const(arg0):
            return None
        return f"{name}({spec['kw_true']}=True) 에 상수가 아닌 명령"
    if spec.get("missing_safe_loader"):
        loader = _kw(call, "Loader") or (call.args[1] if len(call.args) > 1 else None)
        lname = _dotted(loader, {}) if isinstance(loader, (ast.Name, ast.Attribute)) else None
        if lname and lname.rsplit(".", 1)[-1] in ("SafeLoader", "CSafeLoader", "BaseLoader"):
            return None
        return f"{name} 에 SafeLoader 없음"
    if "nonconst_arg" in spec and (arg0 is None or _is_const(arg0)):
        return None
    return f"{name} 호출"


def detect_ast_python(rule: Rule, path: str, content: str) -> list[RuleHit]:
    """Python 위험 호출: 별칭 import·여러 줄 호출을 해석하고 상수 인자는 제외한다 (정규식 대비 오탐·미탐 감소)."""
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    aliases = _py_aliases(tree)
    lines = content.splitlines()
    out: list[RuleHit] = []
    specs: list[dict[str, Any]] = list(rule.params.get("calls", []))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func, aliases)
        if name is None:
            continue
        for spec in specs:
            if (why := _py_call_reason(name, node, spec)) is not None:
                ln = node.lineno
                out.append(hit(rule, path, ln, lines[ln - 1] if ln <= len(lines) else name, call=name, reason=why))
                break
    out.sort(key=lambda h: h.line)
    return out


DETECTORS: dict[str, Detector] = {
    "ast_python": detect_ast_python,
    "regex": detect_regex,
    "entropy": detect_entropy,
    "yaml_path": detect_yaml_path,
    "python": detect_python,
}
