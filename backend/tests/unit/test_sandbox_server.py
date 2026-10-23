"""sandbox-runner 컨트롤러 (Docker SDK 대역). 실제 Docker 검증은 scripts/verify_uploaded_runtime.py.

빌드 컨텍스트 구성(카나리·fixture·비루트), 격리 옵션, 인증, 체크섬, 동시 실행 제한, atlas/auditor 채팅 정규화,
실패 시 정리를 확인한다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.settings import REPO_ROOT, get_settings
from sandbox import server

ZIP = (REPO_ROOT / "artifacts" / "leak-validation.zip").read_bytes()
TOKEN = "sandbox-test-token"


class Obj:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class FakeContainer:
    def __init__(self, docker: FakeDocker, image: str, kwargs: dict[str, Any]) -> None:
        self.docker, self.image, self.kwargs = docker, image, kwargs
        self.labels = kwargs.get("labels", {})
        self.id = f"c{len(docker.containers.items)}"
        self.started = False

    def start(self) -> None:
        self.started = True

    def remove(self, force: bool = False, v: bool = False) -> None:
        self.docker.removed.append(self.id)

    def exec_run(self, cmd: list[str], user: str) -> Obj:
        req = json.loads(base64.b64decode(cmd[-1]))
        if isinstance(req, list):
            return Obj(exit_code=0, output=json.dumps({"ready": self.docker.service_ports_ready}).encode())
        self.docker.http.append(req)
        path = req["url"].split(":8000", 1)[1]
        reply = self.docker.replies.get(path, {"status": 200, "data": {}})
        return Obj(exit_code=0, output=json.dumps(reply).encode())


class Containers:
    def __init__(self, docker: FakeDocker) -> None:
        self.docker = docker
        self.items: list[FakeContainer] = []

    def create(self, image: str, *args: Any, **kwargs: Any) -> FakeContainer:
        c = FakeContainer(self.docker, image, kwargs)
        self.items.append(c)
        return c

    def run(self, image: str, *args: Any, **kwargs: Any) -> FakeContainer:
        c = self.create(image, **kwargs)
        c.start()
        return c

    def list(self, all: bool = False, filters: dict[str, Any] | None = None) -> list[FakeContainer]:
        labels = (filters or {}).get("label", [])
        if isinstance(labels, str):
            labels = [labels]
        return [c for c in self.items if not any(
            (c.labels.get(key) != value if sep else key not in c.labels)
            for key, sep, value in (item.partition("=") for item in labels)
        )]


class FakeDocker:
    def __init__(self) -> None:
        self.containers = Containers(self)
        self.removed: list[str] = []
        self.http: list[dict[str, Any]] = []
        self.service_ports_ready = True
        self.builds: list[bytes] = []
        self.replies: dict[str, Any] = {"/health": {"status": 200, "data": {"mode": "mock"}}}
        self.build_events: list[dict[str, Any]] | None = None
        self.networks = Obj(create=self._net, list=lambda filters=None: [])
        self.images = Obj(list=lambda filters=None: [], remove=lambda *a, **k: None)
        self.api = Obj(build=self._build, create_endpoint_config=lambda aliases: {"aliases": aliases})

    def _net(self, name: str, internal: bool, labels: dict[str, str]) -> Obj:
        assert internal is True
        return Obj(name=name, connect=lambda c, aliases: None, remove=lambda: None)

    def _build(self, fileobj: io.BytesIO, **kw: Any) -> Iterator[dict[str, Any]]:
        self.builds.append(fileobj.getvalue())
        yield from self.build_events or [{"stream": "Step 1/1"}, {"aux": {"ID": f"sha256:img{len(self.builds)}"}}]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeDocker]:
    d = FakeDocker()
    monkeypatch.setenv("MCP_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(server, "client", lambda: d)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    server.active.clear()
    yield d
    server.active.clear()
    get_settings.cache_clear()


def body(aid: str, data: bytes = ZIP, **extra: Any) -> dict[str, Any]:
    return {
        "audit_id": aid,
        "project_zip": base64.b64encode(data).decode(),
        "sha256": hashlib.sha256(data).hexdigest(),
        **extra,
    }


def api() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://sandbox")


AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def test_start_builds_isolated_containers_and_cleans_up(fake: FakeDocker) -> None:
    aid = str(uuid.uuid4())
    async with api() as c:
        assert (await c.get("/health")).json() == {"status": "ok"}
        assert (await c.post("/start", json=body(aid))).status_code == 401
        res = await c.post("/start", json=body(aid, proxy={"url": "http://llm-proxy:8400/v1", "token": "grant"}), headers=AUTH)
        assert res.status_code == 200, res.text
        assert res.json() == {"status": "READY", "mode": "mock"}

    # 서비스 2개 빌드: Dockerfile 끝에 비루트 USER, fixture COPY, 일회성 카나리. .env 는 넣지 않는다
    assert len(fake.builds) == 2
    with tarfile.open(fileobj=io.BytesIO(fake.builds[1])) as tar:
        names = set(tar.getnames())
        dockerfile = tar.extractfile("agent/Dockerfile")
        assert dockerfile is not None
        text = dockerfile.read().decode()
    assert text.rstrip().endswith("USER 10001:10001") and '"/lab/fixtures"' in text
    assert "fixtures/private/__audit_canary__.txt" in names
    assert not any(n.endswith("/.env") or n == ".env" for n in names)

    services = [c for c in fake.containers.items if c.image.startswith("sha256:")]
    assert len(services) == 2
    for c in services:
        k = c.kwargs
        assert k["user"] == "10001:10001" and k["read_only"] and k["cap_drop"] == ["ALL"]
        assert k["security_opt"] == ["no-new-privileges:true"] and k["mem_limit"] == "768m"
        assert k["network"] == f"audit-{aid}" and "ports" not in k and "volumes" not in k
    relay = next(c for c in fake.containers.items if c.kwargs.get("name", "").startswith("relay-"))
    assert relay.kwargs["environment"]["AUDIT_PROXY_TOKEN"] == "grant" and relay.started

    async with api() as c:
        # 같은 감사로 다시 시작하면 이전 컨테이너(서비스 2 + relay + helper)를 지우고 새로 빌드한다
        first = {x.id for x in fake.containers.items}
        assert (await c.post("/start", json=body(aid), headers=AUTH)).json()["status"] == "READY"
        assert len(fake.builds) == 4 and first <= set(fake.removed)
        assert (await c.post(f"/stop/{aid}", headers=AUTH)).json() == {"status": "STOPPED"}
    assert aid not in server.active and {x.id for x in fake.containers.items} <= set(fake.removed)


async def test_chat_protocols_normalize_replies(fake: FakeDocker) -> None:
    aid = str(uuid.uuid4())
    fake.replies["/sessions"] = {"status": 200, "data": {"id": "s-1"}}
    fake.replies["/chat"] = {
        "status": 200,
        "data": {
            "answer": "done",
            "mode": "mock",
            "trace": [
                {"name": "read_file", "arguments": {"path": "a"}, "output": {"content": "x"}},
                {"name": "read_file", "arguments": {"path": "../b"}, "output": {"error": "denied"}},
            ],
        },
    }
    async with api() as c:
        assert (await c.post("/start", json=body(aid), headers=AUTH)).status_code == 200
        msg = {"audit_id": aid, "session_id": "probe-1", "message": "hi"}
        out = (await c.post("/chat", json=msg, headers=AUTH)).json()
        assert out["reply"] == "done" and [t["executed"] for t in out["tool_calls"]] == [True, False]
        await c.post("/chat", json=msg, headers=AUTH)  # 같은 세션은 재사용
        assert sum(1 for r in fake.http if r["url"].endswith("/sessions")) == 1

        fake.replies["/chat"] = {"status": 200, "data": {"reply": "raw", "tool_calls": []}}
        raw = await c.post("/chat", json={**msg, "protocol": "auditor", "kb_docs": ["d"]}, headers=AUTH)
        assert raw.json() == {"reply": "raw", "tool_calls": []}
        assert fake.http[-1]["body"]["kb_docs"] == ["d"]

        fake.replies["/chat"] = {"status": 500, "data": {}}
        assert (await c.post("/chat", json=msg, headers=AUTH)).status_code == 502
        assert (await c.post("/chat", json={**msg, "audit_id": "nope"}, headers=AUTH)).status_code == 502


async def test_rejects_bad_input_and_concurrent_audits(fake: FakeDocker) -> None:
    async with api() as c:
        bad = body(str(uuid.uuid4()))
        bad["sha256"] = "0" * 64
        res = await c.post("/start", json=bad, headers=AUTH)
        assert res.status_code == 422 and "격리 실행 실패" in res.text
        assert (await c.post("/start", json=body(str(uuid.uuid4()), data=b"not zip"), headers=AUTH)).status_code == 422

        first = str(uuid.uuid4())
        assert (await c.post("/start", json=body(first), headers=AUTH)).status_code == 200
        busy = await c.post("/start", json=body(str(uuid.uuid4())), headers=AUTH)
        # 한 번에 프로젝트 하나. 일시적이라 409(재시도 대상)
        assert busy.status_code == 409 and "another sandbox audit is running" in busy.json()["error"]["message"]
        await c.post(f"/stop/{first}", headers=AUTH)

        fake.build_events = [{"error": "boom"}]
        failed = str(uuid.uuid4())
        res = await c.post("/start", json=body(failed), headers=AUTH)
        assert res.status_code == 422
        assert res.json()["error"]["message"] == "격리 실행 실패: image build for backend failed: boom"
        assert failed not in server.active  # 실패하면 정리

        fake.build_events = [{"stream": "Successfully built abc123"}]
        fake.replies["/health"] = {"status": 503, "data": {}}
        slow = str(uuid.uuid4())
        assert (await c.post("/start", json=body(slow), headers=AUTH)).status_code == 422  # health 시간 초과
        assert slow not in server.active

    with pytest.raises(ValueError):
        server.start(str(uuid.uuid4()), "/etc")  # 업로드 경로 밖
    with pytest.raises(RuntimeError):
        server.request("missing", "/health")
    server.active["x"] = {"helper": None, "url": "http://agent:8000"}
    with pytest.raises(ValueError):
        server.request("x", "/admin")


async def test_build_failure_reports_build_tool_cause(fake: FakeDocker) -> None:
    # 소스가 빠져 Spring Boot repackage 가 실패한 실제 사례(leak, lab/target 패키지 누락)의 Maven 출력
    maven = [
        "[INFO] BUILD FAILURE",
        "[ERROR] Failed to execute goal org.springframework.boot:spring-boot-maven-plugin:3.5.7:repackage "
        "(repackage) on project target-backend: Unable to find main class -> [Help 1]",
        "[ERROR] ",
        "[ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.",
        "[ERROR] [Help 1] http://cwiki.apache.org/confluence/display/MAVEN/MojoExecutionException",
    ]
    fake.build_events = [{"stream": line + "\n"} for line in maven] + [
        {"error": "The command '/bin/sh -c mvn -B package' returned a non-zero code: 1"}
    ]
    async with api() as c:
        res = await c.post("/start", json=body(str(uuid.uuid4())), headers=AUTH)
    message = res.json()["error"]["message"]
    assert res.status_code == 422
    assert message.startswith("격리 실행 실패: image build for backend failed: The command '/bin/sh -c mvn -B package'")
    assert "Unable to find main class" in message and "To see the full stack trace" not in message


def test_build_failure_without_tool_error_line_keeps_docker_message() -> None:
    assert server.build_failure("agent", "returned a non-zero code: 2", ["Step 3/5 : RUN make", "compiling ErrorView.ts"]) == (
        "image build for agent failed: returned a non-zero code: 2"
    )
    pip = ["Collecting nope==9.9", "ERROR: No matching distribution found for nope==9.9"]
    assert "(ERROR: No matching distribution found for nope==9.9)" in server.build_failure("agent", "code: 1", pip)


async def test_agent_health_alone_does_not_mark_backend_ready(fake: FakeDocker) -> None:
    fake.service_ports_ready = False
    aid = str(uuid.uuid4())
    async with api() as c:
        response = await c.post("/start", json=body(aid), headers=AUTH)
    assert response.status_code == 422
    assert "health check timed out" in response.json()["error"]["message"]
    assert aid not in server.active


def test_maven_dependency_failure_reports_fatal_cause() -> None:
    recent = [
        "[ERROR] [ERROR] Some problems were encountered while processing the POMs:",
        "[FATAL] Non-resolvable parent POM: dependency repository unavailable",
        "[ERROR] The build could not read 1 project",
    ]
    message = server.build_failure("backend", "code: 1", recent)
    assert "repository unavailable" in message and "Some problems" not in message


async def test_sandbox_client_keeps_reason_and_only_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import sandbox_client
    from core.errors import AppError, ErrorCode

    replies = {
        "/start": httpx.Response(422, json={"error": {"code": "VALIDATION", "message": "격리 실행 실패: image build for backend failed: boom"}}),
        "/busy": httpx.Response(409, json={"error": {"code": "CONFLICT", "message": "격리 실행 대기: another sandbox audit is running"}}),
        "/bare": httpx.Response(500, text="oops"),
    }
    transport = httpx.MockTransport(lambda req: replies[req.url.path])
    real = httpx.AsyncClient
    monkeypatch.setattr(sandbox_client.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox-runner:8300")
    get_settings.cache_clear()

    with pytest.raises(AppError) as exc:
        await sandbox_client.sandbox_call("/start", {})
    assert exc.value.code == ErrorCode.VALIDATION and not exc.value.retryable  # 같은 프로젝트는 다시 해도 실패
    assert exc.value.message == "격리 실행 실패: image build for backend failed: boom"
    with pytest.raises(AppError) as exc:
        await sandbox_client.sandbox_call("/busy", {})
    assert exc.value.retryable and exc.value.message == "격리 실행 대기: another sandbox audit is running"
    with pytest.raises(AppError) as exc:
        await sandbox_client.sandbox_call("/bare", {})
    assert exc.value.retryable and exc.value.message == "격리 실행 실패: HTTP 500"
    get_settings.cache_clear()


def test_reaper_stops_only_expired(fake: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[str] = []
    monkeypatch.setattr(server, "stop", stopped.append)
    namespace = {server.NAMESPACE_LABEL: "ai-security-auditor"}
    fake.containers.create("x", labels={**namespace, server.LABEL: "old", "auditor.expires": "0"})
    fake.containers.create("x", labels={**namespace, server.LABEL: "new", "auditor.expires": "9999999999"})
    fake.containers.create("x", labels={server.NAMESPACE_LABEL: "another-stack", server.LABEL: "foreign", "auditor.expires": "0"})
    server.reap_expired()
    assert stopped == ["old"]


def test_cleanup_cannot_remove_another_stack_resources(fake: FakeDocker) -> None:
    aid = str(uuid.uuid4())
    own = fake.containers.create("own", labels={server.LABEL: aid, server.NAMESPACE_LABEL: "ai-security-auditor"})
    foreign = fake.containers.create("foreign", labels={server.LABEL: aid, server.NAMESPACE_LABEL: "another-stack"})
    server.stop(aid)
    assert own.id in fake.removed and foreign.id not in fake.removed
