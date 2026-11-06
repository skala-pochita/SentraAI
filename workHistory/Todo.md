# 감사 파이프라인 개선 TODO

> 작성: 2026-11-06 · 근거: 코드 정적 분석 (실측 아님). 진행 상황 범례는 `checklist.md`와 같다.
> 범례: `[x]` 완료 · `[~]` 진행 중 · `[ ]` 미착수 · `[!]` 막힘(사유 기재)
> 규칙: 명세와 다르게 바꾸는 항목은 `DECISIONS.md`에 먼저 기록 → 테스트 → `make test`·`make lint` 통과.
> 우선순위: **P0** 바로 고칠 버그 · **P1** 속도/탐지율 개선 효과 큼 · **P2** 확장

---

## 1. 정확성·안정성 버그 (P0)

- [x] **P1. 400줄 넘는 근거가 항상 검증에 실패** — 2026-11-06 `read_full_file()`로 400줄씩 끝까지 읽기, `tests/unit/test_audit_improvements.py` 2개
  - 위치: `backend/core/graph/nodes/code_evidence_check.py`, `mcp_servers/business/tools/query.py` (`MAX_READ_LINES = 400`)
  - 문제: `read_repo_file`을 라인 범위 없이 호출 → 앞 400줄만 받음 → 401번째 줄 이후 근거는 `evidence_matches` 실패, confidence -0.3, NEEDS_REVIEW로 강등. `relocate_evidence`도 앞 400줄 안에서만 찾음
  - 조치: 근거 라인 주변(±50줄)을 `start_line`/`end_line`으로 읽거나, 400줄씩 나눠 전체를 읽어 캐시
  - 완료 조건: 500줄 이상 파일의 450번째 줄 근거가 통과하는 단위 테스트

- [x] **P2. 감사가 30분을 넘으면 대상 락이 만료됨** — 2026-11-06 `keep_locks_alive()` heartbeat(10분 주기)를 `AuditRunner.run`에서 실행, 단위 테스트 1개
  - 위치: `backend/core/redis/locks.py` (`LOCK_TTL_S = 1800`, 연장 장치 없음)
  - 문제: 샌드박스 준비 타임아웃(1200초)에 ReAct·probe 시간이 더해지면 30분을 넘을 수 있음 → 같은 대상에 대한 다른 감사가 동시에 시작될 수 있음
  - 조치: 감사 진행 중 주기적으로 TTL을 연장하는 heartbeat 작업 (`acquire_target_lock` 재호출), 종료·실패 시 cancel
  - 완료 조건: TTL을 짧게 잡은 통합 테스트에서 감사가 끝날 때까지 락이 유지됨

- [x] **P3. Kafka `max_poll_interval_ms` 30분 상한** — 2026-11-06 `consume_loop()`로 처리 중 파티션 pause + poll 유지(B6에서 파티션별 동시 처리로 확장), `max_poll_interval_ms` 30분→5분, 단위 테스트 1개 (실 Kafka 장시간 시연은 미실행)
  - 위치: `backend/core/kafka/consumer.py` (`max_poll_interval_ms=1_800_000`)
  - 문제: 감사가 30분을 넘으면 리밸런스 → 재전달 (중복 실행은 heartbeat 키가 막지만 실질적인 처리 시간 상한이 됨)
  - 조치: 처리 중인 파티션은 `pause()` 후 poll 계속, 끝나면 `resume()` + commit
  - 완료 조건: 처리 시간이 긴 handler 테스트에서 리밸런스 없이 커밋됨

- [x] **P4. 재개·재시도할 때 예산(Budget)이 0으로 초기화됨** — 2026-11-06 `Budget.restore()` + 워커 재개 시 호출, 단위 테스트 1개
  - 위치: `backend/core/graph/deps.py`, `backend/workers/audit.py` (`_config`가 실행마다 `Deps` 새로 생성)
  - 문제: 체크포인트에서 이어서 실행하거나 Kafka가 재시도하면 사용한 토큰·비용이 0부터 다시 시작 → `max_cost_usd`를 넘을 수 있음
  - 조치: 재개 시 체크포인트 state의 `token_usage` 합계로 `Budget.used_tokens`/`used_usd` 복원
  - 완료 조건: 재개 테스트에서 이전 사용량이 반영된 상태로 예산 초과가 판정됨

