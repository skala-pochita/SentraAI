# AI Security Auditor — 개발 진행 체크리스트

> 원본 명세: `AI_Security_Auditor_SPEC.pdf` (v2, 37p) → `docs/SPEC.md`에 텍스트 사본.
> **세션이 끊기면**: 이 파일에서 첫 번째 `[ ]` 항목부터 이어서 진행. 진행 중이던 항목은 `[~]`로 표시.
> 규칙: Phase 완료 조건을 테스트로 확인 후 다음 Phase. 명세와 다르게 구현하면 `docs/DECISIONS.md`에 먼저 기록.
> 범례: `[x]` 완료 · `[~]` 진행 중 · `[ ]` 미착수 · `[!]` 막힘(사유 기재)

## 환경 메모
- 로컬: uv 0.12, Node 26 / pnpm 11, Docker 29 (데몬 기동 필요), Java 21, Python 3.12는 uv로 설치
- 저장소 루트 = 이 폴더 (명세의 `ai-security-auditor/`에 해당)

---

## Phase 0 — 저장소 골격 (P0)
- [x] 디렉터리 구조 생성 (19장)
- [x] `docs/SPEC.md`, `docs/DECISIONS.md`, `CLAUDE.md`, `README.md`
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
- [ ] H1 프롬프트 레지스트리 / H2 컨텍스트 / H3 도구 게이트 / H4 가드레일 / H5 평가기 / H6 관측 / H7 신뢰성 / H8 예산
- [ ] ReAct 조사 노드 (create_react_agent, 6스텝, investigate.step 이벤트)
- [ ] 적응형 probe 생성, probe_judge 라운드 루프
- [ ] code_evidence_check 근거 검증
- [ ] jobs.retry / retry-worker / jobs.dlq / dlq_messages / replay / FAULT_INJECTION
- [ ] **완료 조건**: C19·C20 탐지, I01·I02 견고, 재시도·DLQ 테스트

## Phase 8 — 프론트엔드 + 평가 러너 (P0/P1)
- [ ] 로그인, 개요, 감사 실행, 감사 진행(SSE), Findings, Finding 상세, 승인함, 수정·PR (P0)
- [ ] 결합 위험, 레드팀 결과, 보고서, 모델 비교, 품질, 트레이스, 지식 관리, Ops (P1)
- [ ] A/B/C 러너, matcher/scorer, `reports/eval_{id}.md`, `make eval`
- [ ] **완료 조건**: 데모 1~9단계 리허설, make eval 결과 /eval 표시

## Phase 9 — 확장 (P2)
- [ ] GitHub 모드, 외부 MCP 클라이언트 연동, D_large_harness

