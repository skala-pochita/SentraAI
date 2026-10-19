# 설계 변경 기록 (DECISIONS)

명세(`docs/SPEC.md`)와 다르게 구현하는 경우 여기에 먼저 기록한다.

| # | 날짜 | 항목 | 결정 | 이유 |
|---|------|------|------|------|
| 1 | 2026-10-19 | 저장소 루트 | 명세의 `ai-security-auditor/` 대신 기존 `capstoneAIAgent/` 폴더를 루트로 사용 | IDE 프로젝트가 이미 이 폴더로 열려 있음. 내부 구조는 19장과 동일 |
| 2 | 2026-10-19 | 진행 추적 | 루트에 `checklist.md` 추가 | 세션 중단 시 이어서 개발하기 위함 |
| 3 | 2026-10-19 | Node 버전 | Node 20 → Node 22 (프론트 이미지 `node:22-alpine`) | pnpm 11 이 Node ≥ 22.13 을 요구. 기능 차이 없음 |
| 4 | 2026-10-19 | 임베딩 오프라인 대체 | `OPENAI_API_KEY` 가 없으면 결정론적 해시 임베딩(1536차원 feature hashing) 사용, 하이브리드 가중치는 `config/rag.yaml` 에서 provider 별로 조정 | 키 없이도 rag-mcp·시드·테스트가 동작해야 함. 키가 있으면 명세대로 `EMBEDDING_MODEL` 사용 (임베딩 교체 시 `make seed` 재실행으로 재인덱싱) |
| 5 | 2026-10-19 | rag-mcp 도구 추가 | `list_documents` 조회 도구 추가 | 대시보드 지식 관리 화면(16장)의 문서 목록용 |
| 6 | 2026-10-19 | Kafka 멱등 키 | `kafka:processed:{message_id}` 를 처리 시작 시 `processing:<worker>` lease(60s, 하트비트 갱신)로, 완료 후 `done`(24h)으로 기록 | 명세대로 '시작 시 NX 실패면 skip' 하면 처리 중 워커가 죽었을 때 재전달 메시지가 skip 되어 감사가 유실됨. lease 만료 후 이어받아 checkpoint 부터 재개 |
| 7 | 2026-10-19 | State 직렬화 | AuditState 에 Pydantic 모델 대신 `model_dump()` dict 저장 (스키마는 `core/schemas`) | Redis 체크포인터 직렬화 안정성 |
| 8 | 2026-10-19 | 수정안 edits | `create_fix_proposal` 이 `diff` 외에 `edits`(search/replace·line_regex 등)를 받아 서버가 실제(비마스킹) 파일에 적용해 diff 계산. 결정론 수정기(`core/fixes.py`)를 우선 쓰고 없으면 LLM | 워커·LLM 은 마스킹된 내용만 보므로 비밀값이 있는 줄의 unified diff 를 정확히 만들 수 없음 |
| 9 | 2026-10-19 | merge_fix 도구 | business-mcp 에 `merge_fix` 추가 (GIT_MODE=local 병합 시뮬레이션). API `/fixes/{id}/merge` 가 호출 | /workspace 는 business-mcp 만 쓰기 마운트 |
| 10 | 2026-10-19 | fingerprint | probe·agent Finding 은 파일 경로 대신 probe_id, snippet 대신 rule_id 를 사용 | 파일·스니펫이 없는 Finding 끼리 같은 지문으로 병합되는 문제 |
| 11 | 2026-10-19 | 기획서(`AI_Agent_Cloud_Audit_Plan.md` 개정판 6) 적용 범위 | 기획서의 **감사 범위(A1~A4·B1·C1~C3·D1), 증거 묶음 입력, 예산, 평가(A/B, T01~T08), 승인·티켓 흐름**만 Phase 10 으로 채택. Vue·Spring·FastAPI 3층 구조는 채택하지 않고 기존 Python 단일 스택(FastAPI api + Kafka 워커 + business/rag-mcp)에 매핑 | 명세(SPEC)가 단일 진실 공급원이고 이미 Phase 0~6 이 Python 스택으로 구현됨. 기획서 책임 경계(Agent 는 업무 DB 직접 접근 금지, MCP 는 api 경유)는 유지 방향으로 Phase 10 에서 점검 |
| 12 | 2026-10-19 | 기획서 외부 API 5개 | `/api/ai/tasks` 계열 5개는 기존 `/audits` 라우터 위의 얇은 파사드로 추가 (POST tasks→202 {taskId}, GET tasks/{id}→{status,answer,sources}, GET stream, POST approval→202, GET tasks?mine=true). 내부 `/runs`·`/resume` 은 만들지 않고 Kafka `audit.requested`/`audit.resume` 로 대체 | 기획서 11장의 비동기 접수 요건(202·SSE·승인 202)은 동일하게 충족. Kafka 는 기획서가 가산점으로 둔 대기열 확장에 해당 |
| 13 | 2026-10-19 | 6개 이벤트 | 기획서 이벤트 TOKEN/TOOL_START/TOOL_END/APPROVAL_REQUIRED/DONE/FAILED 는 기존 `harness/events.py` 이벤트 타입에 매핑해 SSE 에서 변환 노출. 내부 이벤트는 그대로 유지 | 기존 프론트·테스트 계약 보존 |
| 14 | 2026-10-19 | 예산 값 | 기획서의 LLM 8턴·활성 120초·동일 도구 3회·429/503 재시도 2회·회귀 1회를 `config/budget.yaml` 의 `cloud_audit` 블록으로 추가(코드 하드코딩 금지). 기존 `audit`(토큰·비용) 한도는 유지 | CLAUDE.md 규칙 6. A/B 공통 적용 |
| 15 | 2026-10-19 | 평가 모델 | 기획서 후보 `gpt-5.4-nano-2026-03-17` 은 `LLM_SMALL_MODEL` 환경변수로 지정(제거 예정 2027-04-01, 권장 대체 gpt-6-luna). 계정 호출 가능 여부는 키 확보 후 확인 | CLAUDE.md 규칙 6. 키 없이는 실 LLM 평가 불가이므로 평가 결과는 미측정 표기 |
| 16 | 2026-10-19 | 체크포인터 | 기획서는 PostgreSQL checkpointer, 명세는 Redis. 명세(Redis)를 유지하고 Redis 실환경 확인 실패 시 Postgres(전용 스키마)로 대체 | 기존 결정 유지. 기획서 요건(thread_id 재개, 재시작 후 이어서 실행)은 어느 쪽이든 동일 |
| 17 | 2026-10-19 | ReAct 조사 구현 | 명세 8.5 의 `create_react_agent` 대신 `structured_call` 기반 JSON 스텝 루프(`react_investigator.py`)로 구현. 도구 선택은 매 스텝 `{why, action|final}` | 모델 비의존(도구 호출 API 없이 동작)·스텝 상한 6 을 코드가 보장·오프라인 스크립트 모델로 테스트 가능. H3 게이트·investigate.step 이벤트는 명세와 동일 |
| 18 | 2026-10-19 | Cloud 감사 저장 | 기획서의 Spring 업무 DB 를 대신해 Postgres 의 `cloud_tasks/cloud_task_events/cloud_approvals/remediation_tickets` 사용. 워커는 business/rag-mcp 로만 읽고 쓴다(체크포인터는 Redis). 승인은 `cloud_approvals`(APPROVED) 가 기준 | DECISIONS #11 의 매핑. 책임 경계(Agent 직접 DB 금지)는 MCP 경유로 유지 |
| 19 | 2026-10-19 | Cloud 입력 | 증거 묶음은 합성 사례 15개(`datasets/cloud`). 공개 자료(IAM Vulnerable 등) commit·라이선스·2인 검토는 미확인 | `datasets/cloud/SOURCES.md` 참조. 확인 전에는 공개 자료에서 추출했다고 주장하지 않음 |
