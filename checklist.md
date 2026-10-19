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
- [ ] Kafka envelope, producer, consumer(수동 커밋, 멱등 키)
- [ ] 토픽 생성 (11.1, 파티션 3)
- [ ] `harness/events.py` emit → Pub/Sub + Stream + DB
- [ ] AuditState (8.2), StateGraph 1차: intake → code/agent 서브그래프(병렬 Send) → correlate → report
- [ ] AsyncRedisSaver 체크포인터
- [ ] `POST /audits` → audit.requested, `GET /audits/{id}/events` SSE (Last-Event-ID)
- [ ] **완료 조건**: 감사 1건이 Kafka로 시작해 report까지 완료, SSE 이벤트 수신

## Phase 6 — 승인·수정·재검증 루프 (P0)
- [ ] human_approval interrupt / risk_accept / fix_generate / apply_fix(local) / wait_merge / re_verify / mark_manual
- [ ] business-mcp 변경 도구 + 역할 재검사 + 멱등성(fingerprint, idempotency_key) + 상태 전이 표
- [ ] `/approvals`, `/approvals/{id}/decision`, `/fixes/{id}/merge`, audit.resume
- [ ] **완료 조건**: 데모 2~6단계 e2e, 승인 없는 apply_fix → FORBIDDEN, 워커 재시작 후 재개 테스트

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

---

## 진행 로그
- 2026-10-19: PDF 분석, checklist.md 작성.
- 2026-10-19: Phase 0 완료 — make up 전 컨테이너 healthy, make test/lint 통과. (DECISIONS #3 Node 22)
- 2026-10-19: Phase 1 완료 — 13장 전 테이블 모델+Alembic 0001, JWT/bcrypt/RBAC/잠금/회전/블랙리스트, 시드. tests/integration/test_auth.py 6개 통과(로컬 postgres auditor_test DB 사용).
- 2026-10-19: Phase 2 완료(오프라인) — vuln-spring-app C01~C20/D01~D06/I01~I02, code.yaml 라인 검증 스크립트, target-agent(vuln/hardened, policy.py), agent.yaml, probes/base.yaml(P01~P12), demo-reset. 실 LLM 스모크는 키 필요.
- 2026-10-19: Phase 3 완료 — 규칙 엔진(regex/entropy/yaml_path/python/manifest), 코드 규칙 17 + Agent 규칙 7, C01~C18 18/18 탐지·C19/C20 미탐(의도)·함정 통과, business-mcp 조회7+실행3 도구, 서비스 토큰·역할·경로 탈출 거부, `python -m mcp_servers.inspect_cli list` 로 HTTP 확인.
- 2026-10-19: Phase 4 완료 — knowledge/*.md 6종, 헤더 청크(800/100), 하이브리드 검색(pgvector+tsvector, 1h 캐시+버전 무효화), rag-mcp 조회7·변경4 도구, 시드 인덱싱. 키 없으면 해시 임베딩(DECISIONS #4). permitAll 검색 1위 확인.
