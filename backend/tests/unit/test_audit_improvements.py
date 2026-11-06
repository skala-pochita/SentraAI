"""감사 파이프라인 개선 항목 회귀 테스트 (workHistory/Todo.md)."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from core.graph.deps import Deps
from core.graph.nodes.code_evidence_check import code_evidence_check


class FileGateway:
    """read_repo_file 만 흉내 내는 게이트웨이 (1회 400줄 제한 포함)."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool: str, args: dict[str, Any], ctx: Any) -> Any:
        self.calls.append((tool, args))
        assert tool == "read_repo_file"
        lines = self.files[args["path"]].splitlines()
        total = len(lines)
        s = max(1, args.get("start_line") or 1)
        e = min(total, args.get("end_line") or (s + 399), s + 399)
        return {"content_masked": "\n".join(lines[s - 1 : e]), "start_line": s, "end_line": e, "total_lines": total}


def _finding(path: str, line: int, snippet: str) -> dict[str, Any]:
    return {
        "temp_id": "t1",
        "verdict": "TRUE_POSITIVE",
        "confidence": 0.85,
        "reasoning_summary": "x",
        "evidence": [
            {"kind": "code", "file_path": path, "line_start": line, "line_end": line, "snippet_masked": snippet}
        ],
    }


# ---- P0-1: 400줄 넘는 파일의 근거 검증 ----


async def test_evidence_beyond_400_lines_is_verified() -> None:
    body = [f"int filler{i} = {i};" for i in range(1, 901)]
    body[449] = 'String q = "select * from t where id=" + id;'  # 450번째 줄
    gw = FileGateway({"Big.java": "\n".join(body)})
    deps = Deps(gateway=gw, emit_events=False)  # type: ignore[arg-type]
    st = {"target": {"target_id": "t"}, "investigated": [_finding("Big.java", 450, body[449])]}
    out = await code_evidence_check(st, {"configurable": {"deps": deps}})
    f = out["code_findings"][0]
    assert f["verdict"] == "TRUE_POSITIVE" and f["confidence"] == 0.85
    assert len(gw.calls) == 3  # 1-400, 401-800, 801-900


async def test_evidence_relocated_beyond_400_lines() -> None:
    body = [f"int filler{i} = {i};" for i in range(1, 701)]
    body[599] = "http.csrf().disable();"
    gw = FileGateway({"Sec.java": "\n".join(body)})
    deps = Deps(gateway=gw, emit_events=False)  # type: ignore[arg-type]
    st = {"target": {"target_id": "t"}, "investigated": [_finding("Sec.java", 120, "http.csrf().disable();")]}
    out = await code_evidence_check(st, {"configurable": {"deps": deps}})
    ev = out["code_findings"][0]["evidence"][0]
    assert ev["line_start"] == 600 and out["code_findings"][0]["verdict"] == "TRUE_POSITIVE"


# ---- P0-2: 긴 감사 중 대상 락 연장 ----


async def test_lock_heartbeat_extends_only_own_lock(fake_redis: Any) -> None:
    import asyncio
    import contextlib

    from core.redis.locks import acquire_target_lock, keep_locks_alive

    await acquire_target_lock("t-mine", "audit-1")
    await fake_redis.set("lock:audit:target:t-other", "audit-2", ex=5)
    await fake_redis.expire("lock:audit:target:t-mine", 2)
    task = asyncio.create_task(keep_locks_alive(["t-mine", "t-other"], "audit-1", interval_s=0.02, ttl_s=900))
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert await fake_redis.ttl("lock:audit:target:t-mine") > 800  # 내 락은 연장
    assert await fake_redis.ttl("lock:audit:target:t-other") <= 5  # 다른 감사의 락은 그대로


# ---- P0-3 · B6: 처리 중 파티션 pause + poll 유지, 파티션별 동시 처리 ----


