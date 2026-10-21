# 변경 사항 (2026-10-21, ISO/IEC 25010 미달 항목 보완)

기준: 커밋 `b558d2b` (Feat: ZIP upload) 이후 작업 트리. 아직 커밋하지 않았다.
상세 근거는 `DECISIONS.md` "2026-10-21 — ISO/IEC 25010 미달 항목 보완", 측정 결과는 `isoReport.md`.

## 1. LangGraph 플로우

**변경 없음.** `core/graph/build.py` 는 `b558d2b` 와 같다.

```
intake ─┬─ code_audit:  code_static_scan → code_investigate → code_evidence_check ─┐
        ├─ agent_audit: agent_static_check → prepare_sandbox → probe_generate      │
        │               → probe_run → probe_judge ─(불확실 & 라운드<2)→ probe_generate
        │                                        └─────────────────────────────────┤
        └──────────────────────────────────────────────────────────────────────→ correlate → report
```

### 노드 내부 동작 변경
| 노드 | 변경 |
|---|---|
| `react_investigator` | LLM 오탐 판정 보류 가드레일. 아래 후보는 `FALSE_POSITIVE` 대신 `NEEDS_REVIEW`(confidence ≤ 0.6)로 남긴다: ① 자격증명 계열(`HARDCODED_SECRET`, `HARDCODED_CREDENTIAL`, `SECRET_LEAK`, `COMMENT_SENSITIVE`)이면서 테스트 경로가 아닌 것 ② 같은 파일에 감사기 인젝션(`AUDITOR_INJECTION_ATTEMPT`) 후보가 있는 것 |
| `correlate`, `probe_generate`, `probe_judge`, `report` | 함수 분리 리팩터링만. 기존 코드와 차등 테스트로 결과 동일 확인 |

### 삭제한 노드 파일 (이미 그래프에서 빠져 있던 미사용 코드)
`human_approval`, `risk_accept`, `fix_generate`, `llm_fixer`, `apply_fix`, `wait_merge`, `re_verify`, `mark_manual`
— 함께 `core/fixes.py`, `harness/prompts/fix_generate_v1.yaml`, `config/models.yaml`·`harness/events.py` 의 해당 항목 삭제. DB 테이블은 유지.

## 2. 아키텍처

서비스 구성(13개)은 그대로다.

| 영역 | 변경 | 파일 |
|---|---|---|
| audit-worker | `audit.requested` 만 처리. `audit.resume` 은 로그 후 무시. 체크포인트(`snap.next`)가 있으면 이어서 실행 | `workers/audit.py` |
| Business MCP | `request_approval`, `create_fix_proposal`, `apply_fix`, `merge_fix`, `register_risk_acceptance` 삭제. 쓰기 도구는 `create_finding`, `update_finding_status`, `save_report` 3개 | `mcp_servers/business/tools/mutate.py`, `mcp_servers/business/patches.py`(삭제) |
| ToolGate | `RETIRED_TOOLS` 추가 — 삭제된 도구 이름 호출 차단. 삭제 노드의 허용 목록 정리 | `harness/tools.py` |
| 장애 주입 | `FAULT_INJECTION=rate_limit:N` 이 `run_static_scan` 을 감사·대상별 N회 실패시킴 (구 `github_rate_limit:` 도 허용) | `mcp_servers/business/tools/execute.py`, `Makefile` |
| API | `/quality` 라우터 삭제. 결과 조회(findings, risk-paths, probes, report, report.md, traces, finding PATCH)를 `results.py` 로 분리 — URL 동일 | `api/routers/quality.py`(삭제), `api/routers/audits.py`, `api/routers/results.py`(신규), `api/main.py` |
| 보안 헤더 (API) | X-Frame-Options DENY, nosniff, Referrer-Policy no-referrer, CSP `default-src 'none'` (/docs·/redoc·/openapi.json 제외) | `api/main.py` |
| 보안 헤더 (nginx) | CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy. `server_tokens off`, `/api/` 중복 헤더 숨김 | `frontend/nginx.conf` |
| 서버 헤더 | api·llm-proxy·sandbox uvicorn `--no-server-header`, MCP 서버 `server_header=False` | `docker-compose.yml`, `mcp_servers/common.py` |
| 복구성 | postgres·redis·kafka·frontend 에 `restart: unless-stopped` (13개 전부 적용) | `docker-compose.yml` |
| 샌드박스 | 만료 컨테이너 정리기를 FastAPI lifespan 으로 이동, 실패 시 경고 로그 | `sandbox/server.py` |
| 평가 러너 | 실행 전 작업공간이 정답셋과 같은지 검사(`workspace_drift`). 다르면 LLM 호출 전에 VALIDATION 오류 → `make demo-reset` 안내 | `harness/eval/runner.py` |

