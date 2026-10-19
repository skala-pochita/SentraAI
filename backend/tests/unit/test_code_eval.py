from __future__ import annotations

from harness.eval.code_eval import load_code_truth, score_code, static_findings


def test_static_baseline_scores_are_honest() -> None:
    s = score_code(static_findings())
    assert s["fn"] == ["C19", "C20"]  # 정적 규칙은 난독화 2건을 놓친다 (ReAct 조사 대상)
    assert s["recall"] == 0.9 and s["auditor_injections_detected"] == 1.0
    assert s["injection_robustness"] == 1.0  # 인젝션 옆 C06 도 계속 보고
    assert "D03" in s["decoy_false_positives"] and s["precision"] < 1.0


def test_perfect_and_empty_findings() -> None:
    gt = load_code_truth()
    perfect = [{"file": g["file"], "line": g["line"], "category": g["category"]} for g in gt["findings"]]
    s = score_code(perfect)
    assert s["recall"] == 1.0 and s["precision"] == 1.0 and not s["decoy_false_positives"]
    e = score_code([])
    assert e["recall"] == 0.0 and e["precision"] is None and e["f1"] is None  # 분모 0 = 해당 없음
    assert e["injection_robustness"] == 0.0
