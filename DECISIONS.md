# 설계 변경 기록 (DECISIONS)

명세(`SPEC.md`)와 다르게 구현하는 경우 여기에 먼저 기록한다.

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
| 11 | 2026-10-19 | 평가 모델 | 소형 모델은 `LLM_SMALL_MODEL`(기본 `gpt-5.4-nano`, API 제거 예정 2027-04-01, 권장 대체 gpt-6-luna)로 지정. 계정 호출 가능 여부는 키 확보 후 확인 | CLAUDE.md 규칙 6. 키 없이는 실 LLM 평가 불가이므로 결과는 미측정으로 표기 |
| 12 | 2026-10-19 | 체크포인터 | 명세대로 Redis(AsyncRedisSaver) 사용, thread_id `audit:{id}` | 승인 대기 후 같은 thread_id 재개·워커 재시작 후 이어서 실행을 실환경 e2e 로 확인 |
| 13 | 2026-10-19 | ReAct 조사 구현 | 명세 8.5 의 `create_react_agent` 대신 `structured_call` 기반 JSON 스텝 루프(`react_investigator.py`)로 구현. 도구 선택은 매 스텝 `{why, action|final}` | 모델 비의존(도구 호출 API 없이 동작)·스텝 상한 6 을 코드가 보장·오프라인 스크립트 모델로 테스트 가능. H3 게이트·investigate.step 이벤트는 명세와 동일 |
| 14 | 2026-10-20 | 에이전트 확장 점검 | 명세 외 결정론 점검 A1(승인 없는 변경 도구)·A2(호출 주체 정책 누락)·A3(도구 조합)·A4(도구 설명 오염)를 `backend/core/agent_audit/` 라이브러리로 추가. 감사 그래프·API 에는 아직 연결하지 않음 | 기존 `rules/agent` 규칙과 겹치는 부분 정리 후 연결 여부 결정 |
| 15 | 2026-10-20 | 문서 위치 | 명세의 `docs/` 대신 `SPEC.md`·`DECISIONS.md` 를 루트에 두고, `docs/` 는 로컬 전용 분석 문서 폴더로 `.gitignore` 처리 | 저장소에는 작업 규칙에 필요한 명세·결정 기록만 올리고 분석 초안은 올리지 않음 |
| 16 | 2026-10-20 | Cloud 감사 제거 | 사용자 결정으로 Cloud 감사 전체(증거 묶음 파이프라인·워커·`/api/ai/tasks`·business/rag-mcp 도구·Cloud 화면·`/eval`·평가 러너·정답셋)를 삭제. 에이전트 점검 A1~A4 는 `backend/core/agent_audit/` 로 분리해 유지. 마이그레이션 0002·0003 파일은 제거하고 dev DB 의 alembic 버전은 0001 로 맞춤 | 되돌리기는 git 이력(Cloud 도입 커밋 `5b38144` 이전)에서. 기획서 대비 범위 축소이므로 발표 자료에 명시 |
