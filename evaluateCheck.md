# 평가표 대비 점검 (evaluateCheck)

> 기준: `evalute.md` (표 6-14 평가표, 표 6-13 Harness 요점, 표 6-3 구현 기준)
> 범위: **보안감사 에이전트(코드 감사 + Agent 감사)만**. Cloud 감사(Phase 10)는 제외.
> 점검일: 2026-10-20. 근거는 저장소 코드와 `checklist.md` 진행 로그.
> 범례: `[x]` 충족(근거 있음) · `[~]` 부분 충족 / 미검증 · `[ ]` 부족

## 요약 — 우선 보완할 것

| 순위 | 부족한 부분 | 영향 항목 | 비고 |
|---|---|---|---|
| 1 | **유효한 OPENAI_API_KEY 로 실 LLM 경로 검증** (ReAct 도구 선택, probe, LLM 판정) | AI Agent 구현(15), Harness(20) | 키 없이 "모델이 도구를 선택해 근거와 함께 답함"을 시연할 수 없음 |
| 2 | **코드 감사 A/B/C 비교 실험 없음** (`/eval` 은 Cloud 화면) | Harness(20) 평가·확인 | 명세 17장. "Harness 효과"를 수치로 보여줄 근거 |
| 3 | **Agent Loop 한도 미흡**: 120초 작업 시간·같은 도구 3회 제한 없음 (ReAct 6스텝만) | Harness(20) | 표 6-3 권장값과 다르면 Harness 명세에 남겨야 함 |
| 4 | **변경 도구 `destructiveHint` 표시 없음** | Harness Guardrails, MCP(20) | MCP ToolAnnotations 로 7개 변경 도구 표시 |
| 5 | **개인정보(이메일·전화) 마스킹 없음** (비밀값만 마스킹) | Harness Guardrails | probe 응답·도구 결과에 고객 이메일 포함 가능 |
| 6 | **Context: 오래된 대화 요약 없음** | Harness Context | 토큰 예산은 있음 |
| 7 | **trace_id 하나로 묶는 관측 + LangSmith 실연동 미확인** | Harness Observability | 현재는 audit_id 로 span 묶음 |
| 8 | **아키텍처 다이어그램 파일 없음** (`docs/diagrams/` 비어 있음), `/quality` API 없음 | 아키텍처 설계(10) | 명세 4·5·14장 |
| 9 | Agent 가 **MultiServerMCPClient**(langchain-mcp-adapters)로 연결하지 않음 | MCP(20) | 자체 HTTP 게이트웨이 사용. 명세 9.1 과 차이 → DECISIONS 기록 필요 |

---

## 1. Biz. 아이템 (10)
- [x] B2B 대상(스타트업·중견기업 보안 담당), 경쟁 대비 포지셔닝 — `docs/SPEC.md` 1장
- [~] 페르소나 시나리오·경쟁 비교표가 명세에만 있음 → 발표 자료/README 에 정리 필요
- [ ] 수익 모델·시장 효용 근거(비용 절감 수치 등) 발표용 정리 없음

## 2. 아키텍처 설계 (10)
- [x] 서비스 구성·컴포넌트 책임 — `docker-compose.yml`(11개 서비스), 명세 4장
- [ ] **아키텍처·Graph 다이어그램 원본 없음** — `docs/diagrams/` 비어 있음 (mermaid 로 구성도, 감사 그래프, 시퀀스 작성)
- [~] ISO/IEC 25010 품질 대응 — 명세 5장 표는 있으나 **`GET /quality` API 없음**. 대시보드 `QualityPage` 는 감사 목록에서 일부만 계산
- [ ] 품질 지표 실측값(재현율·정밀도·감사 1건 처리 시간·장애 후 완료율) 미측정 — LLM 키·평가 러너 필요

## 3. AI Agent 설계 (15)
- [x] State 구조 — `backend/core/graph/state.py` (AuditState + 서브그래프 State, reducer)
- [x] Graph 흐름: 병렬 서브그래프(Send), 분기 4개(CRITICAL·승인/반려·수정 대상·재검증), 반복 3개(probe 라운드·재검증·ReAct) — `core/graph/build.py` 상단 다이어그램과 1:1
- [x] Agentic 요소 설계: ReAct 조사, 적응형 probe 생성, 자기 재검증(근거 검증·사전 재검사·re_verify)
- [ ] Graph 다이어그램 이미지/mermaid 파일 (발표용) — 2번과 동일

## 4. AI Agent 구현 (15)
- [x] 설계한 Graph 가 코드에 반영 — `build.py`, 노드 1개 = 파일 1개 (`core/graph/nodes/`)
- [~] **모델이 도구를 선택해 근거와 함께 답함** — 코드 있음(`react_investigator.py`: 도구·`why` 이벤트, `tools_used`, `evidence`), 단위 테스트 6개는 가짜 LLM. **실 LLM 미검증**
- [x] 상태 저장·재개 — AsyncRedisSaver, 승인 대기 중 audit-worker 재시작 후 재개 Docker e2e 통과 (checklist 로그)
- [x] 진행 이벤트 — Redis Pub/Sub + Stream + DB, SSE `Last-Event-ID` 재생 e2e 통과
- [~] Agentic 구현 — 적응형 probe·LLM 판정·LLM 수정안은 LLM 있을 때만 동작, **실측 없음**
- [ ] C19·C20(정적 회피) ReAct 탐지 실측 — 발표 핵심 주장 근거

