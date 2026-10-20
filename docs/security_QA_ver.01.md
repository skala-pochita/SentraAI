# Security QA ver.01

- 작성일: 2026-10-20
- 범위: 코드 감사 + AI 에이전트 감사 (Cloud 감사 제거 후, DECISIONS #20)
- 기준: `main@5b38144` + 미커밋 변경(Cloud 제거, `.env.example` 정리)
- 입력: (1) 외부 QA 표 7항목, (2) 이번 세션 갭 분석·페르소나 리뷰·Cloud 제거 작업 중 발견 사항

## 확인 수준 표기
| 표기 | 의미 |
|---|---|
| **재현** | 이번에 직접 실행해서 문제 동작을 확인함 |
| **코드 확인** | 코드 경로를 읽어 문제 동작이 확정적으로 일어남을 확인함 (실행은 안 함) |
| **부분 확인** | 주장 중 일부만 코드로 확인됨. 나머지는 재현 경로를 찾지 못함 |
| **정상 확인** | 의심했으나 문제 없음 |

담당: **C** = Claude가 코드로 수정 가능, **U** = 사용자 결정·조치 필요

---

## 1. 요약

| ID | 심각도 | 항목 | 출처 | 확인 수준 | 담당 |
|---|---|---|---|---|---|
| SQA-01 | CRITICAL | 공개 기본값 JWT 비밀키·MCP 서비스 토큰으로 스택 동작 중 | 세션 | 코드 확인 | U (+C) |
| SQA-02 | HIGH | 승인 범위 검증 누락 (승인 목록 밖 CRITICAL 수정 통과) | QA표 | 코드 확인 | C |
| SQA-03 | HIGH | 재검증 오류 (스니펫 변경만으로 해결, LLM 발견은 무조건 해결) | QA표 | 코드 확인 | C |
| SQA-04 | HIGH | 비밀값 보호 부족 (JSON 비밀값 미마스킹, 패치 원문 DB 저장) | QA표 | 재현 | C |
| SQA-05 | HIGH | 파일 접근 경계 차이 (스캔·검색이 외부 심볼릭 링크 파일을 읽음) | QA표 | 재현 | C |
| SQA-06 | MEDIUM | 인젝션 방어 누락 (probe 생성 프롬프트에 도구 설명 원문 전달) | QA표 | 부분 확인 | C |
| SQA-07 | MEDIUM | 다중 턴 공격 판정 누락 (마지막 턴만 판정) | QA표 | 코드 확인 | C |
| SQA-08 | LOW | ASR 표시 왜곡 (판정 불가를 실패로 집계, 분모 0 → 0%) | QA표 | 부분 확인 | C |
| SQA-09 | MEDIUM | A1~A4 확장 점검이 API·워커에 미연결 | QA표·세션 | 확인 | U → C |
| SQA-10 | MEDIUM | CI·시크릿 스캔 부재 | 세션 | 확인 | C |
| SQA-11 | MEDIUM | 실 LLM 경로 미검증 (키 401) | 세션 | 확인 | U → C |
| SQA-12 | LOW | 인프라 포트 전체 노출·DB 비밀번호 하드코딩 | 세션 | 코드 확인 | U |
| SQA-13 | LOW | 장애 주입(`make fault-on`) 시나리오 미관찰·결정 미기록 | 세션 | 확인 | U + C |
| SQA-14 | INFO | Cloud 잔재: 미사용 `read_resource` | 세션 | 확인 | U → C |

**우선 처리 권장 순서**: SQA-01 → SQA-02 → SQA-05 → SQA-04 → SQA-03 → SQA-06·07 → SQA-10 → 나머지

---

## 2. 상세

### SQA-01 [CRITICAL] 공개 기본값 비밀키로 동작 중
- **현상**: 실제 `.env` 에 `JWT_SECRET`, `MCP_SERVICE_TOKEN` 키가 없다(키 이름만 비교, 값은 열람하지 않음). 그래서 `backend/core/settings.py:33` 의 `dev-only-jwt-secret-change-me-32bytes!!` 와 `:36` 의 `change-me` 가 쓰인다. 두 값 모두 저장소에 공개돼 있다.
- **영향**: 누구나 ADMIN JWT 를 위조해 API 를 호출할 수 있다. business-mcp(`8101`)·rag-mcp(`8102`)가 호스트 `0.0.0.0` 에 노출돼 있어(`docker-compose.yml:116,131`), 서비스 토큰만 알면 수정 적용·상태 변경 도구를 직접 부를 수 있다.
- **수정안**:
  - U: `.env` 에 `JWT_SECRET`(`openssl rand -hex 32`)·`MCP_SERVICE_TOKEN` 설정 후 `make up`.
  - C: 기동 시 기본값이면 거부(또는 `ENV=dev` 일 때만 허용)하는 검사를 `settings` 에 추가.
- **검증**: 기본값으로 API 기동 시 실패하는 단위 테스트. 설정 후 기존 토큰 무효 확인.

### SQA-02 [HIGH] 승인 범위 검증 누락
- **현상**: `apply_fix`(`backend/mcp_servers/business/tools/mutate.py:286-299`)는 CRITICAL 이 포함된 수정안에 대해 승인의 상태(`APPROVED`)와 `audit_id` 만 확인한다. 수정안의 `finding_ids` 가 승인된 `Approval.finding_ids` 안에 있는지는 보지 않는다.
- **영향**: 같은 감사의 아무 승인 ID 하나로 승인 목록 밖 CRITICAL 수정까지 적용된다. 사람 승인이 우회된다.
- **수정안 (C)**: `set(prop.finding_ids) ∩ CRITICAL ⊆ set(appr.finding_ids)` 를 검사하고, 아니면 `FORBIDDEN`. 승인 시점과 수정안의 finding 집합을 비교한 사유를 오류 상세에 남긴다.
- **검증**: 승인 A(finding X) 로 finding Y(CRITICAL) 수정안 적용 시 FORBIDDEN 이 나오는 통합 테스트.

### SQA-03 [HIGH] 재검증 오류
- **현상** (`backend/core/graph/nodes/re_verify.py`):
  - 코드 발견(`:61`): 규칙 ID·경로·**정규화 스니펫이 완전히 같은** 탐지가 없으면 해결이다. 변수명·공백 정규화 범위 밖의 사소한 변경만으로 취약점이 남아도 `RESOLVED` 가 된다.
  - LLM 발견: ReAct 가 만든 발견은 `rule_id="LLM-REACT"`(`react_investigator.py:218`)인데, 정적 스캔은 이 규칙 ID 를 내지 않는다. 그래서 병합 뒤 항상 `RESOLVED` 다. 사실상 정적 검사 결과와 무관하게 해결 처리된다.
- **수정안 (C)**:
  - 코드: 같은 규칙·같은 파일에서 원래 줄 ±N 범위에 탐지가 남아 있으면 미해결로 본다(스니펫 일치는 보조).
  - LLM 발견: 해결 판정을 하지 않고 `NEEDS_REVIEW` 로 두거나, 해당 파일에 ReAct 재조사를 돌려 판정한다.
- **검증**: 취약 줄의 변수명만 바꾼 패치 → 미해결. `LLM-REACT` 발견 → `RESOLVED` 아님.

### SQA-04 [HIGH] 비밀값 보호 부족
- **재현** (`backend/harness/masking.py:17-24`):
  - `mask_text('{"password": "hunter2-secret", "api_key": "abcd1234efgh"}')` → 원문 그대로
  - `mask_obj({'password': 'hunter2-secret'})` → 원문 그대로
  - `mask_text('password=hunter2-secret')` → `password=hunt****` (대입 형식만 마스킹됨)
  - 원인: 키 뒤에 `"` 가 오는 JSON 형식을 정규식이 받지 않고, `mask_obj` 는 키 이름을 보지 않고 값 문자열만 검사한다.
- **현상 2**: `register_fix`(`mutate.py:228-229`)는 응답에는 `diff_masked` 를 주지만 DB `fix_proposals.diff` 에는 **원문 diff** 를 저장한다. 비밀값을 지우는 수정이면 비밀값 원문이 DB 에 남는다(명세 0장 규칙 5 위반).
- **수정안 (C)**:
  - 정규식에 `"key"\s*:\s*"value"` 형식 추가, `mask_obj` 는 민감 키 이름(password·secret·token·api_key…)이면 값 전체를 `mask_secret`.
  - diff: DB 에는 마스킹본만 저장하고, 적용용 원문은 작업공간 브랜치(git)에만 둔다. 또는 적용 시 edits 로 다시 계산한다.
- **검증**: 위 3개 입력의 단위 테스트. 비밀값 포함 수정안 등록 후 DB 행에 원문이 없는지 확인하는 통합 테스트.

### SQA-05 [HIGH] 파일 접근 경계 차이
- **재현**: 저장소 안에 외부 파일을 가리키는 심볼릭 링크를 두고 `iter_repo_files` 를 실행하면 외부 파일 내용(`SECRET_OUTSIDE=1`)을 읽는다.
  - 직접 읽기는 `safe_path`(`backend/mcp_servers/business/workspace.py:87`)가 `resolve()` 후 경계를 확인해 막는다.
  - 스캔(`run_static_scan`)·검색(`search_repo`)이 쓰는 `iter_repo_files`(`backend/core/rules/engine.py:47`)에는 그 검사가 없다.
  - 디렉터리 링크는 따라가지 않았다(Python 3.12 `rglob` 동작). 파일 링크만 해당한다.
- **영향**: 감사 대상 저장소에 링크 하나만 두면 서버의 다른 파일(설정·키)이 스캔 결과·LLM 프롬프트로 흘러갈 수 있다.
- **수정안 (C)**: `iter_repo_files` 에서 `p.is_symlink()` 는 건너뛰거나 `p.resolve().is_relative_to(root.resolve())` 를 확인한다. 직접 읽기와 같은 경계 함수를 공유한다.
- **검증**: 위 재현을 단위 테스트로 고정 (링크 파일이 결과에 없어야 함).

### SQA-06 [MEDIUM] 인젝션 방어 누락 — 부분 확인
- **확인됨**: `probe_generate`(`backend/core/graph/nodes/probe_generate.py:45`)는 감사 대상 에이전트 매니페스트의 도구 설명을 `wrap_untrusted()` 없이 넣는다. 프롬프트(`backend/harness/prompts/probe_generate_v1.yaml:11` `대상 도구: {tools}`)에도 신뢰 경계 규칙(`UNTRUSTED_RULE`)이 없다. CLAUDE.md 규칙 4 위반이다.
  - 대조: `agent_static_check`·`llm_fixer`·`probe_judge` 는 `UNTRUSTED_RULE` + `wrap_untrusted` 를 쓰고, `code_investigate` 는 규칙을 본문에 직접 포함한다.
- **미확인**: "일부 시스템 정책이 치환되지 않음" — 채워지지 않는 템플릿 자리 표시자나 누락된 정책 치환 경로를 코드에서 찾지 못했다. 재현 절차가 필요하다.
- **수정안 (C)**: 도구 목록을 `wrap_untrusted(json.dumps(tools), "agent:manifest.tools")` 로 감싸고 프롬프트에 `{untrusted_rule}` 추가. 모든 프롬프트에 규칙이 들어갔는지 검사하는 테스트(프롬프트 레지스트리 순회).
- **검증**: 도구 설명에 "ignore previous instructions" 를 넣은 매니페스트로 생성 프롬프트에 `<untrusted>` 태그가 있는지 확인.

### SQA-07 [MEDIUM] 다중 턴 공격 판정 누락
- **현상**: `run_injection_probe`(`backend/mcp_servers/business/tools/execute.py:173`)는 턴마다 `data` 를 덮어써서 **마지막 턴의 응답·도구 호출만** 판정한다. 앞 턴에서 금지 도구가 실행돼도 놓친다.
- **수정안 (C)**: 턴별 `tool_calls` 를 누적하고 응답도 턴별로 보관해 `judge_attempt` 에 전체를 넘긴다. 저장 기록(`probe_runs`)에도 턴별 결과를 남긴다.
- **검증**: 1턴에 `refund_order` 실행, 2턴은 무해한 대화인 probe → `SUCCESS` 판정.

### SQA-08 [LOW] ASR 표시 왜곡 — 부분 확인
- **확인됨**: ASR 집계(`backend/api/routers/audits.py:406-411`)는 `UNDETERMINED`(판정 불가)를 시도 수에 넣고 성공에서는 뺀다. 판정하지 못한 시도가 ASR 을 낮추고, 전부 판정 불가면 화면(`RedTeamPage.vue`)에 `0%` 로 보인다. 분모 0 이면 `0` 을 돌려주는 코드도 있다.
- **미확인**: 실행 기록이 없는 probe 는 집계에 들어가지 않으므로 "미실행 probe 가 0%" 라는 경로는 재현하지 못했다. 위의 판정 불가 경로가 그렇게 보였을 가능성이 있다.
- **수정안 (C)**: `UNDETERMINED` 는 분모에서 빼고 별도 건수로 표시, 분모 0 은 `null` → 화면에 "미측정".

### SQA-09 [MEDIUM] A1~A4 확장 점검 미연결
- **현상**: `backend/core/agent_audit/checks.py` 의 A1(승인 없는 변경 도구)·A2(호출 주체 정책 누락)·A3(도구 조합)·A4(도구 설명 오염)는 단위 테스트로만 동작하고, 감사 그래프·API·워커에서 호출되지 않는다. 입력 형식(`EvidenceBundle`)도 제품 흐름의 에이전트 매니페스트와 다르다.
- **참고**: 제품 흐름의 에이전트 감사는 기존 규칙(`AGT-TOOL-001~004` 등)과 probe 로 동작하며, 2026-10-20 실환경 e2e 에서 7개 규칙·4개 probe·결합 위험 2종 탐지를 확인했다. A1·A4 는 이 규칙들과 내용이 겹친다.
- **결정 필요 (U)**: ① 매니페스트 → 번들 변환기를 만들어 `agent_static_check` 노드에 연결, ② 기존 `rules/agent` 로 흡수하고 `agent_audit` 삭제 중 선택. 이후 C 가 구현.

### SQA-10 [MEDIUM] CI·시크릿 스캔 부재
- **현상**: `.github/workflows/` 가 없다(`.github/modernize` 만 있음). 테스트·린트가 사람이 돌릴 때만 실행된다. 비밀값 원문이 로그·DB·보고서에 남지 않는지 보는 회귀 테스트와 커밋 시크릿 스캔도 없다.
- **수정안 (C)**: `make lint`·`make test`·프론트 빌드 워크플로, gitleaks 단계, SQA-04 회귀 테스트 추가.

### SQA-11 [MEDIUM] 실 LLM 경로 미검증
- **현상**: 셸의 `OPENAI_API_KEY` 는 401 로 거부된다. 키가 없으면 LLM 노드를 건너뛰고 정적 결과로 진행한다(`backend/core/graph/deps.py:23`). 그래서 다음 보안 기능이 실제 모델로는 한 번도 돌지 않았다: probe_judge LLM 판정, 적응형 probe 생성(SQA-06 위험 지점), ReAct 조사(SQA-03 의 `LLM-REACT`), LLM 수정기, 프롬프트 계층 판정.
- **조치**: U 가 유효 키·모델 ID 를 넣으면 C 가 `uv run pytest -m llm tests/e2e/test_probe_smoke.py` 와 ReAct C19·C20 실측을 수행.

### SQA-12 [LOW] 인프라 노출
- **현상**: Postgres(`5432`)·Redis(`6379`)·Kafka(`9092`)·Kafka UI(`8080`)·MCP 서버 두 개·대상 에이전트까지 호스트 전체 인터페이스에 노출된다(`docker-compose.yml` `ports`). Postgres 비밀번호는 `auditor` 로 하드코딩돼 있다(`:31`).
- **조치 (U)**: 로컬 데모 전용으로 둔다면 문서에 명시하고, 공유 네트워크에서 쓸 때는 `127.0.0.1:` 바인딩 또는 내부 서비스 포트 제거. C 가 compose 수정 가능.

### SQA-13 [LOW] 장애 주입 시나리오 미관찰
- **현상**: 재시도·DLQ 는 통합 테스트만 있고 `make fault-on` 실환경 관찰은 안 했다. 필요한 값(N=2 가 아니라 큰 값)도 DECISIONS 에 기록되지 않았다. 실제 `.env` 에 `FAULT_INJECTION=` 줄이 없어 `make fault-on` 이 실패한다.
- **조치**: U 가 `.env` 에 `FAULT_INJECTION=` 줄 추가 → C 가 시나리오 실행·결과 기록.

### SQA-14 [INFO] Cloud 잔재
- `backend/core/mcp_client.py` 의 `read_resource`(MCP resources/read)는 Cloud 감사 전용이었고 이제 호출처가 없다. 공격 표면은 아니지만 미사용 코드다. 제거 여부 결정(U) 후 C 가 정리.

---

## 3. 정상 확인 (문제 없음)
| 항목 | 확인 내용 |
|---|---|
| GitHub 웹훅 서명 | `GITHUB_WEBHOOK_SECRET` 이 비어 있으면 모든 요청을 거부(`backend/api/routers/webhooks.py:24`). 빈 키 HMAC 우회 없음 |
| 직접 파일 읽기 경계 | `safe_path` 가 `resolve()` 후 `is_relative_to` 로 경로 탈출·링크 탈출 차단 |
| 디렉터리 심볼릭 링크 | 스캔이 외부 디렉터리 링크를 따라가지 않음 (Python 3.12 `rglob`) |
| Cloud 제거 후 회귀 | `make lint`·`make test` 94개·infra e2e 3개 통과. 코드 감사 정적 기준선 정밀도 0.82·재현율 0.90·감사자 인젝션 견고성 1.0 (제거 전과 동일) |

## 4. 다음 단계
1. U: SQA-01 `.env` 설정, SQA-09·SQA-14 결정, SQA-11 유효 키 제공
2. C: SQA-02 → SQA-05 → SQA-04 → SQA-03 → SQA-06·07·08 수정 + 각 회귀 테스트, SQA-10 CI 추가
3. 수정 후 ver.02 에서 각 항목을 재현 테스트로 다시 판정
