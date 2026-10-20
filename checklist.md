 # AI Security Auditor — 개발 진행 체크리스트

> 원본 명세: `AI_Security_Auditor_SPEC.pdf` (v2, 37p) → `SPEC.md`에 텍스트 사본.
> **세션이 끊기면**: 이 파일에서 첫 번째 `[ ]` 항목부터 이어서 진행. 진행 중이던 항목은 `[~]`로 표시.
> 규칙: Phase 완료 조건을 테스트로 확인 후 다음 Phase. 명세와 다르게 구현하면 `DECISIONS.md`에 먼저 기록.
> 범례: `[x]` 완료 · `[~]` 진행 중 · `[ ]` 미착수 · `[!]` 막힘(사유 기재)

## 환경 메모
- 로컬: uv 0.12, Node 26 / pnpm 11, Docker 29 (데몬 기동 필요), Java 21, Python 3.12는 uv로 설치
- 저장소 루트 = 이 폴더 (명세의 `ai-security-auditor/`에 해당)

---

## Phase 0 — 저장소 골격 (P0)
- [x] 디렉터리 구조 생성 (19장)
- [x] `SPEC.md`, `DECISIONS.md`, `CLAUDE.md`, `README.md`
- [x] `.env.example` (20.3), `.gitignore`
- [x] `config/models.yaml`, `config/pricing.yaml`, `config/budget.yaml`
- [x] `backend/pyproject.toml` (uv, ruff, mypy, pytest, pytest-asyncio) + `uv.lock`
- [x] `backend/core/settings.py` (pydantic-settings)
- [x] `harness/masking.py` (`mask_secret`) + `harness/context.py` (`wrap_untrusted`) 기본 구현 + 단위 테스트
- [x] 서비스별 최소 엔트리포인트 (api /ops/health, mcp 서버, target-agent, workers) — healthcheck용
- [x] Dockerfile(backend 공용, target-agent, frontend)
- [x] `docker-compose.yml` (postgres, redis-stack, kafka KRaft, kafka-init, kafka-ui, business-mcp, rag-mcp, target-agent, api, audit/eval/retry-worker, frontend) + healthcheck + depends_on
- [x] `Makefile` (up, down, logs, seed, demo-reset, test, test-e2e, lint, eval, fault-on, fault-off)
- [x] frontend 골격 (Vue3+Vite+TS+Pinia+Router+ECharts) + `pnpm-lock.yaml`
- [x] git init + 첫 커밋
- [x] **완료 조건**: `make test` 통과, `make lint` 통과, `make up` 후 모든 컨테이너 healthy

## Phase 1 — DB + 인증 (P0)
- [x] SQLAlchemy 모델 (13장 전 테이블)
- [x] Alembic 마이그레이션 (vector, pgcrypto 확장 포함), api 기동 시 자동 실행
- [x] `api/security.py`: bcrypt(cost 12), JWT HS256 access 15m / refresh 7d, claims {sub, role, jti, exp}
- [x] Redis: refresh `auth:refresh:{jti}`, blacklist, 로그인 실패 5회/15분 잠금
- [x] `/auth/login`, `/auth/refresh`(회전), `/auth/logout`, `/me`, `/users`
- [x] `require_roles(*roles)` 의존성, DEVELOPER 리소스 범위 제한
- [x] 공통 오류 응답 `{"error": {code, message, details}}`, 페이지네이션
- [x] 시드 (admin/dev/auditor@demo.io)
- [x] **완료 조건**: 역할별 허용·거부 통합 테스트, 만료 토큰 401, 블랙리스트 401, 타인 대상 403

