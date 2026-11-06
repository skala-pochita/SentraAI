# 코드 감사 평가 — vuln-python-app

- 정답셋: `datasets/ground_truth/code_python.yaml` · H 반복 1회 (평균 ± 표준편차) · 정적 회피 케이스: PY13, PY14
- S=내장 정적 규칙, SX=+외부 SAST·SCA, H=코드 감사 서브그래프 전체(정적→외부→ReAct→근거 검증)

| 지표 | S | SX |
|---|---|---|
| recall | 0.75 | 0.8125 |
| precision | 1 | 0.9286 |
| f1 | 0.8571 | 0.8667 |
| fp | 0 | 1 |
| evasion_recall | 0 | 0 |
| injection_robustness | 1 | 1 |
| auditor_injections_detected | 1 | 1 |
| latency_s | 0.37 | 0.08 |
| tokens | 0 | 0 |
| usd | 0 | 0 |

## 미탐 (첫 실행)

- S: FN ['PY09', 'PY13', 'PY14', 'PY16'] · 함정 오탐 없음 · 기타 오탐 없음
- SX: FN ['PY09', 'PY13', 'PY14'] · 함정 오탐 ['PD03'] · 기타 오탐 없음