## Phase 10 — Agent·Cloud 감사 확장 (기획서 `AI_Agent_Cloud_Audit_Plan.md` 개정판 6, DECISIONS #11~#16)
> Phase 0~6 의 기존 코드·에이전트 감사 위에 얹는다. 기획서의 이틀 MVP 우선순위: 인증→감사→근거 조회→승인 대기→변경 도구→재개→결과 표시 시연 완주, 그 다음 최종 8묶음 A/B 각 1회(16회).
### 10-1 입력·스키마 (기획서 5장)
- [x] `evidence_bundle.json` 스키마 + 참조 ID·크기·금지 필드 검증 — `core/cloud/bundle.py` `parse_bundle`
- [~] 증거 저장/조회 (`evidence_id` 유지) — 금지 필드(findings/severity/ground_truth 등) 거부는 완료, DB 저장·API·MCP 조회 도구 미착수
- [ ] 출력 Finding 확장: 대상 자원, 판정 상태(결함 확인/조건부 위험/판단 보류), 근거 ID·위치, 위험 경로, 성립 조건, 추가 확인 항목 + 정상 판정·미점검 항목 기록
### 10-2 감사 범위 규칙 (기획서 4장)
- [~] A1(승인 없는 변경 도구)·A4(도구 설명 오염 패턴) 결정론 점검 완료 — A2(역할별 접근)·A3(도구 조합)·기존 `rules/agent` 매핑 정리 미착수
- [x] B1 계층 간 경로(도구→실행 역할→자원): 명시된 mappings 만 사용, 없는 연결 생성 금지, Deny 로 끊긴 경로는 cleared — `core/cloud/checks.py` `build_paths`
- [x] C1 IAM / C2 네트워크 / C3 저장소 결정론 점검 (`config/cloud_checks.yaml` 임계값) — 별표/PassRole 만으로 상승 확정 금지(CONDITIONAL). 규칙 YAML 화는 미정
- [x] 판단 보류(HOLD): 호출자 접근 정책 누락 시 B1 위험 미확정 + `needs` 로 필요한 증거 명시
- [ ] D1 감사자 인젝션 방어(도구 설명·파일 속 지시 무시) — 위반 사건 4종(결함 누락/허위 근거/수신자 변경/무단 호출) 판정기
### 10-3 그래프·Harness (기획서 6장)
- [ ] 노드 매핑: load_evidence→plan_audit→collect→assess→build_paths→verify(회귀 1회)→draft_report→approval→execute_action→finalize
- [ ] 예산: 8 LLM 턴, 활성 120초(승인 대기 제외, 재개 시 초기화 금지), 동일 도구 3회, 429/503 재시도 2회, 남은 30초 이하면 collect/repair 생략 (`config/budget.yaml` `cloud_audit`)
- [ ] `read_resource` 가상 도구 (허용 URI·버전 검증, 작업당 3회) + rag-mcp `guidance://security-baseline/v1` Resource
- [ ] 마스킹: 이메일·전화·키·토큰, ID 는 일관 치환으로 B1 연결 유지
- [ ] 변경 도구 `create_remediation_ticket`: 승인 검증·인수 SHA-256 해시·`taskId+actionId` 멱등 (기존 `fingerprint/idempotency_key` 재사용 여부 점검)
- [ ] 6개 이벤트(TOKEN/TOOL_START/TOOL_END/APPROVAL_REQUIRED/DONE/FAILED) 매핑, `active_elapsed_ms` 승인 이벤트 포함
- [ ] `/api/ai/tasks` 5개 파사드 (DECISIONS #12) + 실행 기한 초과 RUNNING → FAILED 스케줄러
- [ ] 관측: LangSmith trace 1건 확인(키 필요), 불가 시 JSONL 로그 + trace_id=null 및 미충족 표기
### 10-4 평가 (기획서 8~11장)
- [ ] 개발 3묶음 + 최종 T01~T08 정답셋 `datasets/ground_truth/cloud/*.yaml` (IAM Vulnerable·TerraGoat·DVMCP·MCPTox 선정 사례, 정상 반례·정보 부족 포함, 공개 자료 commit·라이선스 기록)
- [ ] A(단순 조회 루프)/B(전체 그래프) 공통 실행기 — 같은 도구·마스킹·예산·스키마, 서버 측 모의 저장소
- [ ] 채점: 정밀도/재현율/F1, 경로 탐지율·정밀도, 근거 정확도, 판단 보류 정확성, 재검증 정확도, 인젝션 실패율, 정상 감사 완료율, 비용·시간 (분모 0 = 해당 없음)
- [ ] 16회 실행(8묶음×A/B) → 결과 JSON → 정적 비교 화면. 미실행 지표는 "미측정"
- [ ] 확장(여유 시): 3회 반복 → E1 Agent Audit / E2 Checkov → D1 변형(T01·T06) → 수정본 재검증
- [ ] **완료 조건**: 기획서 11장 제품 동작 시험 13항목 통과, 최종 8묶음 A/B 각 1회 채점 (LLM 키 없으면 "평가 미완료" 로 표기)

---

## 진행 로그
- 2026-10-19: PDF 분석, checklist.md 작성.
- 2026-10-19: Phase 0 완료 — make up 전 컨테이너 healthy, make test/lint 통과. (DECISIONS #3 Node 22)
- 2026-10-19: Phase 1 완료 — 13장 전 테이블 모델+Alembic 0001, JWT/bcrypt/RBAC/잠금/회전/블랙리스트, 시드. tests/integration/test_auth.py 6개 통과(로컬 postgres auditor_test DB 사용).
- 2026-10-19: Phase 2 완료(오프라인) — vuln-spring-app C01~C20/D01~D06/I01~I02, code.yaml 라인 검증 스크립트, target-agent(vuln/hardened, policy.py), agent.yaml, probes/base.yaml(P01~P12), demo-reset. 실 LLM 스모크는 키 필요.
- 2026-10-19: Phase 3 완료 — 규칙 엔진(regex/entropy/yaml_path/python/manifest), 코드 규칙 17 + Agent 규칙 7, C01~C18 18/18 탐지·C19/C20 미탐(의도)·함정 통과, business-mcp 조회7+실행3 도구, 서비스 토큰·역할·경로 탈출 거부, `python -m mcp_servers.inspect_cli list` 로 HTTP 확인.
- 2026-10-19: Phase 4 완료 — knowledge/*.md 6종, 헤더 청크(800/100), 하이브리드 검색(pgvector+tsvector, 1h 캐시+버전 무효화), rag-mcp 조회7·변경4 도구, 시드 인덱싱. 키 없으면 해시 임베딩(DECISIONS #4). permitAll 검색 1위 확인.
- 2026-10-19: Phase 5/6 진행 중 — 그래프 전 노드·서브그래프·business-mcp 변경 도구·결정론 수정기 완성, in-process 그래프 테스트(승인→수정→병합→재검증→보고서, 반려, FORBIDDEN, 재시작 재개) 통과. 남은 것: Kafka 워커, API(audits/approvals/fixes/SSE), Redis 체크포인터 실환경 확인.

- 2026-10-19: 기획서(개정판 6) 반영 — Phase 10(Agent·Cloud 감사) 추가, DECISIONS #11~#16. test_business_mcp 타임아웃 테스트의 PROBE_ALLOWLIST 누락 수정.

- 2026-10-19: Phase 5·6 완료 — Docker 실환경 e2e 3건(승인→병합→보고서, SSE, 승인 대기 중 audit-worker 재시작 후 재개) 통과, make test 66개·lint·mypy 통과.

- 2026-10-19: Phase 10 착수 — `core/cloud/{bundle,checks}.py`, `config/cloud_checks.yaml`, `tests/unit/test_cloud_checks.py`(17개). make test 83개·lint·mypy 통과.

---

## 🔁 다음 세션 인수인계 (2026-10-19 중단 시점)

### 현재 상태
- 완료: Phase 0~6 (Docker 실환경 e2e 3건 포함 — `cd backend && uv run pytest -m infra tests/e2e/test_demo_flow.py`, 전제 `make up`). Phase 10 은 오프라인 결정론 부분(`core/cloud`)까지.
- 검증됨: `make test` 83개, `make lint`(ruff·mypy·vue-tsc) 통과.

### 바로 다음에 할 일 (순서대로)
1. Phase 10-1 남은 것: 증거 묶음 DB 저장·API·business-mcp 조회 도구, Finding 확장(판정 상태·경로·성립 조건), `/api/ai/tasks` 파사드.
2. Phase 10-2 남은 것: A2·A3, D1 판정기. 그래프에 `core/cloud/checks.audit_bundle` 을 결정론 선행 단계로 연결(load_evidence/assess/build_paths/verify 노드 매핑).
3. Phase 10-3: `config/budget.yaml` `cloud_audit` 블록(8턴·120초·동일 도구 3회), read_resource, 티켓 도구, 기한 초과 스케줄러.
4. Phase 7: ReAct 조사 노드 `core/graph/nodes/react_investigator.py` (아직 없음), retry/DLQ 통합 테스트.
5. Phase 10-4 / 8: 정답셋 T01~T08, A/B 러너·채점, 프론트. (LLM 키 필요한 부분은 미측정 표기)

### 핵심 구조 메모
- 그래프: `backend/core/graph/build.py` (다이어그램 1:1), 노드 = `core/graph/nodes/*.py`, State = dict 저장 (`core/graph/state.py`).
- 의존성 주입: `config["configurable"]["deps"] = Deps(gateway=...)` (`core/graph/deps.py`). 테스트는 `inprocess_gateway()`, 실환경은 `http_gateway()`.
- MCP 도구: business `mcp_servers/business/tools/{query,execute,mutate}.py`, rag `mcp_servers/rag/tools/knowledge.py`. 응답 envelope·역할검사 = `mcp_servers/common.py`. H3 게이트 allowlist = `harness/tools.py` (새 도구 추가 시 여기 등록 필수).
- 수정안: 결정론 수정기 `core/fixes.py` → 서버가 edits 적용·diff 계산 (`mcp_servers/business/patches.py`).
- 워커: `workers/audit.py`(AuditRunner), `workers/retry.py`(retry·DLQ), 진입점 `workers/main.py`.
- API: `api/routers/audits.py`(audits/findings/approvals/fixes/report/SSE/traces), `knowledge.py`, `ops.py`, `webhooks.py`.
- 테스트 대역: `tests/fakes.py` GullibleAgent (LLM 없이 취약 에이전트 흉내, 실제 policy.py 적용).
- 간이 MCP Inspector: `cd backend && uv run python -m mcp_servers.inspect_cli --url http://localhost:8101/mcp list`
- 설계 변경 기록: `docs/DECISIONS.md` #1~#10. (fault-on DLQ 시나리오는 N=2 가 아니라 큰 값 필요 — 인라인 재시도 3회×전달 4회. 아직 DECISIONS 미기록)

### 막힌 것
- `OPENAI_API_KEY` 없음 → 실 LLM 경로(target-agent probe, ReAct, LLM 판정, A/B/C 비교) 미검증. `.env` 에 키·`LLM_SMALL_MODEL`·`LLM_LARGE_MODEL` 기입 필요.