class FakeConsumer:
    """파티션별 로그·위치·pause·seek·commit 을 흉내 낸다."""

    def __init__(self, logs: dict[str, list[str]]) -> None:
        from types import SimpleNamespace

        self.logs = {tp: [SimpleNamespace(offset=i, value=v) for i, v in enumerate(vs)] for tp, vs in logs.items()}
        self.pos = {tp: 0 for tp in logs}
        self.paused: set[str] = set()
        self.committed: dict[str, int] = {}
        self.polls = 0

    def assignment(self) -> set[str]:
        return set(self.logs)

    def pause(self, *tps: str) -> None:
        self.paused |= set(tps)

    def resume(self, *tps: str) -> None:
        self.paused -= set(tps)

    def seek(self, tp: str, offset: int) -> None:
        self.pos[tp] = offset

    async def commit(self, offsets: dict[str, int]) -> None:
        self.committed.update(offsets)

    async def getmany(self, timeout_ms: int, max_records: int) -> dict[str, list[Any]]:
        import asyncio

        self.polls += 1
        await asyncio.sleep(0.005)
        out: dict[str, list[Any]] = {}
        n = 0
        for tp in sorted(self.logs):  # pause 된 파티션은 돌려주지 않는다
            if tp in self.paused or n >= max_records:
                continue
            recs = self.logs[tp][self.pos[tp] : self.pos[tp] + 2]  # 한 번에 2건씩 (되감기 경로 검증)
            if recs:
                out[tp] = recs
                self.pos[tp] += len(recs)
                n += len(recs)
        return out


async def _consume(logs: dict[str, list[str]], concurrency: int, work_s: float) -> tuple[FakeConsumer, dict[str, Any]]:
    import asyncio

    from core.kafka import consumer as kc

    c = FakeConsumer(logs)
    stats: dict[str, Any] = {"live": 0, "peak": 0, "order": [], "paused_ok": True}
    total = sum(len(v) for v in logs.values())
    stop = asyncio.Event()

    async def handle(rec: Any, handler: Any) -> None:
        stats["live"] += 1
        stats["peak"] = max(stats["peak"], stats["live"])
        await asyncio.sleep(work_s)
        stats["order"].append(rec.value)
        stats["live"] -= 1
        if len(stats["order"]) == total:
            stop.set()

    real = kc.handle_record
    kc.handle_record = handle  # type: ignore[assignment]
    try:
        await asyncio.wait_for(kc.consume_loop(c, lambda m: None, concurrency, stop=stop), timeout=5)  # type: ignore[arg-type]
    finally:
        kc.handle_record = real  # type: ignore[assignment]
    return c, stats


async def test_consume_loop_parallel_partitions_in_order() -> None:
    logs = {"p0": ["a0", "a1"], "p1": ["b0", "b1"], "p2": ["c0", "c1"]}
    c, st = await _consume(logs, concurrency=2, work_s=0.05)
    assert st["peak"] == 2  # 동시 처리 상한
    for vals in logs.values():  # 같은 파티션 안에서는 순서대로
        assert [v for v in st["order"] if v in vals] == vals
    assert c.committed == {"p0": 2, "p1": 2, "p2": 2}  # 전부 커밋, 유실·중복 없음
    assert len(st["order"]) == 6
    assert c.polls >= 6  # 처리하는 동안에도 poll 계속 (P3: max_poll_interval 초과 리밸런스 없음)
    assert c.paused == set()


async def test_consume_loop_single_concurrency_is_sequential() -> None:
    _, st = await _consume({"p0": ["a0"], "p1": ["b0"], "p2": ["c0"]}, concurrency=1, work_s=0.02)
    assert st["peak"] == 1 and sorted(st["order"]) == ["a0", "b0", "c0"]


# ---- P0-4: 재개 시 예산 복원 ----


def test_budget_restore_from_token_usage() -> None:
    from harness.budget import Budget

    b = Budget(max_tokens=1000, max_cost_usd=1.0)
    b.restore([{"in": 400, "out": 300, "usd": 0.2}, {"in": 200, "out": 150, "usd": 0.1}])
    assert b.used_tokens == 1050 and round(b.used_usd, 3) == 0.3
    assert b.exceeded()  # 재개 후 첫 LLM 호출에서 바로 예산 초과로 막힌다


# ---- B1: 파일 단위 ReAct 병렬 조사 ----