- [x] **P5. ReAct 120초 deadline을 스텝 사이에서만 확인** — 2026-11-06 LLM 호출을 `asyncio.wait_for(남은 시간)`으로 제한, final은 `final_grace_s`(30초), 통합 테스트 1개
  - 위치: `backend/core/graph/nodes/react_investigator.py` `_investigate_file`
  - 문제: LLM 1회 호출 = 60초 타임아웃 × SDK 재시도 2회 × 스키마 재요청 2회 → 한 스텝이 120초를 크게 넘을 수 있음
  - 조치: `ask()`를 `asyncio.wait_for(남은 시간)`으로 감싸고, 시간이 다 되면 마지막 final 요청만 짧은 타임아웃으로
  - 완료 조건: 느린 가짜 LLM으로 파일당 시간 한도가 지켜지는 테스트

- [x] **P6. 파일당 토큰 예산이 LLM 토큰을 세지 않음** — 2026-11-06 파일별 usage 분리 + `max_llm_tokens_per_file`(60k) 추가, 통합 테스트 1개
  - 위치: `react_investigator.py` (`spent`는 도구 결과 텍스트 토큰만 합산)
  - 조치: `usage`에 쌓이는 해당 파일의 LLM in/out 토큰도 `spent`에 포함
  - 완료 조건: `max_tokens_per_file`을 넘으면 `loop.limit(reason=token_budget)` 이벤트가 나오는 테스트

---

## 2. 속도 병목 (P1)

> 모든 항목은 `backend/harness/eval/bench_speed.py`로 개선 전후를 수치화한다 (결과: `reports/bench_speed.md`).

- [x] **B1. 파일 단위 ReAct 조사를 병렬로 실행** ⭐ 효과 가장 큼 — 2026-11-06 Semaphore+gather, `react.concurrency: 4`. 벤치마크 8.61s → 2.58s (-70%, ×3.3), 테스트 1개
  - 위치: `react_investigator.py` `investigate()`의 for 루프
  - 조치: `asyncio.Semaphore(react.concurrency)` + `gather`, `config/budget.yaml`에 `react.concurrency: 4` 추가. `Budget`은 공유 객체라 그대로 사용
  - 완료 조건: 결과가 순차 실행과 같은지 테스트 + `make eval`로 latency 비교 (현재 B 84.6초)

- [x] **B2. MCP 호출마다 세션을 새로 만듦** — 2026-11-06 감사 단위 세션 재사용(`audit_gateway()`, 전용 task 소유) + `list_tools` 1회 캐시 + correlate 동시 저장. HTTP 요청 240→63건, 순차 2.33s→0.53s(-77%), 동시 0.39s→0.14s(-63%), 저장 30건 1.54s→0.21s(-87%). 실제 MCP 서버 테스트 1개
  - 위치: `backend/core/mcp_client.py` `HttpTransport.raw_call`
  - 조치: 감사 단위(Deps)로 세션 재사용 (맥락 헤더는 감사 동안 고정), 안 되면 httpx 연결 풀만이라도 공유
  - 추가: correlate의 `create_finding`을 한꺼번에 저장하는 batch 도구 검토 (`mcp_servers/business/tools/mutate.py`)
  - 완료 조건: 감사 1건의 MCP 호출 평균 지연 측정, 개선 전후 비교

- [x] **B3. 이벤트 발행이 감사 실행을 막음** — 2026-11-06 `buffered_events()` 큐 + Redis pipeline + DB 묶음 insert, 감사 워커에 적용. 이벤트 200건 2.34s→0.24s(-90%), 테스트 1개
  - 위치: `backend/harness/events.py` `emit` (Redis 명령 5번 + DB 트랜잭션 1번을 순서대로 await)
  - 조치: 큐에 넣고 백그라운드 작업이 일괄 처리 (Redis pipeline, DB 묶음 insert). 종료 시 flush 보장
  - 완료 조건: SSE 재생·순서 테스트 통과, 이벤트 유실 없음

- [x] **B4. probe_judge LLM 판정이 하나씩 순서대로 실행됨** — 2026-11-06 probe 안·probe 간 동시 판정(`probe.judge_concurrency: 4`), 반영은 순서대로. 18건 7.26s→2.02s(-72%), 테스트 1개
  - 위치: `backend/core/graph/nodes/probe_judge.py` `_judge_undetermined`
  - 조치: 판정이 안 난 시도를 `gather`로 동시 판정 (동시 실행 수 제한)

