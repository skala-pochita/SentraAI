"""위험 파일 선택: 파일 이름이 아니라 내용 신호로 점수를 매긴다 (S2·PY4).

ReAct 조사는 정적 후보가 있는 파일 + 위험 파일 상위 K 개를 본다. 정적 규칙을 피한 취약점(난독화·문자열 분할·
규칙 없는 유형)은 후보가 없으므로, 어떤 파일을 '위험 파일'로 고르느냐가 정적 회피 탐지율을 좌우한다.
이전에는 파일 이름 키워드(Config·Client·Payment ...)와 .java 확장자로만 골라서, 이름이 평범한 파일
(utils.py, helpers.py, views.py)에 있는 취약점은 조사 대상에 들어가지 못했다. 언어 중립적인 내용 신호를 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

CODE_EXT = (".java", ".kt", ".py", ".js", ".ts", ".go", ".rb", ".php", ".cs")
TEST_PATH = re.compile(r"(^|/)(src/test|tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|Test\.(java|kt)$")

# (신호 이름, 가중치, 패턴). 같은 신호는 파일당 최대 3회까지만 센다
SIGNALS: list[tuple[str, int, re.Pattern[str]]] = [
    # 외부 입력이 들어오는 진입점
    ("entrypoint", 2, re.compile(r"@(?:Rest)?Controller\b|@(?:Get|Post|Put|Delete|Patch|Request)Mapping\b")),
    (
        "entrypoint",
        2,
        re.compile(r"@\w+\.(?:route|get|post|put|delete|patch|websocket)\(|\bAPIRouter\(|\burlpatterns\b"),
    ),
    (
        "input",
        1,
        re.compile(
            r"\brequest\.(?:args|form|json|values|GET|POST|body|query_params|files)|@(?:RequestParam|PathVariable|RequestBody)\b"
        ),
    ),
    # 보안 설정
    (
        "security_config",
        3,
        re.compile(r"\bSecurityFilterChain\b|\bHttpSecurity\b|\bCORS_|\bCORSMiddleware\b|\bALLOWED_HOSTS\b"),
    ),
    # 위험 싱크
    (
        "sql",
        2,
        re.compile(r"\b(?:jdbcTemplate|createQuery|createNativeQuery|cursor|executemany)\b|\.execute\(|\btext\(", re.I),
    ),
    (
        "exec",
        3,
        re.compile(
            r"\b(?:subprocess|os\.system|os\.popen|Runtime\.getRuntime|ProcessBuilder|child_process)\b|(?<![.\w])(?:eval|exec)\("
        ),
    ),
    ("deserialize", 3, re.compile(r"\b(?:pickle|marshal|ObjectInputStream|XMLDecoder|yaml\.load|jsonpickle)\b")),
    (
        "sql",
        2,
        re.compile(r"\.\w*(?:query|update|execute)\w*\(\s*[\"'f][^\"']*\b(?:select|insert|update|delete)\b", re.I),
    ),
    ("xml", 2, re.compile(r"\betree\.|\b(?:DocumentBuilderFactory|XMLInputFactory|SAXParserFactory|xml\.sax)\b")),
    ("network", 1, re.compile(r"\b(?:requests|httpx|urlopen|RestTemplate|WebClient|HttpClient|fetch)\b")),
    ("file", 1, re.compile(r"\bopen\(|\bPaths\.get\(|\bnew File\(|\bsend_file\(|\bFileResponse\(")),
    ("crypto", 1, re.compile(r"\b(?:hashlib|MessageDigest|Cipher|jwt|JWT|Signature|bcrypt)\b|PasswordEncoder\b")),
    # 비밀값 흔적 (인코딩·분할 포함): 키 이름 + 문자열 대입, Base64 디코드, 문자열 결합
    (
        "secret",
        2,
        re.compile(r"(?i)\b\w*(?:secret|token|api_?key|passw(?:or)?d|credential|private_?key)\w*\s*[:=]\s*[\"'f]"),
    ),
    (
        "obfuscation",
        3,
        re.compile(r"\b(?:Base64|b64decode|base64\.b64decode|atob|getDecoder\(\)\.decode|fromCharCode)\b"),
    ),
    ("obfuscation", 2, re.compile(r"[\"'](?:sk_|sk-|AKIA|ghp_|xox[bp]-)[\w-]*[\"']\s*\+")),
]


@dataclass(frozen=True)
class FileRisk:
    path: str
    score: int  # 내용 점수 + 이름 보너스 (정렬용)
    signals: dict[str, int]
    content_score: int = 0  # 최소 점수 판정은 내용 점수로만 (이름만 그럴듯한 빈 파일 제외)


def score_file(path: str, content: str, name_keywords: Iterable[str] = ()) -> FileRisk:
    counts: dict[str, int] = {}
    for name, _w, pat in SIGNALS:
        counts[name] = min(3, counts.get(name, 0) + len(pat.findall(content)))
    weights: dict[str, int] = {}
    for name, w, _p in SIGNALS:
        weights[name] = max(weights.get(name, 0), w)
    content_score = sum(weights[n] * c for n, c in counts.items())
    base = path.rsplit("/", 1)[-1].lower()
    bonus = sum(1 for k in name_keywords if k.lower() in base)  # 이름은 비슷한 점수끼리 순서만 바꾸는 작은 보너스
    return FileRisk(path, content_score + bonus, {n: c for n, c in counts.items() if c}, content_score)


def rank_files(
    files: Iterable[tuple[str, str]], top_k: int, name_keywords: Iterable[str] = (), min_score: int = 2
) -> list[FileRisk]:
    """(path, content) 목록에서 코드 파일만, 테스트 경로 제외, 점수 내림차순 상위 top_k."""
    kws = list(name_keywords)
    scored = [score_file(p, c, kws) for p, c in files if p.endswith(CODE_EXT) and not TEST_PATH.search(p)]
    ranked = sorted((r for r in scored if r.content_score >= min_score), key=lambda r: (-r.score, r.path))
    return ranked[:top_k]