async def test_react_files_run_concurrently_with_cap(monkeypatch: Any) -> None:
    import asyncio

    import core.graph.nodes.react_investigator as ri
    from harness.eval import bench_speed as bs

    monkeypatch.setattr(bs, "LLM_LATENCY_S", 0.02)
    monkeypatch.setattr(bs, "MCP_LATENCY_S", 0.0)
    live, peak = 0, 0
    real = ri._investigate_file

    async def counting(*a: Any, **k: Any) -> Any:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await asyncio.sleep(0.01)
            return await real(*a, **k)
        finally:
            live -= 1

    monkeypatch.setattr(ri, "_investigate_file", counting)
    real_limits = ri.limits
    monkeypatch.setattr(ri, "limits", lambda: {**real_limits(), "react": {**real_limits()["react"], "concurrency": 3}})
    deps = Deps(gateway=bs._SlowRepoGateway(), emit_events=False)  # type: ignore[arg-type]
    cands = [
        {
            "temp_id": f"s-{i}",
            "target_id": "t",
            "source": "code",
            "category": "SQL_INJECTION",
            "rule_id": "SEC-SQLI-001",
            "title": "x",
            "severity": "HIGH",
            "confidence": 0.6,
            "verdict": "NEEDS_REVIEW",
            "reasoning_summary": "x",
            "evidence": [{"kind": "code", "file_path": f"F{i}.java", "line_start": 1, "line_end": 1}],
        }
        for i in range(8)
    ]
    st = {"audit_id": "", "target": {"target_id": "t"}, "candidates": cands, "plan": {}}
    out = await ri.investigate(st, {"configurable": {"deps": deps}}, (bs._SlowReactModel(), "fake"))
    assert peak == 3  # 동시 실행 상한
    assert [f["temp_id"] for f in out["investigated"]] == [f"s-{i}" for i in range(8)]  # 순서 보존
    assert len(out["token_usage"]) == 16  # 파일 8개 × LLM 2회, 누락 없음


# ---- B2: MCP 세션 재사용 (실제 streamable-http 서버) ----


async def test_pooled_mcp_session_reuse_and_recovery(monkeypatch: Any) -> None:
    import asyncio

    from core.errors import AppError
    from core.mcp_client import CallCtx, HttpTransport
    from core.settings import get_settings
    from harness.eval.bench_speed import start_echo_mcp, stop_echo_mcp

    server, counter, url = await start_echo_mcp(rtt_s=0)
    monkeypatch.setattr(get_settings(), "business_mcp_url", url)
    tr = HttpTransport(reuse=True)
    ctx = CallCtx(node="t", actor_role="ADMIN", audit_id="a1")
    try:
        counter.requests = 0
        outs = await asyncio.gather(*(tr.raw_call("business", "ping", {"n": i}, ctx, 10) for i in range(10)))
        assert [json.loads(o)["data"]["n"] for o in outs] == list(range(10))
        assert len(tr._pool) == 1 and counter.requests <= 13  # 세션 1개 (initialize·list_tools + 호출 10)
        other = CallCtx(node="t", actor_role="DEVELOPER", audit_id="a2")  # 맥락 헤더가 다르면 세션도 따로
        await tr.raw_call("business", "ping", {}, other, 10)
        assert len(tr._pool) == 2

        # 세션이 끊기면 버리고 다음 호출에서 새로 연다
        pooled = next(iter(tr._pool.values()))
        await pooled.close()
        with contextlib.suppress(AppError):
            await tr.raw_call("business", "ping", {"n": 99}, ctx, 10)
        assert json.loads(await tr.raw_call("business", "ping", {"n": 7}, ctx, 10))["data"]["n"] == 7
    finally:
        await tr.aclose()
        await stop_echo_mcp(server)
    assert tr._pool == {}


# ---- B3: 이벤트 비동기 일괄 기록 ----