- [x] **B5. probe 시도가 순서대로 실행됨** — 2026-11-06 시도별 session_id 독립 확인 후 동시 실행(`probe.attempt_concurrency: 3`), 결과는 시도 순서. 1.81s→0.61s(-66%), 테스트 1개
  - 위치: `mcp_servers/business/tools/execute.py` `run_injection_probe` (`for i in range(attempts)`)
  - 조치: 대상 에이전트가 감당할 수 있는 범위에서 시도를 병렬화 (설정값으로)

- [x] **B6. 워커 하나가 감사 1건만 처리** — 2026-11-06 `consume_loop()` 파티션별 동시 처리(`AUDIT_WORKER_CONCURRENCY=3`), 같은 파티션은 순서 유지·파티션별 커밋. 감사 3건 3.03s→1.01s(-67%), 테스트 2개
  - 위치: `core/kafka/consumer.py` (`max_records=1`, 메시지를 순서대로 처리), `docker-compose.yml` audit-worker
  - 조치: audit-worker 복제본을 파티션 수(3)만큼 늘리거나, 워커 안에서 파티션별로 동시 처리
  - 완료 조건: 감사 요청 3건을 동시에 넣었을 때 병렬로 진행됨

- [x] **B7. (진행 중 발견) trace span 기록이 호출마다 DB 트랜잭션을 기다림** — 2026-11-06 `harness/observability.py` span 을 B3 이벤트 버퍼에 실어 묶음 insert. span 300건 1.92s→0.43s(-78%), 테스트 1개

---

## 3. 탐지 구조 공통 한계 (P1)

- [x] **S1. LLM이 새 유형의 취약점을 보고할 수 없음** ⭐ 비용 대비 효과 큼 — 2026-11-06 `config/categories.yaml`(10종) + `core/categories.py`, 새 발견에 OWASP/CWE 자동 부여, DECISIONS 기록, 테스트 2개
  - 위치: `react_investigator.py` `NewFinding.category` → `allowed_categories()` (기존 규칙 카테고리만 허용)
  - 조치: 카테고리 목록을 규칙과 분리해 별도 정의 (`config/` 또는 `core/schemas`): `COMMAND_INJECTION`, `INSECURE_DESERIALIZATION`, `SSRF`, `XSS`, `DEBUG_ENABLED`, `BROKEN_ACCESS_CONTROL`, `VULNERABLE_DEPENDENCY` 등 + 심각도·OWASP/CWE 매핑
  - 완료 조건: 규칙에 없는 카테고리의 LLM 발견이 Finding으로 저장되는 테스트, 정답셋 매칭·집계 정상

- [x] **S2. 위험 파일 선택이 파일 이름 기준** — 2026-11-06 `core/risk_signals.py` + MCP `rank_risky_files`(읽기 전용, intake 허용), 실패 시 이름 방식 폴백. 취약 파일 선택: Python 0/5→5/5, Spring 2/7→6/7, 데모 C19·C20 2/2 유지. 테스트 3개
  - 위치: `backend/core/graph/nodes/intake.py` `risky_files` (이름 키워드 + `.java`일 때 +1점)
  - 조치: 내용 신호로 점수 매기기 — `@RequestMapping`/`@GetMapping`, `SecurityFilterChain`, `jdbcTemplate`, `subprocess`, `eval`, SQL 문자열, `request.` 입력 사용 등. 언어 중립적으로
  - 완료 조건: 이름이 평범한 파일에 심은 취약점(정적 회피 케이스)이 조사 대상에 포함됨. 정적 회피 탐지율 B 0.67 → 개선 확인

- [x] **S3. 파일 사이의 데이터 흐름을 추적하지 못함** — 2026-11-06 ①안: `core/code_graph.py`(import·타입 참조 색인) + MCP `related_files` + 프롬프트 v4, 관련 파일의 새 발견 허용. 데모 앱 하위 계층 취약 파일 3개 중 2개가 진입점에서 1~2단계로 연결됨, 테스트 2개. ②안(외부 SAST taint)은 E1
  - 문제: ReAct가 파일 단위라 Controller → Service → Repository로 이어지는 흐름을 한 번에 보지 못함
  - 조치(택1 또는 병행): ① 조사할 때 호출 관계 파일을 함께 제공(간단한 import/호출 그래프) ② 외부 SAST의 taint 결과 활용(→ 6장 E1)

