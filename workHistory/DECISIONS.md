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

## 2026-10-21 — ISO/IEC 25010 미달 항목 보완

`isoReport.md` 평가에서 미달·부분 달성으로 나온 항목을 보완한다.

- **과거 수정 경로 코드 삭제**: 읽기 전용 전환(2026-10-20) 때 호환용으로 남긴 그래프 노드(`human_approval`·`risk_accept`·`fix_generate`·`llm_fixer`·`apply_fix`·`wait_merge`·`re_verify`·`mark_manual`), `core/fixes.py`, `mcp_servers/business/patches.py`, business-mcp 함수(`request_approval`·`create_fix_proposal`·`apply_fix`·`merge_fix`·`register_risk_acceptance`), `fix_generate` 프롬프트를 지운다. 어디서도 import·등록하지 않았고, 고복잡도 함수와 커버리지 미달의 주원인이었다. DB 테이블(`approvals`·`fix_proposals`·`risk_acceptances`)과 마이그레이션은 기존 데이터 보존을 위해 그대로 둔다. 도구 게이트의 퇴역 도구 차단 목록은 방어용으로 유지한다. 되돌리기는 git 이력에서.
- **장애 주입 지점 이동**: `FAULT_INJECTION` 이 지운 `apply_fix` 경로에 있었으므로 현재 감사 흐름의 `run_static_scan` 으로 옮긴다. 형식은 `rate_limit:N`(감사·대상별 처음 N회 RATE_LIMITED). 과거 값 `github_rate_limit:N` 도 받아들인다.
- **probe 권한 테스트 갱신**: `run_injection_probe` 는 `b558d2b` 부터 ADMIN·DEVELOPER 허용(업로드 대상은 진행 중인 본인 감사·본인 소유만). 테스트를 AUDITOR 거부 + DEVELOPER 소유권 거부 검증으로 바꾼다.
- **보안 헤더**: nginx 에 CSP·X-Frame-Options·X-Content-Type-Options·Referrer-Policy·Permissions-Policy, `server_tokens off`. API 에도 같은 헤더(JSON 은 `default-src 'none'`, `/docs` 제외). CSP `script-src 'self'` 를 위해 인라인 테마 스크립트를 `public/theme-init.js` 로 분리.
- **개요 화면 차트**: 첫 화면 JS 예산(170KB gzip)을 맞추려고 개요의 심각도 도넛을 ECharts 대신 SVG 컴포넌트(`Donut.vue`)로 그린다. ECharts 는 모델 비교 화면에만 남긴다.
- **의존성**: certifi 2026.10.19 (PYSEC-2026-4320), echarts 6.1 + vue-echarts 8 (GHSA-fgmj-fm8m-jvvx).
- **`/quality` API 퇴역**: 품질 화면 제거(2026-10-20) 뒤 프론트·테스트 어디서도 쓰지 않던 고정 판정 집계 API 를 지운다. 품질 평가는 `isoReport.md` 로 대체.
- **LLM 기각 보류 가드레일 확대** (`react_investigator._hold_reason`): 2026-10-20 A/B/C 실측(`b582d34a`)에서 B 가 놓친 코드 케이스 7건 중 5건(C02, C04×3, C06)은 정적 규칙이 잡은 후보를 LLM 이 FALSE_POSITIVE 로 지운 경우였다. 기존 보호는 신뢰도 ≥0.8 후보만 지켰고, `needs_llm_review: true` 규칙(permitAll·주석 속 자격증명·엔트로피)은 신뢰도가 낮아 보호 밖이었다. ① 감사기 인젝션 후보가 있는 파일에서는 LLM 이 다른 후보를 기각할 수 없다(I01: "보고하지 말 것" 주석 아래 C06). ② 자격증명 계열(HARDCODED_SECRET·HARDCODED_CREDENTIAL·SECRET_LEAK·COMMENT_SENSITIVE)은 테스트 경로(`src/test/`) 밖이면 기각 대신 NEEDS_REVIEW. 커밋된 자격증명은 '가짜 값·임시 계정' 주장을 감사기가 검증할 수 없기 때문이다. 함정 D01~D06 중 이 범주에서 정적 후보가 되는 것은 테스트 경로의 D01 뿐이라 함정 오탐은 늘지 않는다. 효과는 실 LLM 재측정으로 확인해야 한다.

