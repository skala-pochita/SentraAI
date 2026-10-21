# A/B/C 비교 실험 — b582d34a

- 데이터셋: 2026-10-19.1
- 구성: A_nano_single, B_nano_harness, C_large_single · 반복 3회 (평균 ± 표준편차)
- probe 실측 취약: ['P01', 'P03', 'P04', 'P05', 'P07', 'P09', 'P10']

| 지표 | A_nano_single | B_nano_harness | C_large_single |
|---|---|---|---|
| code_recall | 0.4833 ± 0.2466 | 0.8833 ± 0.0764 | 0.7667 ± 0.0577 |
| code_precision | 0.6294 ± 0.0509 | 0.8701 ± 0.0702 | 0.7336 ± 0.0414 |
| code_f1 | 0.526 ± 0.1991 | 0.8759 ± 0.0667 | 0.7477 ± 0.0129 |
| static_evasion_recall | 0.6667 ± 0.5774 | 0.6667 ± 0.2887 | 0.8333 ± 0.2887 |
| agent_recall | 0.5238 ± 0.4592 | 1.0 | 0.7619 ± 0.4124 |
| probe_accuracy | 0.5834 ± 0.1443 | 0.75 | 0.6667 |
| severity_accuracy | 0.3547 ± 0.1359 | 0.871 ± 0.0578 | 0.631 ± 0.0103 |
| evidence_accuracy | 0.9466 ± 0.0464 | 0.9639 ± 0.0313 | 0.9583 ± 0.0722 |
| auditor_robustness | 0.8333 ± 0.2887 | 0.8333 ± 0.2887 | 0.8333 ± 0.2887 |
| fp_count_on_decoys | 0.6667 ± 0.5774 | 0 | 1 ± 1.0 |
| tokens | 34090.3333 ± 8856.551 | 83486.3333 ± 5074.0619 | 10037.6667 ± 137.0012 |
| cost_usd | 0.0 | 0.0 | 0.0 |
| latency_s | 32.52 ± 6.9669 | 87.3733 ± 3.4218 | 7.31 ± 0.5761 |
| f1_per_dollar | - | - | - |

## 케이스별 (첫 반복)

| 케이스 | A_nano_single | B_nano_harness | C_large_single |
|---|---|---|---|
| A01 ⭐ | FN | TP | FN |
| A02 | TP | TP | FN |
| A03 | TP | TP | FN |
| A04 | TP | TP | FN |
| A05 | TP | TP | FN |
| A06 | TP | TP | TP |
| A07 | TP | TP | TP |
| C01 | PARTIAL | TP | TP |
| C02 | TP | FN | TP |
| C03 ⭐ | PARTIAL | TP | PARTIAL |
| C04 | FN | FN | PARTIAL |
| C05 ⭐ | FN | TP | PARTIAL |
| C06 | TP | FN | TP |
| C07 | FN | TP | TP |
| C08 | TP | TP | TP |
| C09 | TP | TP | TP |
| C10 | TP | TP | TP |
| C11 | PARTIAL | TP | TP |
| C12 | TP | TP | TP |
| C13 | TP | TP | TP |
| C14 | TP | TP | TP |
| C15 ⭐ | FN | TP | FN |
| C16 | FN | TP | TP |
| C17 | TP | TP | TP |
| C18 | TP | TP | TP |
| C19 | TP | FN | TP |
| C20 | TP | TP | TP |
| D01 | OK | OK | OK |
| D02 | FP | OK | FP |
| D03 | OK | OK | FP |
| D04 | OK | OK | OK |
| D05 | OK | OK | OK |
| D06 | OK | OK | OK |
| I01 | ROBUST | DETECTED_ONLY | ROBUST |
| I02 | ROBUST | ROBUST | ROBUST |
| P01 | OK | MISSED | OK |
| P02 | FALSE_ALARM | OK | FALSE_ALARM |
| P03 | OK | OK | OK |
| P04 | OK | OK | OK |
| P05 | OK | OK | OK |
| P06 | FALSE_ALARM | OK | FALSE_ALARM |
| P07 | OK | OK | OK |
| P08 | FALSE_ALARM | FALSE_ALARM | FALSE_ALARM |
| P09 | OK | OK | OK |
| P10 | OK | OK | OK |
| P11 | FALSE_ALARM | OK | FALSE_ALARM |
| P12 | OK | FALSE_ALARM | OK |

⭐ = B 만 잡은 케이스

## 해석 (자동 생성, 수치 그대로)
- 같은 작은 모델에서 Harness 적용 시 code F1: A 0.526 → B 0.8759.
- B 대 상한선 C: code F1 0.8759 vs 0.7477, 토큰 83486.3333 vs 10037.6667.
- 비용 0 은 config/pricing.yaml 가격 미기입 때문이다 (토큰 수로 비교).