async def test_buffered_events_keep_order_and_flush_on_exit(fake_redis: Any, monkeypatch: Any) -> None:
    import asyncio

    from harness import events as ev

    persisted: list[str] = []

    async def fake_db(evs: list[dict[str, Any]], rows: Any = None) -> None:
        await asyncio.sleep(0.01)  # 느린 DB
        persisted.extend(e["type"] for e in evs)

    monkeypatch.setattr(ev, "_persist_db", fake_db)
    async with ev.buffered_events():
        for i in range(50):
            await ev.emit("00000000-0000-0000-0000-000000000001", f"step.{i}", {"i": i})
        await asyncio.gather(*(ev.emit("00000000-0000-0000-0000-000000000001", "child", {}) for _ in range(3)))
        assert len(persisted) < 53  # emit 은 기록을 기다리지 않고 돌아온다
    assert persisted[:50] == [f"step.{i}" for i in range(50)] and persisted[50:] == ["child"] * 3  # 순서·누락 없음
    stream = await fake_redis.xrange("audit:00000000-0000-0000-0000-000000000001:stream")
    assert len(stream) == 53
    status = await fake_redis.hgetall("audit:00000000-0000-0000-0000-000000000001:status")
    assert status["last_event"] == "child"
    # 블록 밖에서는 즉시 기록
    await ev.emit("00000000-0000-0000-0000-000000000001", "after", {})
    assert persisted[-1] == "after"


# ---- B4: probe LLM 판정 병렬화 ----


async def test_probe_judge_parallel_with_cap_and_same_counts(monkeypatch: Any) -> None:
    import asyncio

    from langchain_core.messages import AIMessage

    import core.graph.nodes.probe_judge as pj
    from core.probes import load_probe_set

    live, peak = 0, 0

    class Model:
        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.02)
            live -= 1
            ok = "SUCCESS-HINT" in str(messages[-1].content)
            return AIMessage(content=json.dumps({"attack_succeeded": ok, "reasoning": "r"}))

    ids = [p.id for p in load_probe_set().probes if p.category != "BENIGN_CONTROL"][:3]
    results = [
        {
            "probe_id": pid,
            "category": "JAILBREAK",
            "attempts": 3,
            "successes": 0,
            "uncertain": 3,
            "asr": 0.0,
            "judged_by": "deterministic",
            "run_ids": [],
            "transcripts": [
                {"verdict": "UNDETERMINED", "reply_masked": "SUCCESS-HINT" if k == 0 else "no", "tool_calls": []}
                for k in range(3)
            ],
        }
        for pid in ids
    ]
    real = pj.limits
    monkeypatch.setattr(pj, "limits", lambda: {**real(), "probe": {**real()["probe"], "judge_concurrency": 2}})
    deps = Deps(gateway=None, emit_events=False, llm=lambda n, e: (Model(), "fake"))  # type: ignore[arg-type]
    out = await pj.probe_judge(
        {"target": {"target_id": "t"}, "probe_results": results, "agent_findings": []}, {"configurable": {"deps": deps}}
    )
    assert peak == 2  # 동시 판정 상한
    assert [r["successes"] for r in out["probe_results"]] == [1, 1, 1]  # 시도마다 정확히 반영
    assert all(r["uncertain"] == 0 and r["asr"] == 0.333 for r in out["probe_results"])
    assert len(out["agent_findings"]) == 3


# ---- B5: probe 시도 병렬화 ----


async def test_probe_attempts_run_concurrently_in_attempt_order(monkeypatch: Any) -> None:
    import asyncio
    import time
    from types import SimpleNamespace

    import mcp_servers.business.tools.execute as ex
    from mcp_servers.common import CallContext, default_call

    target = SimpleNamespace(id="00000000-0000-0000-0000-0000000000b5", config={}, ref="http://x")
    delays = iter([0.15, 0.05, 0.10])

    async def get_target(tid: str, type_: str) -> Any:
        return target

    async def no_access(t: Any) -> None:
        return None

    async def converse(client: Any, t: Any, url: str, audit_id: str, probe: Any) -> dict[str, Any]:
        d = next(delays)
        await asyncio.sleep(d)
        return {"reply": f"r{d}", "tool_calls": []}

    monkeypatch.setattr(ex, "get_target", get_target)
    monkeypatch.setattr(ex, "_sandbox_access", no_access)
    monkeypatch.setattr(ex, "_check_allowlist", lambda u: u)
    monkeypatch.setattr(ex, "_converse", converse)
    monkeypatch.setitem(ex._overrides, target.id, target)
    token = default_call.set(CallContext(None, "ADMIN", None, "t"))
    try:
        started = time.monotonic()
        env = await ex.run_injection_probe(target_id="t", probe_id=ex.load_probe_set().probes[0].id, attempts=3)
    finally:
        default_call.reset(token)
    assert time.monotonic() - started < 0.25  # 순차면 0.3초
    atts = env["data"]["attempts"]
    assert [a["attempt"] for a in atts] == [1, 2, 3] and atts[0]["reply_masked"] == "r0.15"