## Phase 2 — 샘플 대상 + 정답셋 (P0)
- [x] `targets/vuln-spring-app` C01~C20 심기 (모든 키 가짜 값, 파일 상단 "테스트용 가짜 값" 명시)
- [x] 오탐 함정 D01~D06
- [x] 감사기 인젝션 I01 (SecurityConfig C06 바로 위), I02 (README)
- [x] `datasets/ground_truth/code.yaml` (findings, decoys, auditor_injections, 라인 번호)
- [x] `targets/target-agent/app.py` (FastAPI + LangChain, POST /chat, 가짜 도구, 호출 기록)
- [x] `manifests/vuln.yaml`, `manifests/hardened.yaml`, `prompts/system_vuln.md`, `prompts/system_hardened.md` (카나리 `CANARY-7F3A-INTERNAL-DISCOUNT`)
- [x] `datasets/ground_truth/agent.yaml` (A01~A07)
- [x] `datasets/probes/*.yaml` (P01~P12), `datasets/VERSION`
- [x] `make demo-reset` (/workspace git 초기화, agent-config 복사)
- [x] 정답셋 위치 검증 스크립트 + 테스트
- [x] **완료 조건(오프라인)**: 위치 검증 통과, 정책 레이어 vuln 실행/hardened 차단 테스트 통과
- [x] **완료 조건(실 LLM)**: vuln P01·P03·P05 중 ≥2 성공 / hardened 차단 — 2026-10-20 gpt-5.4-nano 로 둘 다 통과 (`uv run pytest -m llm tests/e2e/test_probe_smoke.py`, 취약 샘플 프롬프트 보강 DECISIONS #17)

## Phase 3 — 규칙 엔진 + business-mcp 조회·실행 (P0)
- [x] 규칙 YAML 형식 (7.1) + 로더
- [x] detectors: regex, entropy, yaml_path, manifest, python(파일 존재)
- [x] 코드 규칙 17개 (7.2) `rules/code/*.yaml`
- [x] Agent 규칙 7개 (7.3) `rules/agent/*.yaml`
- [x] MCP 공통 응답·오류 스키마 (9.2), 서비스 토큰 + 맥락 헤더 검증
- [x] business-mcp 조회 도구: list_audit_targets, list_repo_files, read_repo_file(400줄, 마스킹, 경로 탈출 거부), search_repo(1s 타임아웃), get_agent_manifest, get_findings, get_audit_history
- [x] 실행 도구: run_static_scan(커밋 캐시), run_agent_rule_check, run_injection_probe(allowlist, 30s)
- [x] **완료 조건**: C01~C18 ≥80% 탐지 테스트, 경로 탈출 거부 테스트, 도구 목록 확인

## Phase 4 — rag-mcp + 지식 문서 (P0)
- [x] `knowledge/*.md` 6종 (팀 요약본)
- [x] 청크(헤더 기준, 800토큰, overlap 100), 임베딩, 하이브리드 검색(pgvector + tsvector), 캐시 1h
- [x] 조회 도구 6개, 변경 도구 4개 (9.4)
- [x] seed 시 인덱싱
- [x] **완료 조건**: "permitAll 올바른 설정" 검색 시 spring_security_guide 상위 3위 내

## Phase 5 — Kafka + Redis 이벤트 + 그래프 1차 + SSE (P0)
- [x] Kafka envelope, producer, consumer(수동 커밋, 멱등 키)
- [x] 토픽 생성 (11.1, 파티션 3) — kafka-init 8개 토픽 확인
- [x] `harness/events.py` emit → Pub/Sub + Stream + DB
- [x] AuditState (8.2), StateGraph: intake → code/agent 서브그래프(병렬 Send) → correlate → (승인·수정·재검증) → report — `tests/integration/test_graph_flow.py`
- [x] AsyncRedisSaver 체크포인터 (실환경 워커 로그·재시작 재개로 확인)
- [x] `POST /audits` → audit.requested, `GET /audits/{id}/events` SSE (Last-Event-ID) — 첫 이벤트 전 연결 시 스트림이 닫히던 버그 수정
- [x] **완료 조건**: 감사 1건이 Kafka로 시작해 report까지 완료, SSE 이벤트 수신 — `tests/e2e/test_demo_flow.py` (`-m infra`)

## Phase 6 — 승인·수정·재검증 루프 (P0)
- [x] human_approval interrupt / risk_accept / fix_generate / apply_fix(local) / wait_merge / re_verify / mark_manual (그래프 테스트 통과)
- [x] business-mcp 변경 도구 + 역할 재검사 + 멱등성(fingerprint, idempotency_key) + 상태 전이 표 (+merge_fix)
- [x] `/approvals`, `/approvals/{id}/decision`, `/fixes/{id}/merge`, audit.resume
- [x] **완료 조건**: 데모 2~6단계 e2e, 승인 없는 apply_fix → FORBIDDEN, 워커 재시작 후 재개 테스트

## Phase 7 — Harness H1~H8 + Agentic + 재시도/DLQ (P1)
- [x] H1~H8 + 강의 8요소 대조 완료 (`docs/HARNESS.md`, 로컬 전용) — Agent Loop 8턴·120초·같은 도구 3회, 도구 결과 8000자, 관찰 요약, PII 마스킹, ToolAnnotations, trace_id, H5 평가기(`harness/eval/`)
- [x] ReAct 조사 노드 `core/graph/nodes/react_investigator.py` (JSON 스텝 루프, DECISIONS #13) — 실 LLM 검증 완료(도구 선택·사유 이벤트). 프롬프트 v3: 카테고리 정의·위험 파일 읽기 강제·확정 정적 규칙 기각 불가
- [x] 적응형 probe 생성, probe_judge 라운드 루프 — 실 LLM 감사에서 동작 확인
- [x] code_evidence_check 근거 검증 + 근거 라인 보정(스니펫으로 실제 라인 재탐색)
- [x] jobs.retry / retry-worker / jobs.dlq / dlq_messages / replay / FAULT_INJECTION — `tests/integration/test_retry_dlq.py` 7개 (3회 재시도 후 DLQ·백오프 증가·비재시도 즉시 DLQ·중복 skip·DLQ 적재/replay 새 message_id·장애 주입 계산). apply_fix 경로에서의 장애 주입 실환경 시나리오(`make fault-on`)는 미실행
- [~] **완료 조건**: C19·C20 탐지 — B 재현율 0.67 (6회 중 4회), I01·I02 견고성 0.83 (목표 1.0 미달), 재시도·DLQ 테스트 통과

## Phase 8 — 프론트엔드 + 평가 러너 (P0/P1)
- [x] 로그인, 개요, 감사 실행, 감사 진행(SSE), Findings, Finding 상세, 승인함, 수정·PR (P0) — `frontend/src/pages/*` vue-tsc·vite build 통과, nginx 프록시 확인. 브라우저 시연(리허설)은 미실행
- [x] 결합 위험, 레드팀 결과, 보고서, 품질, 트레이스, 지식 관리, Ops (P1)
- [x] A/B/C 러너·채점기 `harness/eval/{runner,scorer,cli}.py`, `/eval-runs` API, eval-worker, `make eval`, 비교 화면(`/eval`), `/quality` API — 실측 반복 3회 `reports/eval_b582d34a.md`
- [ ] **완료 조건**: 데모 1~9단계 리허설

## Phase 9 — 확장 (P2)
- [ ] GitHub 모드, 외부 MCP 클라이언트 연동, D_large_harness

## Phase 10 — 에이전트 확장 점검 A1~A4
> 에이전트 감사의 결정론 확장 점검. 현재 라이브러리 수준이며 감사 그래프·API 에는 연결되지 않았다.
- [~] A1 승인 없는 변경 도구 · A4 도구 설명 오염 — `core/agent_audit/checks.py` `check_tools`, 테스트 `tests/unit/test_agent_checks.py`
- [~] A2 호출 주체 정책 누락(HOLD) · A3 도구 조합(CONDITIONAL) — 같은 파일·테스트 포함. 실행 진입점(API·워커)은 없고 라이브러리 수준. 제품 흐름에 연결하는 작업은 미착수
- [ ] 기존 `rules/agent` 매핑과 A1~A4 결과 정리

---

## 진행 로그
- 2026-10-19: PDF 분석, checklist.md 작성.
- 2026-10-19: Phase 0 완료 — make up 전 컨테이너 healthy, make test/lint 통과. (DECISIONS #3 Node 22)
- 2026-10-19: Phase 1 완료 — 13장 전 테이블 모델+Alembic 0001, JWT/bcrypt/RBAC/잠금/회전/블랙리스트, 시드. tests/integration/test_auth.py 6개 통과(로컬 postgres auditor_test DB 사용).
- 2026-10-19: Phase 2 완료(오프라인) — vuln-spring-app C01~C20/D01~D06/I01~I02, code.yaml 라인 검증 스크립트, target-agent(vuln/hardened, policy.py), agent.yaml, probes/base.yaml(P01~P12), demo-reset. 실 LLM 스모크는 키 필요.
- 2026-10-19: Phase 3 완료 — 규칙 엔진(regex/entropy/yaml_path/python/manifest), 코드 규칙 17 + Agent 규칙 7, C01~C18 18/18 탐지·C19/C20 미탐(의도)·함정 통과, business-mcp 조회7+실행3 도구, 서비스 토큰·역할·경로 탈출 거부, `python -m mcp_servers.inspect_cli list` 로 HTTP 확인.
- 2026-10-19: Phase 4 완료 — knowledge/*.md 6종, 헤더 청크(800/100), 하이브리드 검색(pgvector+tsvector, 1h 캐시+버전 무효화), rag-mcp 조회7·변경4 도구, 시드 인덱싱. 키 없으면 해시 임베딩(DECISIONS #4). permitAll 검색 1위 확인.
- 2026-10-19: Phase 5/6 진행 중 — 그래프 전 노드·서브그래프·business-mcp 변경 도구·결정론 수정기 완성, in-process 그래프 테스트(승인→수정→병합→재검증→보고서, 반려, FORBIDDEN, 재시작 재개) 통과. 남은 것: Kafka 워커, API(audits/approvals/fixes/SSE), Redis 체크포인터 실환경 확인.

- 2026-10-19: Phase 5·6 완료 — Docker 실환경 e2e 3건(승인→병합→보고서, SSE, 승인 대기 중 audit-worker 재시작 후 재개) 통과, make test 66개·lint·mypy 통과.

- 2026-10-19: 체크리스트 전수 점검 — 검증된 항목만 [x], 코드만 있고 미검증인 Phase 7·8 항목은 [~] 로 표기.

- 2026-10-19: Phase 7 — ReAct 조사 노드(테스트 6), 재시도/DLQ 통합 테스트(7). make test 전체 통과.

- 2026-10-19: Phase 8 프론트 전 페이지(19개 라우트), 코드 정적 기준선 채점. make test 137개·lint 통과.
- 2026-10-20: Cloud 감사 제거(DECISIONS #16) — 파이프라인·API·MCP 도구·화면·`/eval`·평가 러너·데이터셋 삭제. 에이전트 점검 A1~A4 는 `backend/core/agent_audit/` 로 분리(Phase 10). make lint·test 94개·infra e2e 3건 통과.
- 2026-10-20: 코드·에이전트 감사 QA — `reports/qa-2026-10-20/QA_REPORT.md` (Q01~Q16, S1~S7). 보안 수정 계획 `docs/security_QA_ver.01.md`. 수정은 아직 미실행.
- 2026-10-20: **실 LLM 검증 + evaluateCheck 보강 (Cloud 제외)** — 유효 키(OpenAI·LangSmith), `LLM_LARGE_MODEL=gpt-5.4-mini`.
  - 평가표 대비 점검 `evaluateCheck.md` 작성 → 부족 9건 모두 반영 후 실측으로 갱신.
  - Harness 보강: Agent Loop(파일당 LLM 8턴·120초·같은 도구 3회), 도구 결과 8000자 절단, 관찰 6000토큰 초과 시 요약(`context_summarize@v1`), 이메일·전화 마스킹, MCP ToolAnnotations(`destructiveHint`·`readOnlyHint`), trace_id=uuid5(audit) 를 Kafka·span·LangSmith 공통 사용, `MultiServerMCPClient` 전송.
  - 실 LLM 검증: probe 스모크 vuln·hardened 통과, Kafka 경유 감사 1건 COMPLETED(ReAct 26스텝, 해결 22·MANUAL 1), LangSmith root run 3개 = trace_id 1개 (토큰 89,898 DB 와 일치).
  - 평가: A/B/C 러너·채점기, `/eval-runs`, eval-worker, `/quality`(ISO 25010), CodeEvalPage·QualityPage, `migrations/0003_trace_id.py`.
  - **A/B/C 실측 (반복 3회, `reports/eval_b582d34a.md`)**: code F1 A 0.53 / **B 0.88** / C 0.75, B 함정 오탐 0, Agent 재현율 1.0, 심각도 정확도 0.87. 정적 회피 B 0.67 < C 0.83, 토큰 B ≈ C×8. `/quality` 미달 2건(재현율 0.88<0.9, 감사기 견고성 0.83<1).
  - 실측 중 발견·수정한 버그: ① system 프롬프트 자리표시자 미치환 ② 전화번호 마스킹이 UUID 손상(간헐 테스트 실패 원인) ③ LLM edit 1개 실패로 수정안 전체 폐기 → 부분 적용 ④ '테스트용 가짜 값' 주석에 속아 확정 탐지 기각 ⑤ 자유 카테고리·틀린 라인 → 카테고리 고정·라인 보정 ⑥ 위험 파일 미열람 결론 ⑦ 평가 러너 게이트 공유·실행 실패 시 중단.
  - 테스트 격리: ReAct 테스트 fixture 가 `db_ready` 를 거치게, 테스트 DB 는 스키마를 새로 만들게 수정.
  - DECISIONS #17~#25 추가 (루트 `DECISIONS.md`).
- 2026-10-20: **원격 병합 → `develop` 브랜치 푸시** (`20367ed`, Claude 작성자 표기 없음). 원격 main 의 Cloud 삭제·`docs/` 로컬 전용 정책을 따름. migration 0003 의 부모를 0001 로(0002 삭제됨), 새 DB 에서 IF NOT EXISTS 로 안전하게. `make eval` = 코드 감사 A/B/C. 병합 후 make test 109개·lint 통과. `docs/HARNESS.md`·`docs/diagrams/*` 는 로컬에만 있음.

---

## 🔁 다음 세션 인수인계 (2026-10-20 기준)

> 범위: 코드 감사 + AI 에이전트 감사 (Cloud 제외). 작업 브랜치: **`develop`** (원격 푸시됨, main 미병합).

### 현재 상태
- 완료: Phase 0~6, Phase 7(완료 조건 일부 미달), Phase 8 기능, Phase 10 은 에이전트 점검 A1~A4 라이브러리(`core/agent_audit/`)만.
- 검증됨: `make test` 109개, `make lint`(ruff·mypy·vue-tsc), 실 LLM probe 스모크·감사 전체 흐름·LangSmith, A/B/C 실측 3회.
- 평가표 대비 상태: `evaluateCheck.md` (항목별 근거·실측표·남은 것).

### 바로 다음에 할 일 (순서대로)
1. `config/pricing.yaml` 에 gpt-5.4-nano·gpt-5.4-mini 가격 기입 → `make eval` 다시 실행해 cost_usd·f1_per_dollar 산출.
2. 목표 미달 분석: 감사기 견고성 0.83 (I01 1회 DETECTED_ONLY = C06 누락), 정적 회피 B 0.67 (C 0.83 보다 낮음), 재현율 0.88 (목표 0.9).
3. `make fault-on N=2`(재시도 후 성공)·`N=12`(DLQ) 실환경 시연, 브라우저 데모 리허설 1~9단계.
4. QA 보안 수정 (`reports/qa-2026-10-20/QA_REPORT.md`, `docs/security_QA_ver.01.md`) — 미실행.
5. 에이전트 점검 A1~A4 를 감사 그래프·API 에 연결할지 결정. Phase 9(GitHub 모드, 외부 MCP 클라이언트, D_large_harness).

### 핵심 구조 메모
- 그래프: `backend/core/graph/build.py` (다이어그램 1:1), 노드 = `core/graph/nodes/*.py`, State = dict 저장 (`core/graph/state.py`).
- 의존성 주입: `config["configurable"]["deps"] = Deps(gateway=...)` (`core/graph/deps.py`). 테스트는 `inprocess_gateway()`, 실환경은 `http_gateway()`(MultiServerMCPClient).
- MCP 도구: business `mcp_servers/business/tools/{query,execute,mutate}.py`, rag `mcp_servers/rag/tools/knowledge.py`. 응답 envelope·역할검사·ToolAnnotations = `mcp_servers/common.py`. H3 게이트 allowlist·도구 분류 = `harness/tools.py` (새 도구 추가 시 여기 등록 필수).
- 수정안: 결정론 수정기 `core/fixes.py` → 서버가 edits 적용·diff 계산·실패 edit 만 건너뜀 (`mcp_servers/business/patches.py`).
- 프롬프트: `harness/prompts/*_v{n}.yaml` (최신 버전 자동 선택, system 의 `{자리표시자}` 도 `render_system()` 으로 채움).
- 평가: `harness/eval/{runner,scorer,cli}.py`, 실행 `make eval` (eval-worker 컨테이너), 결과 `reports/eval_*.md`(공유 볼륨, 커밋은 `git add -f`).
- 워커: `workers/audit.py`(AuditRunner), `workers/eval.py`, `workers/retry.py`(retry·DLQ), 진입점 `workers/main.py`.
- API: `api/routers/audits.py`, `eval_runs.py`, `quality.py`, `knowledge.py`, `ops.py`, `webhooks.py`.
- 테스트 대역: `tests/fakes.py` GullibleAgent. 간이 MCP Inspector: `cd backend && uv run python -m mcp_servers.inspect_cli list`.
- 설계 변경 기록: `DECISIONS.md` #1~#25.

### 막힌 것
- 없음. (LLM 키 해결됨. 비용 계산만 가격 미기입으로 0)
