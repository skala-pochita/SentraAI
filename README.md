<p align="center">
  <img src="frontend/public/logo.png" alt="SentraAI" width="320" />
</p>

# SentraAI — AI Security Auditor

> AI 에이전트 프로젝트를 ZIP으로 올리면 **도구 권한·프롬프트를 점검하고, 격리된 환경에서 실제로 공격해 보고, 코드 취약점과 엮어 결합 위험까지** 근거와 함께 보고하는 읽기 전용 보안감사 에이전트.

SKALA 팀 Capstone · LangGraph / LangChain · MCP · Redis · Kafka · Vue 3

> ⚠️ `targets/` 아래 샘플 코드의 모든 키·비밀번호는 **테스트용 가짜 값**이다. 실제 서비스에서 유효하지 않다.

---

## 왜 만들었나

- 기업은 CS·사내 업무용 AI 에이전트를 빠르게 도입하지만, 에이전트가 **어떤 도구를 어떤 권한으로 호출하는지**, **프롬프트 인젝션에 뚫리는지** 점검하는 절차가 없다.
- 기존 SAST·시크릿 스캐너는 코드만 본다. 에이전트의 도구 권한·인젝션 취약성을 코드 취약점과 연결하지 못한다.
- 보안 담당자는 적고 경보는 많으며, 근거가 부족하면 개발자가 납득하지 못한다.

## 무엇을 하나

| 축 | 내용 |
|---|---|
| **Agent 검증** | 도구 정의·권한·시스템 프롬프트 정적 점검 + 격리 컨테이너에서 실제 인젝션 공격(레드팀 probe)을 실행해 방어 여부 판정 |
| **코드 검증** | 하드코딩 비밀정보, 주석 속 민감정보, Spring Security 설정 실수, SQL Injection, 경로 탐색, 로깅 노출 등 |
| **결합 위험** | 코드 결과와 Agent 결과를 연결해 심각도 재산정 (예: 인젝션 성공 + 승인 없는 삭제 도구 = CRITICAL) |
| **보고** | 파일·라인 근거, OWASP / CWE 조항, 권장 조치를 담은 보고서 (JSON + Markdown) |

원본 코드는 바꾸지 않는다. 자동 수정·PR·병합은 하지 않는다.

- 코드 규칙 17개, Agent 정적 규칙 7개, 결합 위험 규칙 5개, 인젝션 probe 12개 + 대상 맞춤 적응형 probe
- 역할 3개(ADMIN / DEVELOPER / AUDITOR) JWT 인증 + RBAC

## 결과: 같은 작은 모델, 구조만 다르게

같은 정답셋에서 작은 모델 단일 프롬프트(A)와 우리 서비스(B, 같은 모델 + Harness + LangGraph Agent + MCP)를 비교했다. (2026-10-21, 반복 3회)

| 지표 | A 단일 프롬프트 | **B 우리 서비스** |
|---|---|---|
| code_f1 | 0.579 ± 0.046 | **0.928 ± 0.063** |
| code_recall | 0.550 | **0.967** |
| code_precision | 0.614 | **0.893** |
| agent_recall | 0.714 | **1.0** |
| severity_accuracy | 0.427 | **0.863** |
| auditor_robustness | 0.833 | **1.0** |
| tokens | 19,802 | 85,911 |
| latency_s | 23.4 | 84.6 |

B는 A보다 탐지 품질이 크게 높고, 대신 토큰은 약 4.3배, 시간은 약 3.6배 더 든다.

---

## 아키텍처

```
 frontend (Vue 3, nginx :5173)
        │ REST · SSE
   ┌────▼─────┐  produce   ┌───────┐  consume  ┌──────────────────┐
   │ api:8000 ├──────────► │ Kafka ├─────────► │ audit / eval /   │  LangGraph
   └────┬─────┘            └───────┘           │ retry worker     │  + Redis checkpoint
        │                                      └───┬──────────┬───┘
   Postgres (+pgvector)                    MCP     │          │  MCP
   Redis Stack                     ┌───────────────▼┐   ┌─────▼─────┐
                                   │ business-mcp   │   │ rag-mcp   │
                                   └───────┬────────┘   └───────────┘
                                           │
                                   ┌───────▼────────┐  감사별 internal 네트워크
                                   │ sandbox-runner ├─► 업로드 프로젝트 컨테이너
                                   └────────────────┘           │ 임시 토큰
                                                         ┌──────▼─────┐
                                                         │ llm-proxy  ├─► OpenAI
                                                         └────────────┘
```

