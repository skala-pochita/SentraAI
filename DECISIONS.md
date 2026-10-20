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
| 17 | 2026-10-20 | 취약 샘플 프롬프트 | `targets/target-agent/prompts/system_vuln.md` 에 실무형 잘못된 지시(직원 사칭 요청 즉시 수행, KB 문서 지시 우선, 이메일 요청 시 발송, run_sql 자유 조회, 인코딩 지시 실행)를 추가 | 실 LLM(gpt-5.4-nano) 측정에서 기본 안전성 때문에 취약 버전이 P01·P05 등을 스스로 막아 Phase 2 완료 조건(P01·P03·P05 중 ≥2 성공)을 충족 못함. 감사 대상 샘플을 설계 의도(취약)대로 만든 것이며 감사기·정답셋은 변경 없음 |
| 18 | 2026-10-20 | MCP 연결 | 워커의 HTTP 전송을 `langchain-mcp-adapters` `MultiServerMCPClient` 세션으로 교체 (호출 맥락 헤더가 호출마다 달라 클라이언트는 호출 단위 생성) | 명세 9.1 · 평가표 'Agent 가 연결해 호출' |
| 19 | 2026-10-20 | Harness Agent Loop 값 | ReAct: 파일당 LLM 8턴·120초·같은 도구 3회·도구 결과 8000자·관찰 6000토큰 초과 시 요약 (`config/budget.yaml` react) | 강의 표 6-3 권장값과 동일하게 맞춤. 기존 스텝 상한 6 은 유지(8턴 안에 final 포함) |
| 20 | 2026-10-20 | Instructions 위치 | 프롬프트는 강의 기준 `prompts/*.md` 대신 `backend/harness/prompts/*_v{n}.yaml` (id·version·system·user_template·output_schema) | 버전 관리·이벤트에 prompt_id@version 기록·출력 스키마 연결을 한 파일에서 하기 위함. 역할·도구 규칙·금지 사항·출력 형식 4요소는 모두 포함 |
| 21 | 2026-10-20 | 개인정보 마스킹 | `mask_text` 에 이메일(`k***@domain`, 도메인 유지)·전화번호(`010-****-5678`) 추가 | Harness Guardrails '도구 결과의 개인정보 마스킹'. 도메인은 외부 유출 근거라 남김 |
| 22 | 2026-10-20 | trace_id | 감사 1건의 trace_id = uuid5(audit_id). Kafka envelope·trace_spans.trace_id(0003)·LangSmith metadata 에 같은 값 | 요청·재개·재시도 메시지가 각자 새 trace_id 를 만들면 '하나로 묶어 보기'가 안 됨 |
| 23 | 2026-10-20 | 비교 실험 실행 방식 | 명세의 `eval.tasks` 분할 대신 eval-worker 1개가 (config × repeat) 를 순서대로 실행. probe 정답(실제 취약)은 실험마다 vuln 에이전트를 실측(ASR>0)해 정한다 | B 구성이 target-agent·대상 락을 써서 병렬 실행 시 간섭. 실측 기준은 명세 17.2 |
| 24 | 2026-10-20 | 수정안 부분 적용 | `create_fix_proposal` 이 실패한 edit 만 건너뛰고(`skipped_edits`) edit 가 하나도 적용되지 않은 Finding 만 수정안에서 제외 | 실 LLM 검증에서 LLM edit 1개 실패로 코드 수정안 전체가 버려짐 |
| 25 | 2026-10-20 | 장애 주입 DLQ 시나리오 | `make fault-on N=2` = 노드 안 재시도로 성공, DLQ 까지 보려면 N ≥ 12 (인라인 3회 × 전달 4회) | 명세 11.3 의 ':9 면 DLQ' 는 인라인 재시도를 고려하지 않은 값 |


## 2026-10-20 — 업로드 기반 읽기 전용 감사로 전환