# ---- S1: 규칙에 없는 카테고리도 LLM 새 발견으로 보고 ----


def test_new_finding_accepts_extra_categories_and_rejects_free_text() -> None:
    import pytest
    from pydantic import ValidationError

    from core.graph.nodes.react_investigator import Final, NewFinding, _new_findings

    base = {"title": "t", "severity": "CRITICAL", "file_path": "app.py", "line_start": 10, "snippet": "os.system(cmd)"}
    nf = NewFinding.model_validate({**base, "category": "COMMAND_INJECTION"})
    out = _new_findings(Final(new_findings=[nf]), "app.py", [], "t", ["read_repo_file"])
    assert out[0]["category"] == "COMMAND_INJECTION" and "CWE-78" in out[0]["standard_refs"]
    NewFinding.model_validate({**base, "category": "SQL_INJECTION"})  # 규칙 카테고리는 그대로 허용
    with pytest.raises(ValidationError):
        NewFinding.model_validate({**base, "category": "SOMETHING_BAD"})  # 자유 문자열은 거부


def test_category_definitions_include_extra_for_prompt() -> None:
    from core.categories import category_definitions, code_categories

    defs = category_definitions()
    assert {"COMMAND_INJECTION", "SSRF", "INSECURE_DESERIALIZATION", "SQL_INJECTION"} <= code_categories()
    assert "- SSRF:" in defs and "- SQL_INJECTION:" in defs


# ---- S4: 여러 줄 정규식 ----


def test_multiline_regex_matches_across_lines_and_skips_comments() -> None:
    from core.rules.detectors import detect_regex
    from core.rules.models import Rule

    rule = Rule(
        id="T-ML",
        name="t",
        category="SECCONFIG_CSRF",
        default_severity="MEDIUM",
        target="code",
        detector="regex",
        multiline=True,
        patterns=[r"csrf\(\s*(\w+)\s*->\s*\1\s*\.\s*disable\(\)", r"csrf\(\)\s*\.\s*disable\(\)"],
    )
    src = "http\n  .csrf(c -> c\n      .disable())\n  // http.csrf().disable();\n  ;\nhttp.csrf()\n   .disable();\n"
    hits = detect_regex(rule, "A.java", src)
    assert [h.line for h in hits] == [2, 6]
    single = rule.model_copy(update={"multiline": False})
    assert [h.line for h in detect_regex(single, "A.java", src)] == [4]  # 한 줄 모드: 주석 줄만 잡고 실제 2곳은 놓친다


# ---- S2·PY4: 위험 파일을 내용 신호로 선택 ----


def test_risky_files_by_content_not_name() -> None:
    from core.graph.nodes.intake import risky_files
    from core.risk_signals import rank_files

    files = [
        ("app/utils.py", "import subprocess\n\ndef run(c):\n    subprocess.run(c, shell=True)\n"),
        ("app/helpers.py", "import base64\nKEY = base64.b64decode('c2tfbGl2ZV9mYWtl')\n"),
        ("app/views.py", "@app.route('/x')\ndef x():\n    return request.args['q']\n"),
        ("app/PaymentConfig.py", "TIMEOUT = 3\n"),  # 이름만 위험해 보이는 파일
        ("tests/test_utils.py", "subprocess.run(c, shell=True)\n"),  # 테스트 경로 제외
        ("README.md", "eval(x)"),  # 비코드 제외
    ]
    kw = ["Config", "Payment"]
    assert risky_files([{"path": p} for p, _ in files], kw, 8) == ["app/PaymentConfig.py"]  # 이전: 이름만 본다
    picked = [r.path for r in rank_files(files, 8, kw)]
    assert picked[:3] == ["app/utils.py", "app/helpers.py", "app/views.py"] or set(picked[:3]) == {
        "app/utils.py",
        "app/helpers.py",
        "app/views.py",
    }
    assert "app/PaymentConfig.py" not in picked and "tests/test_utils.py" not in picked


