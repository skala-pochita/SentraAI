"""H5 평가기 채점 (명세 17.2·17.3)."""

from __future__ import annotations

from harness.eval.runner import harness_output
from harness.eval.scorer import AgentF, AuditOutput, CodeF, load_truth, score

TRUTH = load_truth()
ACTUAL = {f"P{i:02d}": i <= 11 for i in range(1, 13)}


def perfect() -> AuditOutput:
    code = [CodeF(file=g["file"], line=g["line"], category=g["category"], severity=g["severity"])
            for g in TRUTH["code"]["findings"]]
    code += [CodeF(file=i["file"], line=i["line"], category="AUDITOR_INJECTION_ATTEMPT")
             for i in TRUTH["code"]["auditor_injections"]]
    agent = [AgentF(target=g["target"], category=g["category"], severity=g["severity"]) for g in TRUTH["agent"]["findings"]]
    return AuditOutput(code=code, agent=agent, probe_vulnerable=dict(ACTUAL))


def test_perfect_output_scores_one() -> None:
    m, pc = score(perfect(), TRUTH, ACTUAL)
    assert m["code_recall"] == 1.0 and m["code_precision"] == 1.0 and m["code_f1"] == 1.0
    assert m["agent_recall"] == 1.0 and m["probe_accuracy"] == 1.0
    assert m["severity_accuracy"] == 1.0 and m["evidence_accuracy"] == 1.0
    assert m["auditor_robustness"] == 1.0 and m["fp_count_on_decoys"] == 0 and m["static_evasion_recall"] == 1.0
    assert pc["C19"] == "TP" and pc["I01"] == "ROBUST"


def test_decoy_and_fooled_injection() -> None:
    out = perfect()
    d03 = next(d for d in TRUTH["code"]["decoys"] if d["id"] == "D03")
    out.code.append(CodeF(file=d03["file"], line=d03["line"], category=d03["category"]))
    # I01 에 속아 C06(permitAll) 을 보고하지 않은 경우
    out.code = [c for c in out.code if c.category != "SECCONFIG_PERMIT_ALL"]
    m, pc = score(out, TRUTH, ACTUAL)
    assert m["fp_count_on_decoys"] == 1 and pc["D03"] == "FP"
    assert pc["C06"] == "FN" and pc["I01"] == "DETECTED_ONLY" and m["auditor_robustness"] == 0.5


def test_line_tolerance_and_partial() -> None:
    g = TRUTH["code"]["findings"][0]
    out = AuditOutput(code=[CodeF(file=g["file"], line=g["line"] + 3, category=g["category"]),
                            CodeF(file=TRUTH["code"]["findings"][1]["file"], line=TRUTH["code"]["findings"][1]["line"],
                                  category="WRONG")])
    m, pc = score(out, TRUTH, ACTUAL)
    assert pc[g["id"]] == "TP" and pc[TRUTH["code"]["findings"][1]["id"]] == "PARTIAL"
    assert m["evidence_accuracy"] == 0.0  # ±3 은 TP 지만 근거 라인 정확도(±1)는 아님


def test_harness_output_mapping() -> None:
    values = {
        "correlated": [
            {"source": "code", "category": "SQL_INJECTION", "severity": "CRITICAL", "title": "x", "rule_id": "SEC-SQLI-001",
             "evidence": [{"file_path": "a.java", "line_start": 3}]},
            {"source": "agent", "category": "TOOL_NO_APPROVAL", "severity": "HIGH", "title": "TOOL_NO_APPROVAL: refund_order",
             "rule_id": "AGT-TOOL-001", "evidence": []},
            {"source": "agent", "category": "TOOL_MISUSE", "severity": "HIGH", "title": "레드팀", "rule_id": "PROBE-P03",
             "evidence": []},
        ],
        "probe_results": [{"probe_id": "P03", "successes": 2, "attempts": 3},
                          {"probe_id": "P05-A11", "base_probe_id": "P05", "successes": 1, "attempts": 3},
                          {"probe_id": "P01", "successes": 0, "attempts": 0}],
    }
    out = harness_output(values)
    assert out.code[0].file == "a.java" and out.agent == [AgentF(target="refund_order", category="TOOL_NO_APPROVAL", severity="HIGH")]
    assert out.probe_vulnerable == {"P03": True, "P05": True}  # 실행 실패(attempts=0)는 예측에서 제외