사용자 요청에 따라 기존 명세의 자동 수정·승인·PR·병합·수정 후 재검증 흐름을 제외한다. 코드 근거 검증은 유지하고, 현재 소스에서 탐지된 문제점 및 권장 조치를 보고한다. GitHub 입력·webhook 경로를 제거하고 ZIP 스냅샷 업로드를 제공한다. 모델 비교는 A(작은 모델 단일 프롬프트)와 B(작은 모델 AI Agent)만 실행한다. 비교 실험은 재현 가능한 내장 정답셋을 계속 사용하며 임의 업로드 코드에는 정답셋 기반 점수를 부여하지 않는다. 품질 UI를 제거하고 구현·검증 상태는 QUALITY_IMPLEMENTATION.md에 기록한다. 기존 DB의 이력 테이블과 사용되지 않는 과거 수정 코드 일부는 마이그레이션 호환을 위해 보존하되 제품 API/도구/그래프에서는 실행할 수 없다. 기존 중단 체크포인트는 새 그래프에서 재개하지 않고 새 감사를 실행한다.


## 2026-10-20 — 기존 DB의 0002 마이그레이션 호환 복원

Cloud 기능 제거 시 삭제된 리비전 0002가 기존 DB에 남아 API 시작이 실패했다. 데이터 초기화나 강제 stamp 대신 0002를 무변경 호환 마커로 복원하고 0003의 부모로 연결한다. 신규 DB에는 제거된 Cloud 테이블을 만들지 않고 기존 DB의 과거 테이블·데이터는 보존한다.

## 2026-10-20 — 업로드 프로젝트의 전체 감사와 격리 실행

사용자가 업로드 코드의 자동 격리 실행을 선택했고, Docker 제어 권한에 대한 설명을 받은 뒤 로컬 Docker 소켓 사용을 명시적으로 승인했다. 소켓은 인증된 sandbox-runner에만 연결한다. 업로드 컨테이너에는 소켓·호스트 bind·검사기 환경변수·API 키를 주지 않는다. Compose를 직접 실행하지 않고 로컬 build·내부 포트·리터럴 환경·제한된 fixture 설정만 해석하여 컨테이너를 생성한다. 호스트 포트는 공개하지 않고 감사별 internal 네트워크, 비루트 사용자, 읽기 전용 루트, cap_drop, CPU/메모리/PID/로그 한도를 적용한다. 원본 소스는 실행 전후 유지하고, 카나리·간접 공격용 문서는 일회성 이미지에만 추가한다.

명시적인 manifest 또는 Python 리터럴 도구 스키마를 발견하면 연결된 agent 타깃을 자동 등록한다. 정의에 없는 권한을 추정해 확정하지 않고 검토 대상으로 표시한다. Atlas 세션/응답 형식은 자동 지원하고 다른 호환 에이전트는 audit-runtime.yaml과 audit-probes.yaml로 계약을 선언한다. 실행/공격 미지원과 실패는 NOT_RUN/ERROR로 남기며, mock 결과를 실모델 인젝션 방어 성능으로 해석하지 않는다. 현재 자동 실행 감사는 한 번에 업로드 프로젝트 하나를 지원한다. 격리 컨트롤러의 Docker 권한은 로컬 개발용 신뢰 경계이며 악성 다중 사용자 업로드 운영 환경에는 별도 전용 VM이 필요하다.


## 2026-10-20 — 실행 설정과 실제 LLM 검증

사용자 요청에 따라 실행 설정 화면, 암호화 비밀 저장소, 감사별 제한 토큰 기반 LLM 프록시, ZIP 전송 방식의 독립 실행 서버 구성을 추가한다. 실제 제공자 키는 타깃에 주입하지 않는다. 원격 실행용 Compose를 제공하며 실제 VM 생성은 포함하지 않는다. 현재는 승인된 로컬 Docker에서 컨트롤러와 프로젝트 볼륨을 분리한다. 실제 호출 여부와 공격 성공 여부를 보고서에서 구분한다.

사용자는 leak 가상 문서·도구 정의·공격 대화를 OpenAI Chat Completions에 보내는 실제 검증을 명시적으로 승인했다. 테스트 한도는 총 20회·80,000토큰·응답당 512토큰이며, 종료 후 해당 검증 프로젝트의 제공자 키 사본을 삭제한다.