def test_risky_selection_metrics_on_fixtures() -> None:
    from harness.eval.risky_selection import measure

    rows = {r["target"].split(" ")[0]: r for r in measure()}
    assert rows["vuln-spring-app"]["content_based"] == 2  # 정적 회피 C19·C20 파일 유지
    assert rows["fixture/python"]["content_based"] == rows["fixture/python"]["vulnerable_code_files"]
    assert rows["fixture/spring"]["content_based"] >= rows["fixture/spring"]["name_based"]


async def test_rank_risky_files_mcp_tool_registered_and_gated() -> None:
    from harness.tools import ToolGate, tool_annotations
    from mcp_servers.business.app import mcp

    assert "rank_risky_files" in {t.name for t in await mcp.list_tools()}
    ToolGate().check("intake", "rank_risky_files", "DEVELOPER")  # intake 에서 호출 허용
    assert tool_annotations("rank_risky_files")["readOnlyHint"] is True  # 조회 도구


# ---- PY5: 업로드 Python 에이전트의 도구 탐색 범위 ----

AGENT_STYLES = {
    "literal.py": 'TOOLS = [{"type": "function", "function": {"name": "read_file", "description": "Read"}}]\n',
    "lc.py": (
        "from langchain_core.tools import tool, Tool, StructuredTool\n"
        "@tool\ndef send_email(to: str, body: str) -> str:\n    '''Send an email'''\n    return 'ok'\n"
        "@tool('delete_user')\ndef _del(user_id: str) -> str:\n    return 'ok'\n"
        "def _refund(order_id):\n    return 'ok'\n"
        "refund = StructuredTool.from_function(func=_refund, name='refund_order', description='Refund')\n"
        "search = Tool(name='web_search', func=lambda q: q, description='Search the web')\n"
    ),
    "mcp_server.py": (
        "from mcp.server.fastmcp import FastMCP\nmcp = FastMCP('x')\n"
        "@mcp.tool()\nasync def run_shell(cmd: str, ctx=None) -> str:\n    '''Run a shell command'''\n    return cmd\n"
    ),
    "agents_sdk.py": "from agents import function_tool\n@function_tool\ndef transfer_money(amount: int) -> str:\n    return 'ok'\n",
    "not_a_tool.py": "import functools\n@functools.lru_cache\ndef helper(x):\n    return x\n",
}


def test_agent_discovery_finds_decorated_and_constructed_tools(tmp_path: Any) -> None:
    from core.agent_project import discover_agent

    for name, body in AGENT_STYLES.items():
        (tmp_path / name).write_text(body)
    profile = discover_agent(tmp_path)
    assert profile is not None
    names = {t["name"] for t in profile["manifest"]["tools"]}
    assert names == {
        "read_file",
        "send_email",
        "delete_user",
        "refund_order",
        "web_search",
        "run_shell",
        "transfer_money",
    }
    assert profile["discovery"] == "python_source_tools"
    assert profile["discovered_via"] == ["constructor", "decorator", "literal"]
    send = next(t for t in profile["manifest"]["tools"] if t["name"] == "send_email")
    assert send["description"] == "Send an email" and set(send["parameters"]["properties"]) == {"to", "body"}
    run = next(t for t in profile["manifest"]["tools"] if t["name"] == "run_shell")
    assert set(run["parameters"]["properties"]) == {"cmd"}  # ctx 제외


# ---- E1~E3: 외부 SAST·SCA ----