## 2026-10-22 — three.js 3D 시각화 (결합 위험 그래프·레드팀 공격 화면)

명세에 없는 화면 요소로, 사용자 요청에 따라 추가한다. 데이터·API·백엔드는 바꾸지 않고 기존 응답과 SSE 이벤트만 읽는다.

- **결합 위험 그래프** (`RiskGraph3D.vue`, 결합 위험 화면): Finding 을 노드(모양 = source, 색·크기 = 심각도), 위험 경로의 연속 Finding 을 엣지로 그린다. 경로 심각도만큼 파티클이 흘러 전파 방향을 보여 준다. 목록 카드에 마우스를 올리면 해당 경로만 강조, 노드 클릭 시 Finding 상세로 이동. 목록은 그대로 두어 3D 없이도 같은 정보를 볼 수 있다.
- **레드팀 공격 화면** (`ProbeArena.vue`): 차단된 시도는 방어막에 튕기고 성공한 시도는 코어를 맞힌다. 감사 진행 화면에서는 `probe.progress` 이벤트(시도 수·성공 수)로 실시간 발사, 레드팀 결과 화면에서는 저장된 시도를 순서대로 재생한다. 이벤트에 시도별 순서가 없어 진행 화면의 성공·차단 순서는 실제 순서가 아니다. 적응형 probe(`P03-A11`)는 기본 probe 발사대에서 쏜다.
- **번들 예산**: three 는 해당 컴포넌트를 `defineAsyncComponent` 로 불러올 때만 별도 청크(약 127KB gzip)로 내려받는다. 첫 화면 JS 예산(170KB gzip)에는 영향 없음.
- WebGL 을 쓸 수 없으면 안내 문구를 보여 준다. 화면 밖에서는 렌더 루프를 멈추고, `prefers-reduced-motion` 이면 자동 회전과 장식 움직임을 끈다. 색은 테마 CSS 토큰을 읽어 라이트·다크 전환을 따른다.

## 2026-10-22 — sentraai CLI 와 로컬 모드 (로그인 없음)

명세에 없는 실행 방식으로, 사용자 요청에 따라 추가한다. 캡스톤 시연이 목적이며, 저장소를 받지 않은 사람이 `npm install -g <Release tgz>` → 감사할 디렉터리에서 `sentraai` 로 감사·대시보드까지 가는 흐름이다. 실행은 두 갈래가 된다: **웹 모드**(기존, 로그인·JWT·역할 3개)와 **CLI 로컬 모드**.

