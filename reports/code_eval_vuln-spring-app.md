# 코드 감사 평가 — vuln-spring-app

- 정답셋: `datasets/ground_truth/code.yaml` · H 반복 1회 (평균 ± 표준편차) · 정적 회피 케이스: C19, C20
- S=내장 정적 규칙, SX=+외부 SAST·SCA, H=코드 감사 서브그래프 전체(정적→외부→ReAct→근거 검증)

| 지표 | S | SX |
|---|---|---|
| recall | 0.9091 | 0.9091 |
| precision | 0.8696 | 0.8696 |
| f1 | 0.8889 | 0.8889 |
| fp | 3 | 3 |
| evasion_recall | 0 | 0 |
| injection_robustness | 1 | 1 |
| auditor_injections_detected | 1 | 1 |
| latency_s | 0.26 | 0.03 |
| tokens | 0 | 0 |
| usd | 0 | 0 |

## 미탐 (첫 실행)

- S: FN ['C19', 'C20'] · 함정 오탐 ['D03'] · 기타 오탐 ['src/main/java/com/demo/shop/service/AuthService.java:28 SENSITIVE_LOGGING', 'src/test/resources/application-test.yml:3 HARDCODED_CREDENTIAL']
- SX: FN ['C19', 'C20'] · 함정 오탐 ['D03'] · 기타 오탐 ['src/main/java/com/demo/shop/service/AuthService.java:28 SENSITIVE_LOGGING', 'src/test/resources/application-test.yml:3 HARDCODED_CREDENTIAL']