async def test_sca_local_advisories_on_fixture() -> None:
    from core.external_scanners import run_external
    from core.settings import REPO_ROOT

    res = await run_external(REPO_ROOT / "datasets/sca_fixture", ["sca"])
    got = {(h.rule_id, h.path, h.line) for h in res.hits}
    assert got == {
        ("SCA-CVE-2022-22965", "svc-java/build.gradle", 2),  # spring-webmvc 5.3.17
        ("SCA-CVE-2021-44228", "svc-java/pom.xml", 9),  # log4j ${property} 해석
        ("SCA-CVE-2022-42889", "svc-java/pom.xml", 14),  # commons-text 1.9
        ("SCA-CVE-2020-14343", "svc-py/requirements.txt", 2),
        ("SCA-CVE-2023-32681", "svc-py/requirements.txt", 3),
        ("SCA-CVE-2024-22195", "svc-py/requirements.txt", 4),  # 대소문자 무시 (Jinja2)
    }  # 안전 버전(snakeyaml 2.2, jackson 2.15.2, flask 3.0.3, urllib3 2.2.2)·버전 미지정은 탐지 안 함
    assert all(h.category == "VULNERABLE_DEPENDENCY" and h.extra["external"] for h in res.hits)
    assert res.status["sca_local"] == "COMPLETED" and res.status["sca_osv"] == "DISABLED"  # 외부 전송 기본 꺼짐


def test_version_ranges() -> None:
    from core.external_scanners import in_range

    spring = [{"fixed": "5.2.20"}, {"introduced": "5.3.0", "fixed": "5.3.18"}]
    assert in_range("5.3.17", spring) and in_range("5.2.19.RELEASE", spring)
    assert not in_range("5.3.18", spring) and not in_range("5.2.20", spring) and not in_range("6.1.0", spring)


async def test_bandit_real_run_is_mapped_and_filtered() -> None:
    import shutil

    import pytest

    from core.external_scanners import run_external
    from core.settings import REPO_ROOT

    if shutil.which("bandit") is None:
        pytest.skip("bandit not installed")
    res = await run_external(REPO_ROOT / "datasets/rule_fixtures/python", ["bandit"])
    assert res.status["bandit"] == "COMPLETED"
    ids = {h.rule_id for h in res.hits}
    assert "EXT-BANDIT-B608" in ids and "EXT-BANDIT-B301" in ids  # SQL·pickle
    assert not ids & {
        "EXT-BANDIT-B404",
        "EXT-BANDIT-B403",
        "EXT-BANDIT-B607",
        "EXT-BANDIT-B603",
    }  # import·LOW 잡음 제외
    assert all(not h.path.startswith(("/", ".")) for h in res.hits)  # root 기준 상대 경로


def test_semgrep_json_is_mapped_by_cwe() -> None:
    from core.external_scanners import parse_semgrep

    data = {
        "results": [
            {
                "check_id": "python.lang.security.audit.formatted-sql-query",
                "path": "app/db.py",
                "start": {"line": 9},
                "extra": {
                    "severity": "ERROR",
                    "lines": "cur.execute(f'...')",
                    "metadata": {"cwe": ["CWE-89: SQL Injection"]},
                },
            },
            {
                "check_id": "style.rule",
                "path": "a.py",
                "start": {"line": 1},
                "extra": {"metadata": {"cwe": ["CWE-1234"]}},
            },
        ]
    }
    hits = parse_semgrep(data)
    assert [(h.category, h.severity, h.line) for h in hits] == [("SQL_INJECTION", "HIGH", 9)]  # 매핑 안 되는 CWE 제외


async def test_missing_scanners_report_status_not_fail(monkeypatch: Any, tmp_path: Any) -> None:
    import shutil

    from core.external_scanners import run_external

    monkeypatch.setattr(shutil, "which", lambda name: None)
    (tmp_path / "a.py").write_text("print(1)\n")
    res = await run_external(tmp_path, ["bandit", "semgrep", "sca"])
    assert res.status["bandit"] == "NOT_INSTALLED" and res.status["semgrep"] == "NOT_INSTALLED"
    assert res.hits == []


def test_external_hits_deduped_against_internal() -> None:
    from core.graph.nodes.code_static_scan import _new_external_hits

    internal = [{"path": "a.py", "category": "SQL_INJECTION", "line": 10}]
    external = [
        {"path": "a.py", "category": "SQL_INJECTION", "line": 11},  # 내장 규칙과 같은 위치 → 버림
        {"path": "a.py", "category": "WEAK_CRYPTO", "line": 11},  # 다른 카테고리 → 유지
        {"path": "a.py", "category": "WEAK_CRYPTO", "line": 12},  # 외부끼리 중복 → 버림
    ]
    assert [(h["category"], h["line"]) for h in _new_external_hits(internal, external)] == [("WEAK_CRYPTO", 11)]


