# 평가표 대비 점검 (evaluateCheck)

> 기준: `evalute.md` (표 6-14 평가표, 표 6-13 Harness 요점, 표 6-3 구현 기준)
> 범위: **보안감사 에이전트(코드 감사 + Agent 감사)만**. Cloud 감사는 제외.
> 최초 점검 2026-10-20 오전 → **보강 후 갱신 2026-10-20** (유효한 OpenAI·LangSmith 키로 실측).
> 범례: `[x]` 충족(근거 있음) · `[~]` 부분 충족 · `[ ]` 부족

## 요약 — 보강 결과

| 순위 | 최초 점검의 부족한 부분 | 지금 상태 | 근거 |
|---|---|---|---|
| 1 | 실 LLM 경로 미검증 | **[x] 검증** | probe 스모크 vuln·hardened 통과, Kafka 경유 감사 1건 COMPLETED(ReAct 26스텝, 해결 22·수동 1) |
| 2 | 코드 감사 A/B/C 비교 실험 없음 | **[x] 구현·실측 (반복 3회)** | `reports/eval_b582d34a.md` — B code F1 0.88 vs A 0.53 vs C 0.75 |
| 3 | Agent Loop 120초·같은 도구 3회 없음 | **[x]** | `react_investigator.py`, `config/budget.yaml` react, 테스트 2개 |
| 4 | 변경 도구 destructiveHint 없음 | **[x]** | MCP ToolAnnotations 자동 부여, `test_tool_annotations_mark_destructive` |
| 5 | 개인정보 마스킹 없음 | **[x]** | 이메일·전화 마스킹(UUID 오인 방지 포함), `test_pii_masking`, `test_masking_never_touches_uuids` |
| 6 | 오래된 대화 요약 없음 | **[x]** | `compact_observations` + `context_summarize@v1` |
| 7 | trace_id 하나로 묶기·LangSmith | **[x]** | 감사 1건 = trace_id 1개 (Kafka·span 278개·LangSmith run 3개 동일, 토큰 89,898 일치) |
| 8 | 다이어그램·`/quality` 없음 | **[x]** | `docs/diagrams/*.mmd` 3종, `GET /quality` (ISO 25010 9특성 실측) |
| 9 | MultiServerMCPClient 미사용 | **[x]** | 워커 HTTP 전송 = `MultiServerMCPClient` 세션 (DECISIONS #21) |

### A/B/C 비교 실험 결과 (실측, 반복 3회, 평균 ± 표준편차)

| 지표 | A_nano_single | **B_nano_harness** | C_large_single |
|---|---|---|---|
| code F1 | 0.53 ± 0.20 | **0.88 ± 0.07** | 0.75 ± 0.01 |
| code 재현율 / 정밀도 | 0.48 / 0.63 | **0.88 / 0.87** | 0.77 / 0.73 |
| 정적 회피(C19·C20) 재현율 | 0.67 | 0.67 | **0.83** |
| Agent 정적 재현율 | 0.52 | **1.00** | 0.76 |
| probe 판정 정확도 | 0.58 | **0.75** | 0.67 |
| 심각도 정확도 | 0.35 | **0.87** | 0.63 |
| 감사기 인젝션 견고성 | 0.83 | 0.83 | 0.83 |
| 함정 오탐 수 | 0.67 | **0** | 1 |
| 토큰 / 지연 | 34k / 33s | 83k / 87s | 10k / 7s |

- A = gpt-5.4-nano 단일 프롬프트, B = gpt-5.4-nano + Harness + LangGraph + MCP, C = gpt-5.4-mini(이 키로 쓸 수 있는 최고 모델) 단일 프롬프트.
- 결론(수치 그대로): **같은 작은 모델에서 Harness 적용 시 F1 0.53 → 0.88, 상한선 C(0.75)도 앞섬.** 다만 정적 회피 케이스는 C 가 더 잘 잡고, 토큰은 B 가 C 의 약 8배.
- 비용(USD)은 `config/pricing.yaml` 가격 미기입으로 0 → f1_per_dollar 미산출 (팀이 가격 기입 필요).

---

## 1. Biz. 아이템 (10)
- [x] B2B 대상, 경쟁 대비 포지셔닝 — `docs/SPEC.md` 1장
- [~] 페르소나·경쟁 비교표 발표 자료 정리 필요 (명세에만 있음)
- [ ] 수익 모델·효용 근거 수치 — 이번 실측(같은 모델 F1 +0.35, 정답셋 기준)을 발표 자료에 반영 가능

## 2. 아키텍처 설계 (10)
- [x] 구성도·컴포넌트 책임 — `docs/diagrams/architecture.mmd`, `docker-compose.yml`
- [x] ISO/IEC 25010 대응 + 실측 — `GET /quality`, 품질 화면(`/quality`)
  - 충족: 성능 효율성(87초 ≤ 300초), 호환성, 상호작용성, 신뢰성(완료 7/7, DLQ 0), 유지보수성(규칙 1개 = 파일 1개), 안전성(승인 없이 변경된 CRITICAL 0)
  - **미달(그대로 보고)**: 기능 적합성 — 재현율 0.88 (목표 0.9), 정밀도 0.87 (목표 0.85 충족) / 보안 — 감사기 견고성 0.83 (목표 100%)
  - 미측정: 유연성(이식성) 카드는 수치 대신 `/ops/health?deep=true` 안내

## 3. AI Agent 설계 (15)
- [x] State 구조 — `core/graph/state.py`
- [x] Graph 흐름(병렬 Send, 분기 4, 반복 3) — `core/graph/build.py` = `docs/diagrams/audit_graph.mmd`
- [x] Agentic 요소: ReAct 조사, 적응형 probe, 자기 재검증

## 4. AI Agent 구현 (15)
- [x] Graph 코드 반영 (노드 1개 = 파일 1개)
- [x] **모델이 도구를 선택해 근거와 함께 답함 — 실측**: investigate.step 이벤트에 `{tool, why}` (예: "CSRF disable 이 stateless API 전용인지 판단하기 위해 원문을 읽는다"), Finding `tools_used`·`evidence`
- [x] 상태 저장·재개 (Docker e2e: 승인 대기 중 워커 재시작 후 재개)
- [x] 진행 이벤트 (Pub/Sub + Stream + DB, SSE)
- [x] Agentic 구현 실측 — 적응형 probe, LLM 판정, LLM 수정안(부분 적용), 재검증 루프 (P09 미해결 → MANUAL_REQUIRED)
- [~] C19·C20 탐지 — B 재현율 0.67 (6회 중 4회). 상한선 C 0.83 보다 낮음

## 5. MCP 설계·구현 (20)
- [x] business-mcp 조회 7·실행 3·변경 9, rag-mcp 조회 7·변경 4, docstring 설명
- [x] Agent 연결: **MultiServerMCPClient**(langchain-mcp-adapters) — 실서버 호출 확인
- [x] 오류·예외: 공통 envelope, 오류 코드 + retryable, 서비스 토큰, 서버 역할 재검사, 경로 탈출 거부, 멱등성, 타임아웃, 부분 적용(`skipped_edits`)
- [x] **ToolAnnotations**: 변경 `destructiveHint`, 조회 `readOnlyHint`, probe `openWorldHint`
- [ ] 외부 MCP 클라이언트(Claude Desktop 등) 연결 확인 (Phase 9)

## 6. Harness (20) — 표 6-3 기준 (상세: `docs/HARNESS.md`)

| 구성 요소 | 상태 | 구현 |
|---|---|---|
| Instructions | [x] | `harness/prompts/*_v{n}.yaml` 버전 관리(code_investigate v1→v3 개선 이력), prompt_id@version 을 trace 에 기록. 위치 형식 차이는 DECISIONS #23 |
| Tools | [x] | H3 게이트(노드별 allowlist·역할·호출 수), 결과 8000자 절단, 오류를 관찰로 반환, ToolAnnotations |
| Agent Loop | [x] | 파일당 LLM 8턴·120초·같은 도구 3회, 위험 파일은 읽기 전 final 불가 |
| Context | [x] | 파일 선별, `wrap_untrusted`, 관찰 6000토큰 초과 시 요약 |
| State·Memory | [x] | AsyncRedisSaver, thread_id, interrupt 2곳, 재시작 재개 |
| Guardrails | [x] | 승인 interrupt + destructiveHint, 승인 없는 apply_fix FORBIDDEN, 인젝션 탐지, 스키마 검증 재요청, 근거 검증 + **라인 보정**, 확정 정적 규칙은 LLM 기각 불가, 비밀값·개인정보 마스킹 |
| Recovery | [x] | 2회 재시도 → Kafka 재시도 3회 → DLQ → FAILED, 회로 차단기 |
| Observability | [x] | trace_id 하나로 node/llm/tool span + LangSmith (실연동 확인) |
| 평가(확인) | [x] | A/B/C 러너·채점기, `/eval` 화면, 요소별 테스트 |

## 7. 인증 (10)
- [x] JWT 회전, bcrypt, 잠금, 블랙리스트, RBAC 3역할, MCP 서비스 토큰 — 통합 테스트

## 8. 가산점 — 대기열 (10)
- [x] Kafka 재시도·DLQ·재처리, 통합 테스트 7개
- [~] `make fault-on` 실환경 시나리오 미관찰 (DLQ 는 N ≥ 12 필요, DECISIONS #28)

---

## 실측 중 발견해 고친 문제 (보강 과정 기록)
1. 시스템 프롬프트의 `{자리표시자}` 가 채워지지 않고 모델에 그대로 전달됨 → `render_system()`
2. 전화번호 마스킹이 UUID 조각을 전화번호로 오인 → finding_id 손상(간헐 테스트 실패의 원인) → 경계 조건 + UUID 제외
3. LLM edit 1개 실패로 코드 수정안 전체가 버려짐 → 부분 적용
4. "테스트용 가짜 값" 주석·마스킹을 보고 LLM 이 확정 탐지를 오탐 처리 → 확정형 규칙은 기각 불가 + 프롬프트 v2
5. 자유 문자열 카테고리·틀린 라인 → 카테고리 고정(v3 정의 포함)·근거 라인 보정
6. 위험 파일을 읽지 않고 결론 → 읽기 전 final 거부
7. 실험 러너가 게이트 1개를 공유해 한도 초과 → 실행마다 게이트 생성, 실행 실패는 결과로 기록

## 남은 것
- [ ] `config/pricing.yaml` 모델 가격 기입 → f1_per_dollar
- [ ] 기능 적합성 재현율 0.9 · 감사기 견고성 100% 목표 미달 분석 (I01 이 1회 DETECTED_ONLY: C06 누락)
- [ ] `make fault-on` 실환경 시연, 외부 MCP 클라이언트 연결, 브라우저 데모 리허설
- [ ] 발표 자료: 페르소나·경쟁표·비교 실험 결과
