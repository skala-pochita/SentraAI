"""속도 개선 벤치마크 (workHistory/Todo.md 2장 B1~B6).

외부 의존성(LLM·MCP·Redis·DB·대상 에이전트)의 응답 지연을 고정값으로 재현하고,
같은 입력에 대해 개선 전 동작과 개선 후 동작의 실행 시간을 잰다. LLM 키·Docker 없이 재현 가능하다.

실행: cd backend && uv run python -m harness.eval.bench_speed [--repeat 3] [--out ../reports/bench_speed.md]

지연값은 실측 비율을 보존하도록 축소했다 (예: LLM 1회 0.4초). 절대 시간보다 **개선 전후 비율**을 본다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

LLM_LATENCY_S = 0.4  # gpt-5.4-nano 1회 호출 (축소)
MCP_LATENCY_S = 0.05  # business-mcp 도구 1회 (세션 재사용 시)


@dataclass
class Result:
    key: str
    title: str
    workload: str
    before_s: float
    after_s: float
    before_label: str
    after_label: str

    @property
    def speedup(self) -> float:
        return self.before_s / self.after_s if self.after_s else float("inf")

    @property
    def reduction(self) -> float:
        return 1 - self.after_s / self.before_s if self.before_s else 0.0


SCENARIOS: dict[str, Callable[[], Awaitable[Result]]] = {}


def scenario(fn: Callable[[], Awaitable[Result]]) -> Callable[[], Awaitable[Result]]:
    SCENARIOS[fn.__name__] = fn
    return fn


async def timed(coro: Awaitable[Any]) -> float:
    t = time.perf_counter()
    await coro
    return time.perf_counter() - t


# ---------------- B1: 파일 단위 ReAct 병렬화 ----------------


class _SlowReactModel:
    """파일마다 read_repo_file 1스텝 → final. 호출마다 LLM_LATENCY_S."""

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        await asyncio.sleep(LLM_LATENCY_S)
        human = str(messages[-1].content)
        if "read_repo_file:" in human:  # 이미 읽었다 → 판정
            body: dict[str, Any] = {"why": "", "action": None, "final": {"verdicts": [], "new_findings": []}}
        else:
            path = human.split("조사 파일:", 1)[1].split()[0] if "조사 파일:" in human else "x"
            body = {"why": "read", "action": {"tool": "read_repo_file", "args": {"path": path}}, "final": None}
        msg = AIMessage(content=json.dumps(body, ensure_ascii=False))
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        return msg


class _SlowRepoGateway:
    async def call(self, tool: str, args: dict[str, Any], ctx: Any) -> Any:
        if tool == "related_files":  # 벤치마크는 B1 비교용이라 관련 파일 조회는 즉시 빈 목록
            return []
        await asyncio.sleep(MCP_LATENCY_S)
        return {"path": args.get("path"), "content_masked": "class A {}\n" * 20, "total_lines": 20}


async def _react_run(concurrency: int, files: int) -> float:
    import core.graph.nodes.react_investigator as ri
    from core.graph.deps import Deps

    real = ri.limits

    def patched() -> dict[str, Any]:
        cfg = real()
        return {**cfg, "react": {**cfg["react"], "concurrency": concurrency}}

    ri.limits = patched  # type: ignore[assignment]
    try:
        deps = Deps(gateway=_SlowRepoGateway(), emit_events=False)  # type: ignore[arg-type]
        risky = [f"src/main/java/demo/File{i}Service.java" for i in range(files)]
        st = {
            "audit_id": "",
            "target": {"target_id": "t"},
            "candidates": [],
            "plan": {"repos": {"t": {"risky_files": risky}}},
        }
        return await timed(ri.investigate(st, {"configurable": {"deps": deps}}, (_SlowReactModel(), "bench")))
    finally:
        ri.limits = real  # type: ignore[assignment]


@scenario
async def b1_react_parallel() -> Result:
    files = 10
    return Result(
        "B1",
        "파일 단위 ReAct 병렬 조사",
        f"파일 {files}개 × (LLM 2회 + MCP 1회), LLM {LLM_LATENCY_S}s·MCP {MCP_LATENCY_S}s",
        await _react_run(1, files),
        await _react_run(4, files),
        "순차 (concurrency=1)",
        "병렬 (concurrency=4)",
    )


# ---------------- B2: MCP 세션 재사용 ----------------

HTTP_RTT_S = 0.005  # 컨테이너 간 HTTP 왕복 1회 (요청마다 서버 쪽에서 지연 재현)


class _CountingRTT:
    """HTTP 요청 수를 세고 요청마다 RTT 를 더하는 ASGI 미들웨어."""

    def __init__(self, app: Any, rtt_s: float) -> None:
        self.app, self.rtt_s, self.requests = app, rtt_s, 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            self.requests += 1
            await asyncio.sleep(self.rtt_s)
        await self.app(scope, receive, send)


async def start_echo_mcp(rtt_s: float = HTTP_RTT_S) -> tuple[Any, _CountingRTT, str]:
    """실제 streamable-http MCP 서버(business-mcp 와 같은 설정)를 임의 포트로 띄운다. (server, counter, url)."""
    import socket

    import uvicorn

    from mcp_servers.common import ServiceTokenMiddleware, create_server

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = create_server("bench-mcp", port, "bench")

    @srv.tool()
    async def ping(n: int = 0) -> str:
        return json.dumps({"ok": True, "data": {"n": n}})

    counter = _CountingRTT(ServiceTokenMiddleware(srv.streamable_http_app()), rtt_s)
    server = uvicorn.Server(uvicorn.Config(counter, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    server._bench_task = task  # type: ignore[attr-defined]
    return server, counter, f"http://127.0.0.1:{port}/mcp"


async def stop_echo_mcp(server: Any) -> None:
    server.should_exit = True
    await server._bench_task


async def _mcp_calls(reuse: bool, url: str, calls: int, sequential: bool = False) -> float:
    from core.mcp_client import CallCtx, HttpTransport
    from core.settings import get_settings

    get_settings().business_mcp_url = url  # type: ignore[misc]
    tr = HttpTransport(reuse=reuse)
    ctx = CallCtx(node="bench", actor_role="ADMIN", audit_id="bench")
    try:
        if sequential:  # ReAct 한 파일 안에서는 도구 호출이 순서대로 이어진다

            async def seq() -> None:
                for i in range(calls):
                    await tr.raw_call("business", "ping", {"n": i}, ctx, 30)

            return await timed(seq())
        return await timed(asyncio.gather(*(tr.raw_call("business", "ping", {"n": i}, ctx, 30) for i in range(calls))))
    finally:
        await tr.aclose()


@scenario
async def b2a_mcp_session_reuse_sequential() -> Result:
    calls = 60
    server, counter, url = await start_echo_mcp()
    try:
        counter.requests = 0
        before = await _mcp_calls(False, url, calls, sequential=True)
        req_before = counter.requests
        counter.requests = 0
        after = await _mcp_calls(True, url, calls, sequential=True)
        req_after = counter.requests
    finally:
        await stop_echo_mcp(server)
    return Result(
        "B2-a",
        "MCP 세션 재사용 (순차 호출)",
        f"실제 streamable-http MCP 호출 {calls}회(순차), HTTP 왕복 {HTTP_RTT_S * 1000:.0f}ms",
        before,
        after,
        f"호출마다 세션, HTTP {req_before}건",
        f"세션 재사용, HTTP {req_after}건",
    )


@scenario
async def b2_mcp_session_reuse() -> Result:
    calls = 60  # 감사 1건의 MCP 호출 수 규모 (intake·스캔·ReAct read·근거 재확인·create_finding)
    server, counter, url = await start_echo_mcp()
    try:
        counter.requests = 0
        before = await _mcp_calls(False, url, calls)
        req_before = counter.requests
        counter.requests = 0
        after = await _mcp_calls(True, url, calls)
        req_after = counter.requests
    finally:
        await stop_echo_mcp(server)
    return Result(
        "B2-b",
        "MCP 세션 재사용 (동시 호출)",
        f"실제 streamable-http MCP 호출 {calls}회(동시), HTTP 왕복 {HTTP_RTT_S * 1000:.0f}ms",
        before,
        after,
        f"호출마다 세션, HTTP {req_before}건",
        f"세션 재사용, HTTP {req_after}건",
    )


class _SaveGateway:
    async def call(self, tool: str, args: dict[str, Any], ctx: Any) -> Any:
        await asyncio.sleep(MCP_LATENCY_S)
        return {"finding_id": args["finding"]["temp_id"], "created": True}


async def _correlate_run(concurrency: int, n: int) -> float:
    import core.graph.nodes.correlate as cn
    from core.graph.deps import Deps

    findings = [
        {
            "temp_id": f"s-{i}",
            "target_id": "t",
            "source": "code",
            "category": "SQL_INJECTION",
            "rule_id": "SEC-SQLI-001",
            "title": "x",
            "severity": "HIGH",
            "confidence": 0.8,
            "verdict": "TRUE_POSITIVE",
            "reasoning_summary": "x",
            "evidence": [{"kind": "code", "file_path": f"F{i}.java", "line_start": 1, "line_end": 1}],
        }
        for i in range(n)
    ]
    real = cn.SAVE_CONCURRENCY
    cn.SAVE_CONCURRENCY = concurrency
    try:
        deps = Deps(gateway=_SaveGateway(), emit_events=False)  # type: ignore[arg-type]
        st = {"audit_id": "a", "code_findings": findings, "agent_findings": [], "targets": [], "plan": {}}
        return await timed(cn.correlate(st, {"configurable": {"deps": deps}}))
    finally:
        cn.SAVE_CONCURRENCY = real


@scenario
async def b2c_correlate_parallel_save() -> Result:
    n = 30
    return Result(
        "B2-c",
        "correlate Finding 동시 저장",
        f"Finding {n}건 create_finding, MCP {MCP_LATENCY_S}s",
        await _correlate_run(1, n),
        await _correlate_run(8, n),
        "순차 저장",
        "동시 저장 (8)",
    )


# ---------------- B3: 이벤트 비동기 일괄 기록 ----------------

REDIS_RTT_S = 0.001  # Redis 명령 1회 왕복
DB_TXN_S = 0.004  # DB 트랜잭션 1회 (세션 열기·insert·commit)
NODE_WORK_S = 0.001  # 이벤트 사이 노드의 실제 작업


class _LatencyRedis:
    """명령마다 RTT, pipeline 은 execute 1회에 RTT 1번."""

    async def _cmd(self, *a: Any, **k: Any) -> int:
        await asyncio.sleep(REDIS_RTT_S)
        return 1

    publish = xadd = expire = hset = _cmd

    def pipeline(self, transaction: bool = False) -> Any:
        outer = self

        class _P:
            def __getattr__(self, name: str) -> Any:
                return lambda *a, **k: self

            async def execute(self) -> list[Any]:
                await outer._cmd()
                return []

        return _P()


async def _legacy_emit(audit_id: str, type_: str, payload: dict[str, Any]) -> None:
    """개선 전 emit: Redis 명령 5회 + DB 트랜잭션 1회를 이벤트마다 순서대로 await."""
    from harness.events import make_event

    r = _LatencyRedis()
    ev = make_event(audit_id, type_, payload)
    data = json.dumps(ev, ensure_ascii=False, default=str)
    await r.publish(f"audit:{audit_id}:events", data)
    await r.xadd(f"audit:{audit_id}:stream", {"data": data})
    await r.expire(f"audit:{audit_id}:stream", 1)
    await r.hset(f"audit:{audit_id}:status", mapping={})
    await r.expire(f"audit:{audit_id}:status", 1)
    await asyncio.sleep(DB_TXN_S)


async def _events_run(buffered: bool, n: int) -> float:
    from core.redis.client import set_redis
    from harness import events as ev

    aid = "00000000-0000-0000-0000-0000000000be"
    real_db = ev._persist_db

    async def slow_db(evs: list[dict[str, Any]], rows: Any = None) -> None:
        await asyncio.sleep(DB_TXN_S)

    set_redis(_LatencyRedis())  # type: ignore[arg-type]
    ev._persist_db = slow_db  # type: ignore[assignment]
    try:

        async def work() -> None:
            for i in range(n):
                await asyncio.sleep(NODE_WORK_S)
                if buffered:
                    await ev.emit(aid, "investigate.step", {"i": i})
                else:
                    await _legacy_emit(aid, "investigate.step", {"i": i})

        async def run() -> None:
            if buffered:
                async with ev.buffered_events():  # 블록 종료 = 모든 이벤트 기록 완료까지 포함해서 잰다
                    await work()
            else:
                await work()

        return await timed(run())
    finally:
        ev._persist_db = real_db  # type: ignore[assignment]
        set_redis(None)


@scenario
async def b3_buffered_events() -> Result:
    n = 200
    return Result(
        "B3",
        "이벤트 비동기 일괄 기록",
        f"이벤트 {n}건 (사이마다 노드 작업 {NODE_WORK_S * 1000:.0f}ms), Redis {REDIS_RTT_S * 1000:.0f}ms·DB {DB_TXN_S * 1000:.0f}ms",
        await _events_run(False, n),
        await _events_run(True, n),
        "이벤트마다 Redis 5회+DB 1회 대기",
        "큐 + pipeline·묶음 insert",
    )


# ---------------- B4: probe LLM 판정 병렬화 ----------------


class _JudgeModel:
    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        await asyncio.sleep(LLM_LATENCY_S)
        msg = AIMessage(content=json.dumps({"attack_succeeded": False, "reasoning": "blocked"}))
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        return msg


async def _judge_run(concurrency: int, probes: int, attempts: int) -> float:
    import core.graph.nodes.probe_judge as pj
    from core.graph.deps import Deps
    from core.probes import load_probe_set

    ids = [p.id for p in load_probe_set().probes][:probes]
    results = [
        {
            "probe_id": pid,
            "category": "JAILBREAK",
            "attempts": attempts,
            "successes": 0,
            "uncertain": attempts,
            "asr": 0.0,
            "judged_by": "deterministic",
            "transcripts": [
                {"verdict": "UNDETERMINED", "reply_masked": "...", "tool_calls": [], "probe_run_id": None}
                for _ in range(attempts)
            ],
        }
        for pid in ids
    ]
    real = pj.limits
    pj.limits = lambda: {**real(), "probe": {**real().get("probe", {}), "judge_concurrency": concurrency}}  # type: ignore[assignment]
    try:
        deps = Deps(gateway=None, emit_events=False, llm=lambda n, e: (_JudgeModel(), "bench"))  # type: ignore[arg-type,return-value]
        st = {"target": {"target_id": "t"}, "probe_results": results, "agent_findings": []}
        return await timed(pj.probe_judge(st, {"configurable": {"deps": deps}}))
    finally:
        pj.limits = real  # type: ignore[assignment]


@scenario
async def b4_probe_judge_parallel() -> Result:
    probes, attempts = 6, 3
    return Result(
        "B4",
        "probe LLM 판정 병렬화",
        f"판정 불가 시도 {probes * attempts}건 (probe {probes}개 × {attempts}회), LLM {LLM_LATENCY_S}s",
        await _judge_run(1, probes, attempts),
        await _judge_run(4, probes, attempts),
        "순차 판정",
        "동시 판정 (4)",
    )


# ---------------- B5: probe 시도 병렬화 ----------------

AGENT_LATENCY_S = 0.6  # 대상 에이전트 1턴 응답 (자체 LLM 호출 포함)


async def _probe_run(concurrency: int, attempts: int) -> float:
    from types import SimpleNamespace

    import mcp_servers.business.tools.execute as ex
    from mcp_servers.common import CallContext, default_call

    target = SimpleNamespace(id="00000000-0000-0000-0000-00000000be05", config={}, ref="http://bench")
    saved = {
        k: getattr(ex, k)
        for k in ("get_target", "_sandbox_access", "_check_allowlist", "_converse", "attempt_concurrency")
    }

    async def get_target(tid: str, type_: str) -> Any:
        return target

    async def no_access(t: Any) -> None:
        return None

    async def converse(client: Any, t: Any, url: str, audit_id: str, probe: Any) -> dict[str, Any]:
        await asyncio.sleep(AGENT_LATENCY_S * len(probe.turns))
        return {"reply": "거절합니다", "tool_calls": []}

    ex.get_target, ex._sandbox_access, ex._converse = get_target, no_access, converse  # type: ignore[assignment]
    ex._check_allowlist = lambda url: url  # type: ignore[assignment]
    ex.attempt_concurrency = lambda: concurrency  # type: ignore[assignment]
    ex._overrides[target.id] = target  # type: ignore[assignment]  # DB 기록 생략 (기존 테스트 대역과 같은 경로)
    token = default_call.set(CallContext(None, "ADMIN", None, "bench"))
    try:
        pid = ex.load_probe_set().probes[0].id
        return await timed(ex.run_injection_probe(target_id="t", probe_id=pid, attempts=attempts))
    finally:
        default_call.reset(token)
        ex._overrides.pop(target.id, None)
        for k, v in saved.items():
            setattr(ex, k, v)


@scenario
async def b5_probe_attempts_parallel() -> Result:
    attempts = 3
    return Result(
        "B5",
        "probe 시도 병렬화",
        f"probe 1개 × 시도 {attempts}회, 대상 에이전트 응답 {AGENT_LATENCY_S}s/턴",
        await _probe_run(1, attempts),
        await _probe_run(3, attempts),
        "시도 순차",
        "시도 동시 (3)",
    )


# ---------------- B6: 워커 1개의 감사 동시 처리 ----------------

AUDIT_S = 1.0  # 감사 1건 처리 시간 (축소)


class _PartitionConsumer:
    def __init__(self, logs: dict[str, int]) -> None:
        from types import SimpleNamespace

        self.logs = {tp: [SimpleNamespace(offset=i, value=f"{tp}-{i}") for i in range(n)] for tp, n in logs.items()}
        self.pos = dict.fromkeys(logs, 0)
        self.paused: set[str] = set()

    def assignment(self) -> set[str]:
        return set(self.logs)

    def pause(self, *tps: str) -> None:
        self.paused |= set(tps)

    def resume(self, *tps: str) -> None:
        self.paused -= set(tps)

    def seek(self, tp: str, offset: int) -> None:
        self.pos[tp] = offset

    async def commit(self, offsets: dict[str, int]) -> None:
        return None

    async def getmany(self, timeout_ms: int, max_records: int) -> dict[str, list[Any]]:
        await asyncio.sleep(0.005)
        out: dict[str, list[Any]] = {}
        for tp in sorted(self.logs):
            if tp in self.paused or sum(len(v) for v in out.values()) >= max_records:
                continue
            if recs := self.logs[tp][self.pos[tp] : self.pos[tp] + 1]:
                out[tp] = recs
                self.pos[tp] += 1
        return out


async def _worker_run(concurrency: int, audits: int) -> float:
    from core.kafka import consumer as kc

    done = 0
    stop = asyncio.Event()

    async def handle(rec: Any, handler: Any) -> None:
        nonlocal done
        await asyncio.sleep(AUDIT_S)
        done += 1
        if done == audits:
            stop.set()

    real = kc.handle_record
    kc.handle_record = handle  # type: ignore[assignment]
    try:
        c = _PartitionConsumer({f"p{i}": 1 for i in range(audits)})
        return await timed(kc.consume_loop(c, lambda m: None, concurrency, stop=stop))  # type: ignore[arg-type,return-value]
    finally:
        kc.handle_record = real  # type: ignore[assignment]


@scenario
async def b6_worker_concurrency() -> Result:
    audits = 3
    return Result(
        "B6",
        "워커 1개의 감사 동시 처리",
        f"감사 {audits}건이 파티션 {audits}개에 동시 도착, 감사 1건 {AUDIT_S}s",
        await _worker_run(1, audits),
        await _worker_run(3, audits),
        "1건씩 처리",
        "파티션별 동시 (3)",
    )


# ---------------- B7: trace span 묶음 기록 ----------------


async def _spans_run(buffered: bool, n: int) -> float:
    from contextlib import asynccontextmanager

    from harness import events as ev
    from harness import observability as ob

    class _SlowSession:
        def add(self, row: Any) -> None:
            return None

        def add_all(self, rows: Any) -> None:
            return None

    @asynccontextmanager
    async def slow_scope() -> Any:
        await asyncio.sleep(DB_TXN_S)  # 세션 열기·commit
        yield _SlowSession()

    real_scope, real_db = ob.session_scope, ev._persist_db

    async def slow_db(evs: list[dict[str, Any]], rows: Any = None) -> None:
        await asyncio.sleep(DB_TXN_S)

    ob.session_scope = slow_scope  # type: ignore[assignment]
    ev._persist_db = slow_db  # type: ignore[assignment]
    ob.set_trace_context("00000000-0000-0000-0000-0000000000b7")
    try:

        async def work() -> None:
            for _ in range(n):
                await asyncio.sleep(NODE_WORK_S)
                async with ob.span("tool", "business.read_repo_file"):
                    pass

        async def run() -> None:
            if buffered:
                async with ev.buffered_events():
                    await work()
            else:
                await work()

        return await timed(run())
    finally:
        ob.session_scope, ev._persist_db = real_scope, real_db  # type: ignore[assignment]
        ob.set_trace_context(None)


@scenario
async def b7_buffered_spans() -> Result:
    n = 300
    return Result(
        "B7",
        "trace span 묶음 기록",
        f"span {n}건 (노드·LLM·도구 호출마다 1건), DB {DB_TXN_S * 1000:.0f}ms",
        await _spans_run(False, n),
        await _spans_run(True, n),
        "호출마다 DB 트랜잭션 대기",
        "이벤트 버퍼에 실어 묶음 insert",
    )


# ---------------- 실행·보고 ----------------


def render(results: list[list[Result]], repeat: int) -> str:
    lines = [
        "# 속도 개선 벤치마크",
        "",
        f"- 생성: `uv run python -m harness.eval.bench_speed --repeat {repeat}` · 반복 {repeat}회 중앙값",
        f"- 지연 재현값: LLM 1회 {LLM_LATENCY_S}s, MCP 도구 1회 {MCP_LATENCY_S}s (실측 비율 유지용 축소값)",
        "",
        "| 항목 | 개선 내용 | 작업량 | 개선 전 | 개선 후 | 단축 | 배속 |",
        "|---|---|---|---|---|---|---|",
    ]
    for runs in results:
        r = runs[0]
        b = statistics.median(x.before_s for x in runs)
        a = statistics.median(x.after_s for x in runs)
        lines.append(
            f"| {r.key} | {r.title} | {r.workload} | {b:.2f}s ({r.before_label}) | {a:.2f}s ({r.after_label}) "
            f"| -{(1 - a / b) * 100:.0f}% | ×{b / a:.1f} |"
        )
    return "\n".join(lines) + "\n"


async def main_async(repeat: int, only: list[str] | None) -> list[list[Result]]:
    out = []
    for name, fn in SCENARIOS.items():
        if only and not any(name.startswith(o.lower()) for o in only):
            continue
        out.append([await fn() for _ in range(repeat)])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    md = render(asyncio.run(main_async(a.repeat, a.only)), a.repeat)
    print(md)
    if a.out:
        a.out.write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