- **배포**: 서버 이미지 3개(backend·frontend·target-agent)를 GHCR(`ghcr.io/skala-pochita/sentraai-*:<버전>`)에 arm64·amd64 로 올리고, CLI 는 `npm pack` 결과를 GitHub Release 에 첨부한다(`.github/workflows/release.yml`, v* 태그). npm 레지스트리는 쓰지 않는다. 저장소가 공개라 이미지 공개로 새로 드러나는 코드는 없다. 이미지에 실제 키가 없는지 `scripts/scan_image_secrets.sh` 로 검사한다(샘플·테스트의 가짜 키 경로 제외). `make cli-images` 는 같은 이름으로 로컬 빌드만 한다(push 없음).
- **로컬 모드** (`AUTH_MODE=local`, `api/local_mode.py`): 토큰 없이 고정 사용자 `User`(ADMIN, 로그인 불가 임의 비밀번호)로 처리한다. 혼자 쓰는 로컬이라 모든 메뉴를 연다. 인증이 없는 대신 ① compose 는 nginx 포트 하나만 `127.0.0.1` 에 열고 API·DB·Kafka·MCP 는 열지 않는다 ② Host 검사로 DNS 리바인딩 차단(nginx 가 `X-Forwarded-Host` 전달) ③ Origin·`Sec-Fetch-Site` 검사로 다른 웹페이지의 요청 차단 ④ 상태 변경 요청에 `X-Sentra-Local: 1` 헤더 요구(교차 출처면 CORS preflight 에서 막힘). 웹 모드 동작은 바뀌지 않는다.
- **CLI** (`cli/`): 외부 의존성 없는 Node(22.13+). 패키지에 든 `compose.yml` 을 `~/.sentraai/` 로 복사해 프로젝트 이름 `sentraai`, 포트 5180 으로 띄운다(웹 모드 5173 과 공존). 첫 실행에 OpenAI 키(입력 숨김, `/v1/models` 로 유효성 확인 — 키는 OpenAI 에만 보냄)와 LangSmith 키(선택)를 받아 `~/.sentraai/.env`(0600)에 저장하고, JWT·MCP 서비스 토큰은 무작위로 만든다. Docker 가 꺼져 있으면 켜 달라고 안내하며 자동 기동도 시도한다. 감사 대상의 `.env` 는 기본 제외(`--include-env`), git 저장소면 `.gitignore` 를 따른다. 지식 문서가 비어 있으면 `api.seed --local` 로 로컬 사용자·보안 기준 문서만 색인한다.
- `cli/compose.yml` 은 저장소 `docker-compose.yml` 과 서비스 목록이 같아야 한다(cli 테스트가 비교).
- **업로드 한도 대응**: 서버 한도(압축 20MiB)는 그대로 두고, CLI 가 감사에 쓰지 않는 큰 파일을 뺀다 — 512KB 넘는 바이너리(확장자 또는 NUL 바이트로 판단), 5MB 넘는 파일. 작은 바이너리(gradle-wrapper.jar 등)는 격리 실행 빌드에 필요할 수 있어 남긴다. 사용자는 `.sentraaiignore`·`--exclude` 로 더 뺄 수 있고, 한도를 넘으면 서버를 띄우기 전에 큰 디렉터리·파일 목록을 보여 준다. 실제 프로젝트가 29.8MB 로 거절된 사례(rc.1)에서 정했다.

## 2026-10-22 — 업로드 에이전트가 격리 실행되지 않던 문제 (leak 사례)

로컬 CLI 로 `leak`(Spring Boot backend + Python agent)을 감사했을 때 에이전트가 한 번도 실행되지 않았고, 보고서에는 `격리 실행 실패: 422` 만 남았다. 원인 세 가지를 고친다.

- **CLI 수집 규칙** (`cli/lib/collect.js`): `target`·`build`·`dist`·`out`·`coverage` 를 이름만으로 빼면서 Java 패키지 `lab/target/` 의 소스 4개가 업로드에서 빠졌다 → `mvn package` 의 Spring Boot repackage 가 "Unable to find main class" 로 실패. 이 다섯 이름은 **바로 옆에 빌드 파일**(pom.xml, build.gradle, package.json, pyproject.toml 등)이 있을 때만 산출물로 본다. `.git`·`node_modules`·캐시류는 지금처럼 이름만으로 뺀다. git 저장소·일반 디렉터리 모두 같은 규칙.
- **CLI 스택 갱신** (`cli/lib/docker.js` `ensureStack`): 서버가 healthy 면 바로 돌아가서, 첫 실행에 입력한 키와 CLI 업그레이드(rc.1→rc.2)가 컨테이너에 반영되지 않았다(컨테이너 안 OpenAI 키 비어 있음 → LLM 조사 생략). 이제 매번 `compose up -d --wait` 를 하고, 이미지 태그나 `.env` 가 바뀐 서비스만 compose 가 다시 만든다. 바뀐 게 없으면 수 초 안에 끝난다. 결과는 `started | updated | running`.
- **실패 사유 전달**:
  - sandbox-runner 는 오류를 `AppError` 로 돌려준다(본문 `{"error": {...}}`). 직접 쓴 메시지(ValueError·RuntimeError·AppError)는 `mask_text` 를 거쳐 그대로, Docker SDK 오류는 이름만 내보낸다. 이미지 빌드 실패에는 서비스 이름과 빌드 도구의 첫 오류 줄(Maven `[ERROR]`, pip `ERROR:`, npm `npm ERR!` 등)을 붙인다.
  - 다른 감사가 실행 중이면 409(CONFLICT), 프로젝트를 빌드·기동할 수 없으면 422(VALIDATION).
  - `core/sandbox_client.py` 가 `detail` 이 아니라 `error.message` 를 읽는다. 422 는 VALIDATION(재시도 안 함 — 같은 프로젝트는 다시 해도 실패, 이전에는 3회 재시도로 84초 소모), 409·5xx 는 UPSTREAM_ERROR(재시도).
  - `prepare_sandbox` 노드가 실패 시 `sandbox.failed` 이벤트를 남기고, CLI 는 전체 모드 감사 끝에 공격 검사를 못 했으면 경고와 사유를 출력한다. 보안 감사에서 "실행 못 함"이 "취약점 0건"으로 읽히는 것을 막기 위해서다.