# ---- B7: trace span 도 이벤트 버퍼로 묶어서 기록 ----


async def test_trace_spans_buffered_with_events(fake_redis: Any, monkeypatch: Any) -> None:
    from harness import events as ev
    from harness import observability as ob

    written: list[tuple[int, int]] = []

    async def fake_db(evs: list[dict[str, Any]], rows: Any = None) -> None:
        written.append((len([e for e in evs if "__flush__" not in e]), len(rows or [])))

    monkeypatch.setattr(ev, "_persist_db", fake_db)
    ob.set_trace_context("00000000-0000-0000-0000-0000000000b7")
    try:
        async with ev.buffered_events() as buf:
            for _ in range(5):
                async with ob.span("tool", "business.read_repo_file"):
                    pass
            assert written == []  # span 이 감사 실행을 막고 DB 에 쓰지 않는다
            await buf.flush()
            assert sum(r for _, r in written) == 5  # flush 로 묶어서 기록
            async with ob.span("node", "report"):
                pass
        assert sum(r for _, r in written) == 6  # 블록을 나갈 때 남은 행까지
    finally:
        ob.set_trace_context(None)


# ---- EV4: 감사 보고서의 노드·도구별 소요 시간 ----


async def test_latency_breakdown_and_report_section(db_ready: None) -> None:
    import uuid as _uuid
    from datetime import UTC, datetime

    from api.seed import seed_users
    from core.db.models import Audit, TraceSpan
    from core.db.session import session_scope
    from core.graph.nodes.report import render_md
    from harness.observability import latency_breakdown

    aid = _uuid.uuid4()
    async with session_scope() as db:
        users = await seed_users(db)
        db.add(Audit(id=aid, requested_by=users["ADMIN"].id, mode="code", status="RUNNING", thread_id=f"audit:{aid}"))
        await db.flush()
        now = datetime.now(UTC)
        for kind, name, ms in [
            ("node", "code_investigate", 900),
            ("node", "code_investigate", 300),
            ("tool", "business.read_repo_file", 40),
            ("llm", "code_investigate", 700),
        ]:
            db.add(
                TraceSpan(
                    id=_uuid.uuid4(), audit_id=aid, kind=kind, name=name, latency_ms=ms, status="ok", started_at=now
                )
            )
    t = await latency_breakdown(str(aid))
    assert t["span_count"] == 4
    assert t["node"] == [{"name": "code_investigate", "calls": 2, "total_ms": 1200, "max_ms": 900, "tokens": 0}]
    md = render_md(
        {
            "audit_id": str(aid),
            "generated_at": "x",
            "targets": [],
            "summary": {"total": 0, "by_severity": {}, "by_source": {}, "by_status": {}, "vulnerable_probes": 0},
            "agent_coverage": [],
            "correlation_gaps": [],
            "risk_paths": [],
            "findings": [],
            "probes": [],
            "tokens": {"input": 0, "output": 0, "usd": 0},
            "timings": t,
        }
    )
    assert "## 소요 시간 (노드)" in md and "| code_investigate | 2 | 1200 | 900 |" in md


# ---- EV1·EV2: 대상별 코드 감사 평가 ----


async def test_code_target_eval_static_configs(db_ready: None) -> None:
    from harness.eval.code_target_eval import run

    rep = await run("vuln-python-app", "code_python.yaml", ["S", "SX"], 1)
    s, sx = rep["results"]["S"][0], rep["results"]["SX"][0]
    assert rep["evasion_ids"] == ["PY13", "PY14"]
    assert s["recall"] == 0.75 and s["fp"] == 0 and s["injection_robustness"] == 1.0
    assert "PY16" not in sx["fn"]  # SCA 가 취약 PyYAML 을 찾는다
    assert sx["decoy_false_positives"] == ["PD03"]  # Bandit 의 체크섬 md5 — LLM 검토가 필요한 이유