- [x] **S4. 정적 규칙이 한 줄 정규식 위주** — 2026-11-06 규칙 필드 `multiline: true`(파일 전체 매칭·매치 시작 줄 보고·주석 줄 제외), 테스트 1개
  - 조치: regex 탐지기에 여러 줄 매칭 옵션(`multiline: true`, 문장 단위 결합) 추가
  - 위치: `backend/core/rules/detectors/`, `core/rules/models.py`

---

## 4. Spring(Java/Kotlin) 분석 보강 (P1)

- [x] **SP1. SQL Injection 규칙 확장** (`rules/code/SEC-SQLI-001.yaml`) — 2026-11-06 `SEC-SQLI-002/003`
  - [x] 변수로 만든 쿼리: `String sql = "..." + x;` 다음 줄 `query(sql)` (같은 메서드 안 변수 추적 또는 LLM 후보로)
  - [x] `String.format(...)`, `StringBuilder.append`로 만든 쿼리
  - [x] MyBatis XML 매퍼의 `${}` (`**/*Mapper.xml`)
  - [x] JPA 네이티브 쿼리를 변수로 받는 경우 (`createNativeQuery(var)`) — Java 어노테이션 값은 컴파일 상수만 가능해 `@Query` 문자열 결합은 성립하지 않아 대상을 바꿈
- [x] **SP2. 인가 설정 규칙 확장** (`SEC-SPRING-001`) — 2026-11-06 `SEC-SPRING-007/009/010`
  - [x] `requestMatchers("/admin/**", "/actuator/**").permitAll()` 같은 민감 경로 허용
  - [x] 컨트롤러 엔드포인트별 `@PreAuthorize`/`@Secured` 누락 탐지 (LLM 검토 대상)
  - [x] IDOR 후보: `@PathVariable id`로 조회하면서 소유자 확인이 없는 경우 (LLM 검토 대상)
- [x] **SP3. CSRF 규칙의 여러 줄 람다 DSL 대응** (`SEC-SPRING-002`) — `csrf(c -> c` / `.disable())`
- [x] **SP4. 보안 설정 파일을 이름이 아니라 내용으로 선택** — `*Config*.java`/`*Security*.java` 대신 `SecurityFilterChain`·`HttpSecurity`가 들어 있는 파일
- [x] **SP5. `application.properties` 지원** — H2 콘솔·Actuator·정보 노출 규칙(`SEC-SPRING-005/006`, `SEC-INFO-001`)이 지금은 `application*.yml`만 봄
- [x] **SP6. Kotlin 지원** — SQLi·경로 조작·로그·CORS·PasswordEncoder 규칙의 `file_globs`에 `**/*.kt` 추가 + Kotlin 문법 패턴
- [x] **SP7. 기타 Spring 위험 패턴** — 2026-11-06 `SEC-SPRING-008/011`, `SEC-JAVA-001~003`
  - [x] `@CrossOrigin("*")` (어노테이션 형태 CORS)
  - [x] `server.error.include-stacktrace=always`, `spring.jpa.show-sql=true`(운영 프로필)
  - [x] `XMLInputFactory`/`DocumentBuilderFactory` XXE 설정 누락
  - [x] `ObjectInputStream.readObject` 역직렬화
  - [x] `Runtime.exec`/`ProcessBuilder` + 입력값
- [x] 완료 조건 — 2026-11-06 변경: 데모 앱 기준선을 보존하려고 규칙 fixture `datasets/rule_fixtures/spring/`(양성 26·음성 11) + `test_rule_fixtures.py`로 검증. 데모 앱에서 새로 찾은 실제 취약점 2건은 정답셋 C21·C22로 추가, 위치 검증 통과. fixture 탐지 0/26 → 26/26, 오탐 0

---

## 5. Python 분석 추가 (P1 — 지금은 사실상 없음)

