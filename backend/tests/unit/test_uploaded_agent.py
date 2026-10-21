from pathlib import Path

import pytest

from core.agent_project import discover_agent
from core.errors import AppError
from core.project_probes import uploaded_probe_set
from core.project_upload import extract_project
from core.rules.engine import iter_repo_files
from core.runtime_spec import runtime_spec
from tests.unit.test_project_upload import archive


def test_java_target_package_survives_but_build_output_excluded(tmp_path: Path) -> None:
    root = extract_project(
        archive(
            {
                "project/backend/src/main/java/lab/target/App.java": "class App {}",
                "project/backend/target/classes/App.class": "compiled",
            }
        ),
        tmp_path / "upload",
    )
    assert [rel for rel, _ in iter_repo_files(root)] == ["backend/src/main/java/lab/target/App.java"]


def test_literal_agent_discovery_does_not_execute_code(tmp_path: Path) -> None:
    (tmp_path / "tools.py").write_text(
        'raise RuntimeError("never execute")\nTOOL_SCHEMAS = [{"type":"function","function":{"name":"read_file","description":"Read"}}]\nSYSTEM = "credential %s" % config.SERVICE_TOKEN\n'
    )
    profile = discover_agent(tmp_path)
    assert profile["manifest"]["tools"][0]["name"] == "read_file"
    assert profile["manifest"]["tools"][0]["policy_status"] == "UNKNOWN"
    assert {f["category"] for f in profile["source_findings"]} == {
        "TOOL_ACCESS_POLICY_UNKNOWN",
        "PROMPT_SECRET_EMBEDDED",
    }


def test_project_attack_set_is_not_sample_customer_dataset() -> None:
    ps = uploaded_probe_set("test-id", {"tools": [{"name": "read_file"}]}, "atlas")
    assert {p.id for p in ps.probes} == {"UP-PATH-01", "UP-RAG-01", "UP-BENIGN-01"}
    assert not any("delete_customer" in " ".join(p.turns) for p in ps.probes)
    assert not uploaded_probe_set("test-id", {"tools": []}, "unknown").probes


def test_runtime_rejects_host_access_and_external_context(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    compose = tmp_path / "compose.yaml"
    compose.write_text('services:\n  agent:\n    build: .\n    ports: ["8000"]\n    privileged: true\n')
    with pytest.raises(AppError):
        runtime_spec(tmp_path)
    compose.write_text(
        'services:\n  agent:\n    build: .\n    ports: ["8000"]\n    volumes: ["/var/run/docker.sock:/var/run/docker.sock:ro"]\n'
    )
    with pytest.raises(AppError):
        runtime_spec(tmp_path)
    compose.write_text('services:\n  agent:\n    build: {context: ../}\n    ports: ["8000"]\n')
    with pytest.raises(AppError):
        runtime_spec(tmp_path)


def test_safe_runtime_spec_does_not_forward_host_ports(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "compose.yaml").write_text('services:\n  agent:\n    build: .\n    ports: ["127.0.0.1:8090:8000"]\n')
    spec = runtime_spec(tmp_path)
    assert spec["agent_port"] == 8000
    assert "ports" not in spec["services"][0]
