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
- [!] **완료 조건(실 LLM)**: vuln P01·P03·P05 중 ≥2 성공 / hardened 차단 — `OPENAI_API_KEY` 필요. `uv run pytest -m llm tests/e2e/test_probe_smoke.py`

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
- [~] H1 프롬프트 레지스트리 / H2 컨텍스트 / H3 도구 게이트 / H4 가드레일 / H5 평가기 / H6 관측 / H7 신뢰성 / H8 예산 — 코드 존재(`harness/{context,tools,guardrails,observability,reliability,budget,llm,prompts}`), H5 `harness/eval/` 은 비어 있음, 강의 용어 대조 미완
- [x] ReAct 조사 노드 `core/graph/nodes/react_investigator.py` (6스텝 상한·파일당 토큰 예산·H3 게이트·investigate.step·관찰 untrusted 래핑·감사기 인젝션 후보 기각 불가) — `create_react_agent` 대신 JSON 스텝 루프(DECISIONS #13). 테스트는 스크립트된 모델, 실 LLM 로 C19·C20 탐지는 키 필요
- [~] 적응형 probe 생성, probe_judge 라운드 루프 — 노드·프롬프트 존재(그래프 흐름 테스트는 LLM 없는 경로만), 실 LLM 검증 미실행
- [x] code_evidence_check 근거 검증 (그래프 흐름 테스트에서 파일·라인 대조, 불일치 시 NEEDS_REVIEW)
- [x] jobs.retry / retry-worker / jobs.dlq / dlq_messages / replay / FAULT_INJECTION — `tests/integration/test_retry_dlq.py` 7개 (3회 재시도 후 DLQ·백오프 증가·비재시도 즉시 DLQ·중복 skip·DLQ 적재/replay 새 message_id·장애 주입 계산). apply_fix 경로에서의 장애 주입 실환경 시나리오(`make fault-on`)는 미실행
- [ ] **완료 조건**: C19·C20 탐지, I01·I02 견고, 재시도·DLQ 테스트

## Phase 8 — 프론트엔드 + 평가 러너 (P0/P1)
- [x] 로그인, 개요, 감사 실행, 감사 진행(SSE), Findings, Finding 상세, 승인함, 수정·PR (P0) — `frontend/src/pages/*` vue-tsc·vite build 통과, nginx 프록시 확인. 브라우저 시연(리허설)은 미실행
- [x] 결합 위험, 레드팀 결과, 보고서, 품질, 트레이스, 지식 관리, Ops (P1)
- [~] 러너·matcher/scorer: 코드·에이전트 정적 기준선 채점 `harness/eval/code_eval.py`(S: 재현율 0.9, C19·C20 미탐, D03 오탐) 완료. 코드 감사 A/B/C 모델 비교(LLM)는 키 필요로 미구현
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

---

## 🔁 다음 세션 인수인계 (2026-10-19 중단 시점)

> **2026-10-20 갱신**: 범위는 코드 감사 + AI 에이전트 감사.

### 현재 상태
- 완료: Phase 0~6, Phase 7 대부분(ReAct, 재시도/DLQ), Phase 8 프론트 전 페이지, Phase 10 은 에이전트 점검 A1~A4 라이브러리(`core/agent_audit/`)만 남음.
- 검증됨(2026-10-20, 새 이미지): `make test` 94개, `make lint`(ruff·mypy core/harness/mcp_servers·vue-tsc), infra e2e 3건(전체 흐름·SSE·워커 재시작).
  - 코드 감사(정적 기준선): 정밀도 0.82 · 재현율 0.90 · 인젝션 견고성 1.0 (`uv run python -m harness.eval.code_eval`)
  - 에이전트 감사(demo-reset 직후 전체 흐름): 규칙 AGT-TOOL-001~004·AGT-SECRET-001·AGT-PROMPT-001~002, probe P03·P04·P07·P09, 결합 CORR-001·002 탐지
- 막힘: **LLM 키** — 셸의 `OPENAI_API_KEY` 는 OpenAI 가 401 로 거부. 실 LLM probe·ReAct 실검증·코드 감사 A/B/C 비교·LangSmith 는 미측정/미충족.

### 바로 다음에 할 일 (순서대로)
1. 유효한 `OPENAI_API_KEY`·`LLM_SMALL_MODEL` 을 `.env` 에 넣고 `uv run pytest -m llm tests/e2e/test_probe_smoke.py` (Phase 2 실 LLM 완료 조건), ReAct 로 C19·C20 탐지 실측.
2. 코드 감사 A/B/C 모델 비교(eval-worker, `config/models.yaml`).
3. 에이전트 점검 A1~A4 를 제품 흐름(감사 그래프·API)에 연결할지 결정 — 지금은 라이브러리·단위 테스트만 있음. 연결 시 기존 `rules/agent` 규칙과 중복 정리.
4. 브라우저 데모 리허설 1~9단계(프론트), LangSmith trace 1건, `make fault-on` 실환경 시나리오(`.env` 에 `FAULT_INJECTION=` 줄 필요).
5. Phase 9(P2): GitHub 모드, 외부 MCP 클라이언트, D_large_harness.

### 핵심 구조 메모
- 그래프: `backend/core/graph/build.py` (다이어그램 1:1), 노드 = `core/graph/nodes/*.py`, State = dict 저장 (`core/graph/state.py`).
- 의존성 주입: `config["configurable"]["deps"] = Deps(gateway=...)` (`core/graph/deps.py`). 테스트는 `inprocess_gateway()`, 실환경은 `http_gateway()`.
- MCP 도구: business `mcp_servers/business/tools/{query,execute,mutate}.py`, rag `mcp_servers/rag/tools/knowledge.py`. 응답 envelope·역할검사 = `mcp_servers/common.py`. H3 게이트 allowlist = `harness/tools.py` (새 도구 추가 시 여기 등록 필수).
- 수정안: 결정론 수정기 `core/fixes.py` → 서버가 edits 적용·diff 계산 (`mcp_servers/business/patches.py`).
- 워커: `workers/audit.py`(AuditRunner), `workers/retry.py`(retry·DLQ), 진입점 `workers/main.py`.
- API: `api/routers/audits.py`(audits/findings/approvals/fixes/report/SSE/traces), `knowledge.py`, `ops.py`, `webhooks.py`.
- 테스트 대역: `tests/fakes.py` GullibleAgent (LLM 없이 취약 에이전트 흉내, 실제 policy.py 적용).
- 간이 MCP Inspector: `cd backend && uv run python -m mcp_servers.inspect_cli --url http://localhost:8101/mcp list`
- 설계 변경 기록: `DECISIONS.md` #1~#25. (fault-on DLQ 시나리오는 N ≥ 12 — #25)

### 막힌 것
- (해결 2026-10-20) 유효한 OpenAI·LangSmith 키로 실 LLM 경로 검증 완료. 남은 막힘: `config/pricing.yaml` 가격 미기입.

- 2026-10-20: **evaluateCheck 보강 (Cloud 제외)** — 유효 키 확보(OPENAI·LangSmith, LLM_LARGE_MODEL=gpt-5.4-mini).
  - Harness: ReAct 8턴·120초·같은 도구 3회·도구 결과 8000자·관찰 요약(context_summarize@v1), 이메일·전화 마스킹, MCP ToolAnnotations(destructiveHint), trace_id=uuid5(audit) Kafka·span·LangSmith 공통, MultiServerMCPClient 전송, investigate 프롬프트 v2(카테고리 고정·'가짜 값' 주석 회피), 확정형 정적 규칙은 LLM 이 기각 불가, 근거 라인 보정 가드레일, 수정안 부분 적용(skipped_edits), probe 스코프 수정기(get_order :self).
  - 평가: `harness/eval/{scorer,runner,cli}.py`, `/eval-runs`, eval-worker(eval.requested), `make eval-code`, `/quality`(ISO 25010), CodeEvalPage(/eval), QualityPage.
  - 실 LLM 검증: probe 스모크 vuln 통과·hardened 통과 (취약 샘플 프롬프트 보강, DECISIONS #17), 감사 1건 Kafka 전체 흐름 COMPLETED(ReAct 26스텝, 해결 22·MANUAL 1), LangSmith root run 3개가 trace_id 1개로 묶임.
  - 문서: `docs/HARNESS.md`, `docs/diagrams/*.mmd`(docs/ 는 로컬 전용 정책), `DECISIONS.md` #17~#25.
  - 남은 것: config/pricing.yaml 가격 기입(f1_per_dollar), `make fault-on` 실환경, 외부 MCP 클라이언트.
- 2026-10-20: A/B/C 비교 실측 (반복 3회, `reports/eval_b582d34a.md`): code F1 A 0.53 / **B 0.88** / C 0.75, B 함정 오탐 0, Agent 재현율 1.0. 정적 회피 B 0.67 < C 0.83. /quality 미달 2건(재현율 0.88<0.9, 견고성 0.83<1). 실측 중 버그 7건 수정(evaluateCheck.md '실측 중 발견해 고친 문제'). reports 볼륨 api·eval-worker 공유.