- [x] **PY1. 기존 규칙의 적용 범위를 Python까지 확대** — 2026-11-06 엔트로피(작은따옴표 문자열 지원)·주석·로그(`logger.*`, `print`) 규칙에 `.py`
  - [x] `SEC-SECRET-002`(엔트로피) `file_globs`에 `**/*.py`, `**/*.env.example` 제외 처리
  - [x] `SEC-COMMENT-001/002`, `SEC-LOG-001`(`logging.info(... password ...)`)에 `**/*.py`
- [x] **PY2. Python 규칙팩 신설** (`rules/code/SEC-PY-*.yaml`) — 2026-11-06 `SEC-PY-001~010` (001·002 는 AST)
  - [x] `eval(`/`exec(` + 입력값 → COMMAND_INJECTION (S1 카테고리 필요)
  - [x] `subprocess.*(..., shell=True)`, `os.system(`, `os.popen(`
  - [x] `pickle.loads`/`pickle.load`, `yaml.load(`(SafeLoader 없이), `marshal.loads` → INSECURE_DESERIALIZATION
  - [x] f-string·`%`·`.format`으로 만든 SQL을 `cursor.execute`에 전달 → SQL_INJECTION
  - [x] `requests.get/post(<변수>)`, `urllib.request.urlopen(<변수>)` → SSRF (LLM 검토 대상)
  - [x] `open(... + ...)`, `os.path.join(base, user_input)` 정규화 없이 → PATH_TRAVERSAL
  - [x] Flask `app.run(debug=True)`, Django `DEBUG = True`, `ALLOWED_HOSTS = ['*']` → DEBUG_ENABLED
  - [x] `jinja2.Environment(autoescape=False)`, `Markup(`/`|safe` → XSS
  - [x] `verify=False`(TLS 검증 끔), `hashlib.md5/sha1`로 비밀번호 해시
- [x] **PY3. AST 탐지기 추가** — 2026-11-06 `ast_python` (import 별칭·여러 줄 호출·상수 인자 제외). exec 케이스 정규식 TP 6/8·FP 8 → AST 8/8·FP 0 (`backend/core/rules/detectors/`에 `python_ast`)
  - 호출 이름 + 인자(키워드 `shell=True`, 상수가 아닌 인자)를 확인해 정규식 오탐 줄이기
  - 지금 `ast`는 에이전트 도구 코드를 찾는 데만 씀 (`core/tool_code_evidence.py`)
- [x] **PY4. `risky_files` 점수 언어 중립화** — 2026-11-06 S2로 해결 (`.java` +1점 제거, 코드 확장자 9종) — `.java`에만 주는 +1점 제거·확장 (S2와 함께)
- [x] **PY5. 에이전트 도구 탐색 범위 확인** — 2026-11-06 확인 결과 리터럴 목록만 탐색 → `@tool`·`@mcp.tool()`·`@function_tool`·`Tool(...)`·`StructuredTool.from_function(...)` 추가. 7개 방식 샘플 1/7 → 7/7, 테스트 1개 — `core/agent_project.py` `python_literal_tools`가 데코레이터(`@tool`)나 동적으로 등록한 도구를 놓치는지 점검
- [x] 완료 조건: 규칙 단위 fixture 27/27·오탐 0, 대상 단위 정적 구성 F1 0.857(EV1·EV2). LLM 구성(H) 측정은 EV5(키 필요)

---

## 6. 외부 도구 연동 (P2)

- [x] **E1. Semgrep 연동** — 2026-11-06 설치 시에만 실행(`--metrics=off`, `SEMGREP_CONFIG`), CWE→카테고리 매핑, 파서 테스트. (이 환경엔 미설치라 실행 검증은 파서 단위) — Java·Python 규칙과 taint 모드. 결과를 정적 후보(NEEDS_REVIEW)로 넣고 기존 LLM 검증 단계 재사용. 실행 도구는 `run_static_scan`과 같은 방식(커밋 캐시, 타임아웃, 구조화된 에러)
- [x] **E2. Bandit 연동** (Python) — 2026-11-06 의존성 추가, test id→카테고리, MEDIUM 이상만. Python fixture에서 실행 검증
- [x] **E3. 의존성 취약점(SCA)** — 2026-11-06 requirements/pyproject/pom(속성 해석)/gradle 파서 + 로컬 권고 DB 16건 + OSV(opt-in). SCA fixture 취약 6건 6/6, 안전 버전 오탐 0 — Java: OWASP Dependency-Check 또는 `pom.xml`/`build.gradle` 파싱 + OSV 조회, Python: `pip-audit`(`requirements.txt`/`pyproject.toml`)
- [x] 외부 도구 결과는 감사 대상 텍스트로 취급 → 프롬프트에 넣을 때 `wrap_untrusted()`, 비밀값 `mask_secret()`
- [x] `harness/tools.py`의 allowlist와 도구 분류에 새 도구 등록 (`run_external_scan` EXEC, code_static_scan 허용)