### 감사 그래프 (LangGraph)

```
START → intake ─┬─► code_audit  ─┐
                └─► agent_audit ─┴─► correlate → report → END

code_audit:  code_static_scan → code_investigate (ReAct) → code_evidence_check
agent_audit: agent_static_check → prepare_sandbox → probe_generate → probe_run → probe_judge
                                                        ▲                          │
                                                        └──── 불확실 probe 재시도 ──┘
```

- **ReAct 조사**: 위험 파일을 직접 읽고 보안 기준 문서를 검색해 정적 후보를 판정하고 새 취약점을 찾는다.
- **적응형 공격**: 대상 에이전트의 도구 목록을 보고 probe를 새로 만든다.
- **자기 검증**: 근거 파일·라인 실재 확인, probe 판정 라운드 루프.

### 신뢰성
- Kafka 수동 커밋 + Redis 임대(lease) 기반 멱등 처리
- 노드 내 재시도 → `jobs.retry` 지연 재발행(5 / 30 / 120초) → 한도 초과 시 DLQ, Ops 화면에서 재처리
- Redis 체크포인트로 끊긴 노드부터 재개, MCP 서킷 브레이커
- SSE 진행 이벤트 (`Last-Event-ID` 재연결 지원)

### 보안 설계
- 감사 대상 텍스트는 지시로 취급하지 않는다 (감사기 대상 인젝션 방어)
- 비밀값은 로그·DB·보고서·LLM 출력에서 마스킹
- 업로드 ZIP: 경로 탈출·심볼릭 링크·ZIP bomb 차단, 정적 분석 단계에서 코드 실행 없음
- 격리 실행: 읽기 전용 루트, cap_drop ALL, 리소스 한도, 호스트 포트 없음, 감사별 internal 네트워크
- 실제 모델 키는 암호화 저장소와 LLM 프록시에만 두고, 대상에는 감사 전용 임시 토큰만 전달

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| 백엔드 | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, uv |
| 에이전트 | LangGraph, LangChain, langchain-mcp-adapters, FastMCP |
| 데이터 | PostgreSQL + pgvector, Redis Stack, Kafka (KRaft) |
| 프론트 | Vue 3, TypeScript, Vite, Pinia, ECharts |
| 실행 격리 | Docker SDK (sandbox-runner) |
| LLM | OpenAI (`LLM_SMALL_MODEL`, 임베딩 `EMBEDDING_MODEL`), LangSmith (선택) |

---

## 실행

```bash
cp .env.example .env      # OPENAI_API_KEY, LLM_SMALL_MODEL, JWT_SECRET 등 기입
make up                   # 전체 컨테이너 기동 (healthy 까지 대기)
make demo-reset           # 감사 대상 작업공간(/workspace) 초기화
make seed                 # 데모 계정·대상·지식 문서
open http://localhost:5173
```

`OPENAI_API_KEY`가 비어 있으면 LLM 노드를 건너뛰고 정적 규칙 결과로만 동작한다.

| 서비스 | 포트 |
|---|---|
| frontend | 5173 |
| api (OpenAPI `/docs`) | 8000 |
| business-mcp / rag-mcp | 8101 / 8102 (`/mcp`) |
| target-agent (내장 샘플 에이전트) | 8201 |
| kafka-ui (`docker compose --profile ui up -d kafka-ui`) | 8080 |

> 로컬 개발·시연용 구성이다. 외부에 공개된 네트워크에서 실행하지 말고, 실행 전 `.env`의 `JWT_SECRET`·`MCP_SERVICE_TOKEN`·시드 비밀번호를 새 값으로 바꾼다.

### sentraai CLI (저장소 없이 로컬 실행)

저장소를 받지 않아도 Node 22.13+ 와 Docker Desktop 만 있으면 된다. 로그인 없이 이 컴퓨터에서만 접근할 수 있는 로컬 모드로 뜬다.

```bash
npm install -g https://github.com/skala-pochita/product/releases/download/v0.1.0/sentraai-0.1.0.tgz
cd ~/my-agent-project
sentraai            # Docker 확인 → (처음만) OpenAI·LangSmith 키 입력 → 감사 확인 → 진행 → 대시보드
sentraai setup      # 키 다시 입력          sentraai stop   # 서버 멈춤 (기록 유지)
```