## 5. MCP 설계·구현 (20)
- [x] business-mcp 조회 7 · 실행 3 · 변경 8(+merge_fix) 도구
- [x] rag-mcp 조회 7 · 변경 4 도구, 하이브리드 검색
- [x] 도구 설명: docstring 에 설명·입력·출력·오류·부작용 (`inspect_cli list` 로 확인)
- [x] 오류·예외 처리: 공통 envelope `{ok, data|error, meta}`, 오류 코드 8종 + retryable, 서비스 토큰, 서버 측 역할 재검사, 경로 탈출 거부, 멱등성(fingerprint·idempotency_key), 타임아웃
- [x] 권한 없는 `apply_fix` 거부(FORBIDDEN) 테스트
- [~] Agent 연결: 워커가 streamable-http 로 호출(`core/mcp_client.py`) — **명세의 MultiServerMCPClient 미사용** → 사용하거나 DECISIONS 에 이유 기록
- [ ] 변경 도구 **ToolAnnotations(`destructiveHint=True`, 조회는 `readOnlyHint=True`)** 미표시
- [ ] 외부 MCP 클라이언트(Claude Desktop 등) 연결 확인 (명세 5장 호환성, Phase 9)

## 6. Harness (20) — 표 6-3 구현 기준별

| 구성 요소 | 최소 기준 | 상태 | 현재 구현 / 부족한 점 |
|---|---|---|---|
| Instructions | prompts/*.md, 역할·도구 규칙·금지·출력 형식, 버전 관리 | [~] | `harness/prompts/*_v1.yaml` 레지스트리(id@version, 이벤트 기록). **위치·형식이 기준(prompts/*.md)과 다름** → Harness 명세에 기록 |
| Tools | 결과 길이 자르기, 오류는 ToolMessage | [~] | 오류는 모델에 텍스트로 반환(`error CODE: msg`), read_repo_file 400줄 제한. **도구 결과 글자 수 상한 없음**(ReAct 는 토큰 예산 소진 시만 자름), ToolMessage 아님(자체 JSON 루프) |
| Agent Loop | 최대 8턴, 작업 120초, 같은 도구 3회 | [ ] | ReAct 최대 6스텝 + 파일당 토큰 예산, 노드당 도구 60회. **120초 시간 제한·같은 도구 3회 제한 없음** |
| Context | 토큰 예산, 넘으면 오래된 대화 요약 | [~] | `harness/context.py` 파일 선별·예산(H2), `wrap_untrusted`. **요약(summarization) 없음** |
| State·Memory | thread_id 저장, 승인 대기 후 재개 | [x] | `thread_id=audit:{id}`, interrupt 2곳, 재시작 재개 e2e 통과 |
| Guardrails | 변경 도구 interrupt, 도구 결과 개인정보 마스킹 | [~] | human_approval·wait_merge interrupt, H3 도구 게이트, 근거 검증, 비밀값 마스킹, 인젝션 탐지. **destructiveHint 없음, 이메일·전화 마스킹 없음** |
| Recovery | 429·503 2회 재시도, 계속 실패 시 대체 모델 또는 FAILED | [x] | `harness/reliability.py` 인라인 2회(0.5s·2s) + 회로 차단기, Kafka 재시도 3회 → DLQ, 감사 FAILED. 대체 모델은 없음(선택 사항) |
| Observability | trace_id 하나로 Node·도구·Token·지연 | [~] | `trace_spans`(node/llm/tool, 토큰·비용·지연), `/traces`, TracesPage. **Kafka envelope 의 trace_id 가 span 에 전파되지 않음**, LangSmith 는 설정만 있고 실연동 미확인 |
| (확인) 관측·평가, 요소마다 테스트 | | [~] | 요소별 단위 테스트 다수. **A/B/C 비교 실험(코드 감사) 러너·결과 없음** |
| (승인) 변경 도구 destructiveHint + checkpointer 대기 | | [~] | checkpointer 대기는 충족, 표시(annotation)만 없음 |

- [ ] 강의 용어 8요소 ↔ 명세 H1~H8 대응표를 Harness 명세(`docs/`)에 작성 (명세 10장 "강의 용어로 맞출 것")

## 7. 인증 (10)
- [x] JWT access 15분 / refresh 7일 회전, bcrypt(cost 12), 로그인 5회 실패 15분 잠금, 로그아웃 블랙리스트
- [x] RBAC 3역할 + DEVELOPER 자기 대상 제한, MCP 서비스 토큰 + 역할 재검사
- [x] 통합 테스트(만료 401, 블랙리스트 401, 타인 대상 403) — `tests/integration/test_auth.py`
- [~] 역할별 화면 차이 데모 리허설 미실시

## 8. 가산점 — 대기열 (10)
- [x] Kafka 토픽 8개, 수동 커밋, 멱등 키, jobs.retry(5s/30s/120s) → jobs.dlq, retry-worker, DLQ 적재·재처리 API, Ops 화면
- [x] 재시도/DLQ 통합 테스트 7개 (checklist 로그)
- [~] `make fault-on` 실환경 시나리오(재시도 후 성공 / DLQ 까지) 미관찰. DLQ 시나리오는 N 을 크게 줘야 함(인라인 3회 × 전달 4회) → DECISIONS 미기록

---

## 다음 작업 제안 (순서)
1. `.env` 에 유효한 키·모델 ID → probe 스모크, ReAct C19·C20 실측, 감사 1건 처음부터 끝까지 시연
2. 코드 감사 A/B/C 러너(명세 17장) + `/eval` 화면을 코드 감사 결과로 연결
3. Harness 보강: ReAct 에 120초·같은 도구 3회 제한, 도구 결과 글자 수 상한, 이메일·전화 마스킹, MCP 변경 도구 `destructiveHint`, trace_id 전파
4. `docs/HARNESS.md`(강의 8요소 대응표 + 권장값과 다른 값 기록), `docs/diagrams/*.mmd`, `GET /quality`
5. DECISIONS 추가: MultiServerMCPClient 미사용 이유, 프롬프트 YAML 위치, fault-on DLQ N 값