---

## 7. 평가·운영 (P1)

- [x] **EV1. Python 정답셋 추가** — 2026-11-06 `targets/vuln-python-app`(취약 16·함정 6·감사기 인젝션 2, 정적 회피 2) + `code_python.yaml`, 위치 검증 스크립트가 `code*.yaml` 전부 검사, seed·demo-reset 연결 — `targets/vuln-python-app`(Flask 또는 FastAPI, 가짜 키), `datasets/ground_truth/code_python.yaml`, 함정(decoy) 포함, 위치 검증 스크립트 확장
- [x] **EV2. A/B 평가에 Python 대상 포함** — 2026-11-06 `harness/eval/code_target_eval.py`(S·SX·H 구성, 대상·정답셋 지정) + `make eval-target`. 정적 S: Python F1 0.857, Spring F1 0.889. 채점 버그 2건 수정(INFO 허용 함정 오탐 집계, 함정 오탐 이중 집계) — `harness/eval/runner.py`, `make eval`
- [x] **EV3. 비용 산출** — 2026-11-06 OpenAI 공식 가격 기입(nano $0.20/$1.25, mini $0.75/$4.50, 임베딩 $0.02 per 1M). 로컬 실행 시 `.env` 키를 ChatOpenAI 에 넘기지 않던 버그 수정 — `config/pricing.yaml`에 사용 모델(gpt-5.4-nano·gpt-5.4-mini) 가격 입력 → `cost_usd`, `f1_per_dollar` 산출
- [x] **EV4. 노드별 소요 시간 측정** — 2026-11-06 `latency_breakdown()`(trace_spans 집계) → 보고서 JSON `timings`·Markdown "소요 시간" 표. 진행 중 B7 발견·개선 — span 기반으로 노드·파일별 latency를 보고서/`/quality`에 표시해 개선 전후 비교 (B1~B6 효과 검증용)
- [!] **EV5. 목표 미달 항목 재측정** — 막힘: `.env` 의 OPENAI_API_KEY 가 `invalid_api_key`(401)로 거부됨 (2026-11-06 확인). 유효한 키로 교체 후 `make eval-target TARGET=vuln-spring-app TRUTH=code.yaml CONFIGS=S,SX,H REPEAT=3`, 같은 명령을 Python 대상으로, 전체 `make eval` 실행. H 구성은 인증 실패 시 결과를 만들지 않도록 해 둠 — 재현율 0.9, 감사기 견고성 1.0, 정적 회피 탐지율 (`checklist.md` Phase 7 완료 조건)

---

## 권장 진행 순서

1. P0 버그: **P1 → P2 → P4** (작고 확실함) → P3, P5, P6
2. **S1 카테고리 확장** → **PY1·PY2 Python 기본 규칙팩** → EV1 Python 정답셋
3. **B1 병렬화** → B2 → EV4로 속도 측정
4. SP1~SP7 Spring 보강, S2 위험 파일 선택
5. E1~E3 외부 도구, S3 파일 사이 흐름 분석

## 코드 리뷰 반영 (`codeReview.md`, 2026-11-06) — 기록 `개선.md` 6장

- [x] R1. 대상 락 획득·해제·연장 Lua 원자화 + 취소 API 해제 통일 (리뷰 A)
- [x] R2. Kafka lease 획득마다 토큰, 소유자 확인 heartbeat, lease 상실 시 처리 취소, done 소유자 확인 (리뷰 B)
- [x] R3. 병렬 LLM 호출 예산 예약 (상한 초과 20,000 → 10,000 토큰)
- [x] R4. `gather_or_cancel`로 병렬 실패 시 나머지 취소·정리 + 실패 경로 테스트 (리뷰 C)
- [x] R5. 이벤트·span 묶음 기록 내구성 트레이드오프 DECISIONS 기록