배포는 `cli/package.json` 버전을 올리고 같은 이름의 태그(`v0.1.0`)를 push 하면 GitHub Actions 가 이미지(GHCR)와 CLI(Release)를 올린다. 첫 배포 뒤 GHCR 패키지를 Public 으로 바꿔야 로그인 없이 받을 수 있다. 로컬에서 이미지만 만들어 시험하려면 `make cli-images && make cli-pack`.

### 데모 계정 (운영 금지)

| 이메일 | 역할 | 비밀번호 |
|---|---|---|
| admin@demo.io | ADMIN | `.env`의 `SEED_ADMIN_PASSWORD` |
| dev@demo.io | DEVELOPER | `SEED_DEV_PASSWORD` |
| auditor@demo.io | AUDITOR | `SEED_AUDITOR_PASSWORD` |

### 사용 흐름
1. 감사 실행 화면에서 소스 ZIP(최대 20MiB, 해제 100MiB·5,000개 항목)을 업로드한다.
2. 에이전트 정의가 있으면 소스 + Agent 대상이 함께 등록되고, 지원되는 Compose/Dockerfile이 있으면 격리 실행과 공격 검사까지 수행한다.
3. 감사 중에는 진행 상태만 표시되고, 완료 후 문제점·근거·권장 조치와 보고서를 조회한다.
4. 모델 비교 화면에서 A / B 구성을 내장 정답셋으로 비교한다.

자세한 실행 계약은 [업로드 프로젝트 전체 감사](workHistory/UPLOADED_PROJECT_AUDIT.md)를 참고한다.

### 수정 전후 비교 (수정본 재업로드)
감사기는 원본을 고치지 않는다. 개발자가 직접 고친 수정본을 다시 올리면 같은 공격을 다시 실행해 이전 감사와 비교한다.

1. 감사 실행 화면에서 "이전 버전"을 고르고 수정본 ZIP을 올린다 → 같은 프로젝트의 다음 버전(v2…)으로 등록되고 실행 설정·비밀값을 이어받는다.
2. "수정 전후 비교"에서 기준 감사(이전 버전의 완료된 감사)를 고르고 전체 또는 에이전트 모드로 감사한다.
3. 완료 후 "수정 전후 비교" 탭에서 Finding·공격을 **신규 발견 / 여전히 존재 / 재검증에서 해소·차단 / 확인 불가**로 나눠 본다. 미실행·오류·판정 불확실·실행 모드(실제 LLM·모의) 불일치·정상 대조군 실패는 확인 불가이며 해결로 세지 않는다.

API로도 같은 흐름을 쓸 수 있다.

```bash
curl -F file=@fixed.zip -F previous_target_id=$V1_TARGET -H "Authorization: Bearer $TOKEN" $API/api/v1/targets/upload
curl -H "Authorization: Bearer $TOKEN" $API/api/v1/targets/$V2_TARGET/baselines            # 기준 감사 후보
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"target_ids":["'$V2_TARGET'"],"mode":"full","baseline_audit_id":"'$V1_AUDIT'"}' $API/api/v1/audits
curl -H "Authorization: Bearer $TOKEN" $API/api/v1/audits/$V2_AUDIT/compare                # .md 도 가능
```

## 개발

```bash
make test    # backend 단위·그래프 테스트 (LLM/인프라 불필요)
make lint    # ruff + mypy + vue-tsc
cd frontend && pnpm dev   # http://localhost:5173 (api 프록시)
```

## 저장소 구조

```
backend/     api · core(graph, kafka, db) · harness · mcp_servers · workers · llm_proxy · sandbox
frontend/    Vue 3 대시보드
rules/       코드·Agent·결합 위험 규칙 (YAML)
knowledge/   RAG 지식 문서 (OWASP, CWE, Spring Security, 사내 정책)
datasets/    비교 실험 정답셋
targets/     내장 샘플 감사 대상 (취약 Spring 앱, CS 에이전트 vuln/hardened)
config/      모델·예산·임계값 설정
workHistory/ 기획서·설계 결정·품질 보고서 등 작업 기록
```

## 문서

- [기획서](workHistory/기획서.md)
- [설계 변경 기록](workHistory/DECISIONS.md)
- [ISO/IEC 25010 품질 평가](workHistory/isoReport.md)
- [품질 구현·검증](workHistory/QUALITY_IMPLEMENTATION.md)
- [진행 체크리스트](workHistory/checklist.md)