## 3. 프론트엔드

| 변경 | 파일 |
|---|---|
| Overview 도넛 차트를 ECharts → SVG 컴포넌트로 교체. 로그인 후 첫 JS 208KB → 51KB | `src/components/Donut.vue`(신규), `src/pages/OverviewPage.vue` |
| 인라인 테마 스크립트를 외부 파일로 분리 (CSP `script-src 'self'` 대응) | `index.html`, `public/theme-init.js`(신규) |
| echarts ^6.1.0, vue-echarts ^8.3.2 업데이트 (취약점 0) | `package.json`, `pnpm-lock.yaml` |

## 4. 결함 수정

| 결함 | 수정 | 파일 |
|---|---|---|
| ZIP 해제 실패 시 이미 있던 폴더까지 삭제될 수 있음 | 이번 호출이 만든 폴더만 삭제(`created` 플래그) | `core/project_upload.py` |
| 빈 docstring 도구에서 `inspect_cli` IndexError | 도구 docstring 추가 + 가드 | `mcp_servers/inspect_cli.py`, `execute.py` |
| SHA1 보안 경고(bandit HIGH) | `usedforsecurity=False` | `core/rules/models.py`, `mcp_servers/rag/search.py` |
| certifi 취약점 | 2026.10.19 로 업데이트 | `backend/uv.lock` |

## 5. 리팩터링 (CC > 20 함수 23개 → 0개)

모두 차등 테스트로 기존 동작과 동일함을 확인했다.
`harness/eval/scorer.py`, `harness/eval/code_eval.py`, `harness/eval/runner.py`, `core/agent_project.py`, `core/rules/engine.py`, `core/runtime_spec.py`, `core/probes.py`, `core/execution_config.py`, `core/agent_audit/bundle.py`, `core/project_upload.py`, `llm_proxy/server.py`, `mcp_servers/business/tools/execute.py`(`run_injection_probe`), `api/routers/audits.py`(`create_audit`), 그래프 노드 5개(1장 참고).

## 6. 테스트·설정

| 변경 | 파일 |
|---|---|
| 커버리지 측정에 greenlet 동시성 설정 (실제 82.8%) | `backend/pyproject.toml` |
| audit-worker + 결과 API 전 구간 | `tests/integration/test_audit_worker_api.py`(신규) |
| 샌드박스 서버 (가짜 Docker SDK) | `tests/unit/test_sandbox_server.py`(신규) |
| probe_judge / probe_generate LLM 경로 | `tests/unit/test_probe_judge.py`(신규) |
| 가드레일 2종, 작업공간 검사, 해제 실패 회귀, probe 역할 권한, 장애 주입 대상 변경 | `test_react_investigator.py`, `test_eval_scorer.py`, `test_project_upload.py`, `test_business_mcp.py`, `test_retry_dlq.py` |

## 7. 결과 요약

| 지표 | 변경 전 | 변경 후 |
|---|---|---|
| ISO 체크 (달성/부분/미달/미측정) | 44 / 6 / 8 / 5 | **58 / 0 / 0 / 5** |
| 재현율 (B) | 0.88 | **0.967** |
| 감사기 견고성 (B) | 0.83 | **1.0** |
| F1 (A → B) | — | 0.579 → **0.928** (`reports/eval_6e3da199.md`) |
| 커버리지 | 67% (설정 오류) | **82.8%** |
| CC > 20 함수 | 23 | **0** |
| 보안 헤더 | 0 | **5종** |
| 취약 의존성 | 있음 | **0** |
| 테스트 | — | **137 통과** |

## 8. 문서

- `기획서.md` 신규 — 현재 구조 기준 기획서
- `isoReport.md` 신규 — ISO/IEC 25010 평가 보고서
- `DECISIONS.md` — 2026-10-21 결정 8건 추가