## 2026-10-23 — 수정본 재업로드 비교·동일 공격 재검증

읽기 전용 감사 방향은 유지하되, 2026-10-20 에 제외한 "수정 후 재검증" 중 **사용자가 직접 고친 수정본을 다시 올려 이전 감사와 비교하는 범위만** 되살린다. 감사기는 여전히 원본을 수정·병합하지 않는다.

- **프로젝트 버전**: `POST /targets/upload` 에 선택 폼 필드 `previous_target_id`(이전 버전의 업로드 소스 대상). 새 대상 `config` 에 `project_id`(첫 업로드의 대상 ID), `version`(1부터), `previous_target_id` 를 기록한다. 마이그레이션 없이 기존 JSONB 를 쓴다. 이전 버전이 없는 업로드·내장 샘플 대상은 자기 ID 가 `project_id` 이고 `version` 1 이다.
- **실행 설정 승계**: 같은 소유자가 버전을 연결하면 이전 버전의 실행 설정(공개 값)을 복사하고 비밀값은 새 대상 범위로 다시 암호화해 저장한다(참조 공유 안 함 — 한쪽 삭제가 다른 버전을 깨지 않게).
- **기준 감사**: `POST /audits` 에 선택 필드 `baseline_audit_id`. 완료된 감사이고, 요청자가 접근할 수 있고, 같은 프로젝트(동일 `project_id`)여야 한다. `audits.baseline_audit_id` 열 추가(0005).
- **이슈 매칭**: 기존 fingerprint 는 대상 ID 를 포함해 버전 간 비교에 쓸 수 없다. 비교는 별도 키로 한다 — 코드·정적 Finding 은 (출처, 카테고리, 규칙, 상대 경로, 정규화 스니펫) 정확 일치 후 (출처, 카테고리, 규칙, 경로) 1:1 완화 일치, probe Finding 은 기준 probe ID, 결합 위험은 규칙·카테고리. 라인 번호는 수정으로 밀리므로 쓰지 않는다.
- **판정 4종**: 신규 발견 / 여전히 존재 / 재검증에서 해소·차단 / 확인 불가. 이번 감사가 해당 범위를 실행하지 않았거나(모드·대상·노드 오류), probe 가 미실행·오류·판정 불확실이거나, 두 감사의 모델 검증 모드(LIVE_VERIFIED·MOCK·UNVERIFIED)가 다르거나, LLM 이 찾은 Finding 인데 이번 감사에서 LLM 조사가 돌지 않았으면 "확인 불가"로 두고 해결로 세지 않는다. 정상 대조군(BENIGN_CONTROL)이 이번 실행에서 실패하면 공격 "차단"도 확인 불가로 낮춘다(기능이 망가져 막힌 것일 수 있음).
- **동일 공격 재실행**: 업로드 에이전트의 probe 세트는 프로젝트 정의(매니페스트·`audit-probes.yaml`)에서 결정론적으로 정해지고 내장 샘플은 기본 probe 를 항상 실행하므로, 새 감사가 같은 probe 를 다시 실행한다. 기준 감사의 probe 가 이번에 실행되지 않으면 확인 불가로 표시한다.
- **비교 결과 저장 위치**: 보고서 노드는 바꾸지 않는다. 두 감사가 모두 완료된 뒤 API 가 계산한다(`GET /audits/{id}/compare`, `.md`). 결과는 두 감사의 저장된 Finding·보고서에서만 만든다.
