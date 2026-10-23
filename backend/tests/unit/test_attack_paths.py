"""Evidence boundaries and cross-target/cross-attempt regression cases."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from core.attack_paths import correlate_paths
from core.path_schema import AttackPath
from core.schemas import Evidence, FindingDraft


def fact(id, category, target="agent", subject=None, source="agent", probe=None):
    return FindingDraft(
        temp_id=id,
        target_id=target,
        source=source,
        subject=subject,
        category=category,
        title=f"{category}: misleading-name",
        severity="HIGH",
        confidence=0.9,
        verdict="TRUE_POSITIVE",
        evidence=[
            Evidence(
                kind="probe" if probe else "code",
                probe_id=probe,
                file_path=None if probe else "app.py",
                line_start=None if probe else 1,
                snippet_masked="API_KEY = masked",
            )
        ],
    )


def setup(effect="delete", name="remove_record"):
    category = "TOOL_EXFIL_CHANNEL" if effect == "external_send" else "TOOL_NO_APPROVAL"
    fs = [fact("attack", "INDIRECT_INJECTION", probe="P"), fact("policy", category, subject=name)]
    plan = {"agents": {"agent": {"tool_profiles": [{"name": name, "side_effect": effect, "requires_approval": False}]}}}
    result = {
        "target_id": "agent",
        "probe_id": "P",
        "runtime_mode": "mock",
        "transcripts": [
            {"probe_run_id": "run", "verdict": "SUCCESS", "tool_calls": [{"name": name, "executed": True}]}
        ],
    }
    return fs, [result], plan


@pytest.mark.parametrize(
    ("effect", "rule", "impact"),
    [("delete", "CORR-001", "삭제"), ("write", "CORR-001", "변경"), ("external_send", "CORR-002", "전송")],
)
def test_arbitrary_tool_names_and_impact_boundaries(effect, rule, impact):
    fs, results, plan = setup(effect, "arbitrary_custom_action")
    paths = correlate_paths(fs, results, [], plan)
    assert len(paths) == 1 and paths[0].rule_id == rule
    path = paths[0].attack_path
    assert path.runtime_mode == "mock" and path.observation_source == "target_reported"
    assert path.steps[-1].evidence_level == "CONDITIONAL" and impact in path.steps[-1].title
    assert path.steps[1].evidence_refs[0].probe_run_id == "run"
    assert path.edges[0].evidence_level == "CONDITIONAL"
    if effect == "write":
        assert "삭제" not in paths[0].title


@pytest.mark.parametrize(
    "change", ["target", "probe", "policy_tool", "no_execution", "unknown_effect", "unknown_approval", "review_policy"]
)
def test_unrelated_or_unconfirmed_facts_do_not_form_path(change):
    fs, results, plan = setup()
    if change == "target":
        results[0]["target_id"] = "other"
    if change == "probe":
        results[0]["probe_id"] = "OTHER"
    if change == "policy_tool":
        fs[1].subject = "different_tool"
    if change == "no_execution":
        results[0]["transcripts"][0]["tool_calls"][0]["executed"] = False
    if change == "unknown_effect":
        plan["agents"]["agent"]["tool_profiles"][0].pop("side_effect")
    if change == "unknown_approval":
        plan["agents"]["agent"]["tool_profiles"][0].pop("requires_approval")
    if change == "review_policy":
        fs[1].verdict = "NEEDS_REVIEW"
    assert correlate_paths(fs, results, [], plan) == []


def test_never_combine_success_and_call_from_different_attempts():
    fs, results, plan = setup()
    failed = deepcopy(results[0]["transcripts"][0])
    failed.update(probe_run_id="failed", verdict="BLOCKED")
    results[0]["transcripts"][0]["tool_calls"] = []
    results[0]["transcripts"].append(failed)
    results[0]["tools_hit"] = ["remove_record"]  # historical aggregate must not influence correlation
    assert correlate_paths(fs, results, [], plan) == []


def test_all_successful_run_references_are_kept():
    fs, results, plan = setup()
    for i in range(5):
        attempt = deepcopy(results[0]["transcripts"][0])
        attempt["probe_run_id"] = f"run{i}"
        results[0]["transcripts"].append(attempt)
    path = correlate_paths(fs, results, [], plan)[0].attack_path
    assert len(path.steps[1].evidence_refs) == 6


def test_secret_names_require_linked_project():
    fs = [
        fact("env", "AGENT_SHARED_SECRET", subject="API_KEY"),
        fact("code", "HARDCODED_SECRET", "repo", source="code"),
    ]
    assert not correlate_paths(fs, [], [], {})
    paths = correlate_paths(fs, [], [{"target_id": "agent", "linked_target_id": "repo"}], {})
    assert paths[0].rule_id == "CORR-003" and paths[0].attack_path.edges[0].evidence_level == "STATIC"


def test_prompt_leak_does_not_confirm_secret_leak_or_upgrade_source():
    fs = [fact("secret", "PROMPT_SECRET_EMBEDDED"), fact("leak", "SYSTEM_PROMPT_LEAK")]
    path = correlate_paths(fs, [], [], {})[0]
    assert path.rule_id == "CORR-004" and path.severity == "HIGH"
    assert fs[0].severity == "HIGH" and path.attack_path.steps[-1].evidence_level == "CONDITIONAL"
    fs[1].target_id = "other"
    assert not correlate_paths(fs, [], [], {})


def test_backend_mapping_must_be_per_tool_and_linked():
    fs = [
        fact("policy", "TOOL_NO_APPROVAL", subject="custom"),
        fact("code", "SECCONFIG_PERMIT_ALL", "repo", source="code"),
    ]
    targets = [{"target_id": "agent", "linked_target_id": "repo"}, {"target_id": "repo", "name": "backend"}]
    plan = {"agents": {"agent": {"backend_api": "backend", "tool_profiles": [{"name": "custom"}]}}}
    assert not correlate_paths(fs, [], targets, plan)
    plan["agents"]["agent"]["tool_profiles"][0]["backend_api"] = "backend"
    assert correlate_paths(fs, [], targets, plan)[0].rule_id == "CORR-005"
    fs[0].subject = "unrelated"
    assert not correlate_paths(fs, [], targets, plan)


def test_invalid_graph_reference_is_rejected():
    fs, results, plan = setup()
    payload = correlate_paths(fs, results, [], plan)[0].attack_path.model_dump()
    payload["edges"][0]["to_step_id"] = "nonexistent"
    with pytest.raises(ValidationError):
        AttackPath.model_validate(payload)
