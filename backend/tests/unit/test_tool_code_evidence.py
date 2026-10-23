"""Real source spans, no name-only proof of missing authorization, severity unchanged."""

from pathlib import Path

from core.attack_paths import correlate_paths
from core.schemas import Evidence
from core.tool_code_evidence import collect_tool_evidence, implementation_evidence, manifest_evidence
from tests.unit.test_attack_paths import fact, setup

MANIFEST = """tools:
  - name: remove_record
    description: remove a test record
    side_effect: delete
    requires_approval: false
    scopes: [records:delete]
"""


def test_manifest_includes_actual_policy_lines_and_masks_secrets():
    refs = manifest_evidence(MANIFEST, "agent/manifest.yaml", "remove_record")
    assert [(e.label, e.line_start) for e in refs] == [
        ("도구 설정: name", 2),
        ("도구 설정: side_effect", 4),
        ("도구 설정: requires_approval", 5),
        ("도구 설정: scopes", 6),
    ]
    assert refs[2].snippet_masked.strip() == "requires_approval: false"
    assert not manifest_evidence(MANIFEST, "x.yaml", "another_tool")


def test_dispatch_span_does_not_include_next_tool(tmp_path: Path):
    (tmp_path / "policy.py").write_text("""def dispatch(name, args):
    if name == "remove_record":
        return db.delete(args["id"])
    elif name == "safe_read":
        return db.read(args["id"])
""")
    refs = implementation_evidence(tmp_path, {"remove_record"})["remove_record"]
    assert len(refs) == 1 and refs[0].line_start == 2 and refs[0].line_end == 3
    assert "safe_read" not in refs[0].snippet_masked
    assert "db.delete" in refs[0].snippet_masked
    assert refs[0].relation == "tool_dispatch_candidate"


def test_no_execution_no_tests_or_symlink_sources(tmp_path: Path):
    outside = tmp_path / "outside.py"
    outside.write_text('raise RuntimeError("must not execute")\ndef remove_record():\n    return 1\n')
    root = tmp_path / "project"
    root.mkdir()
    (root / "escape.py").symlink_to(outside)
    (root / "tests").mkdir()
    (root / "tests" / "fake.py").write_text("def remove_record():\n    return 1\n")
    assert not implementation_evidence(root, {"remove_record"})["remove_record"]
    (root / "main.py").write_text(outside.read_text())
    assert implementation_evidence(root, {"remove_record"})["remove_record"][0].line_start == 2


def test_duplicate_named_functions_are_candidates_not_proof(tmp_path: Path):
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("def remove_record():\n    return None\n")
    refs = implementation_evidence(tmp_path, {"remove_record"})["remove_record"]
    assert len(refs) == 2 and all(r.relation == "tool_function_candidate" for r in refs)


def test_code_issue_must_match_linked_project_path_line_and_content(tmp_path: Path):
    source = "def remove_record():\n    db.delete_all()\n"
    (tmp_path / "main.py").write_text(source)
    fs, results, plan = setup()
    snapshots = collect_tool_evidence(tmp_path, {"tools": [{"name": "remove_record"}]}, MANIFEST, "manifest.yaml")
    fs[1].evidence = [Evidence.model_validate(e) for e in snapshots["remove_record"]]
    issue = fact("code", "MISSING_AUTHORIZATION", target="repo", source="code")
    issue.evidence = [
        Evidence(kind="code", file_path="main.py", line_start=2, line_end=2, snippet_masked="    db.delete_all()")
    ]
    issue.reasoning_summary = "검사에서 확인한 코드 문제"
    targets = [{"target_id": "agent", "linked_target_id": "repo"}]
    paths = correlate_paths([*fs, issue], results, targets, plan)
    path = paths[0]
    assert path.severity == "CRITICAL"  # Existing rule is intentionally preserved.
    assert issue.temp_id in path.component_ids
    assert any(r.kind == "code_finding" for r in path.attack_path.code_reasons)
    assert "manifest.yaml:5" in path.reasoning_summary
    assert any(
        "런타임 바인딩" in r.explanation for r in path.attack_path.code_reasons if r.kind == "implementation_candidate"
    )
    for change in ("other_repo", "other_path", "other_line", "other_content"):
        copy = issue.model_copy(deep=True)
        if change == "other_repo":
            copy.target_id = "unrelated"
        if change == "other_path":
            copy.evidence[0].file_path = "elsewhere.py"
        if change == "other_line":
            copy.evidence[0].line_start = 20
        if change == "other_content":
            copy.evidence[0].snippet_masked = "something_else()"
        assert copy.temp_id not in correlate_paths([*fs, copy], results, targets, plan)[0].component_ids


def test_missing_implementation_is_explicit_and_severity_unchanged():
    fs, results, plan = setup()
    path = correlate_paths(fs, results, [], plan)[0]
    assert path.severity == "CRITICAL"
    assert any("구현 코드는 연결하지 못했습니다" in m for m in path.attack_path.missing_evidence)
