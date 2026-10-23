# 최신 main + 규제·RAG 기능 통합 QA

검증일: 2026-10-23

## 판정

최신 main `0240c71d24be857c757512b1d19bd231c7e1eb69`를 새로 clone한 뒤 **main의 기능을 유지하면서 규제·RAG 기능을 합쳤고, 코드 통합 QA를 통과했다.** 새 로컬 브랜치는 `dev_js_main_qa`다. 기존 `SentraAI-dev-js` 작업은 보존했다. push와 원격 merge는 하지 않았다.

실제 DB를 포함한 기본 테스트 260개, CLI 테스트 9개, 백엔드 lint·타입 검사, 프런트 타입 검사·전체 빌드가 통과했다. 기존 main만 실행했을 때 발견된 마이그레이션 테스트 기대값 오류도 수정했다. 현재 검증 범위에서 재현된 미해결 실패는 없다. 이 결과는 모든 운영 환경과 실 LLM 동작의 무결함을 보증하지 않는다.

## 기준과 통합 방법

- 원격 저장소: https://github.com/skala-pochita/SentraAI
- 기준 main: `0240c71` (작업 마지막에도 원격 main SHA 동일 확인)
- 이전 기능: `b7b10ed` + 기존 폴더의 미커밋 QA 개선 작업 전체
- 별도 import 브랜치에 보존 체크포인트를 만든 후, main에서 만든 브랜치로 3-way merge
- 기존 main 파일 357개 모두 유지, 중요 main 파일 12개는 원본과 바이트 일치
- 변경한 기존 Python 모듈 14개에서도 main의 클래스·함수 이름 삭제 없음. 이 정적 확인을 실제 회귀 테스트로 보완했다.
- 보존 증거: `reports/main-compliance-qa/main-preservation.json`

## main 기능 보존 확인

| 기능 | 통합 결과와 확인 |
|---|---|
| 정적 코드 검사·기존 Findings | 기존 테스트 포함 전체 회귀 통과, 실원문 시나리오에서도 Findings 저장·조회 |
| 공격 경로·코드 근거·실행 기록 | main의 엔진/검증/화면 보존, 실제 DB/API 통합 테스트 및 Chrome 단계별 근거 조회 200 |
| 수정본 재업로드·버전 비교 | main의 업로드·비밀값 승계·동일 공격 재실행 테스트 통과, v1/v2 비교 화면 조회 |
| 기준 감사 지정 | `baseline_audit_id`와 `compliance_profile` 동시 저장, 워커 초기 상태 전달 테스트 추가 |
| 감사 실행 준비 조건 | main의 시작 제한·실행 설정 이벤트 유지, 화면의 시작 제한 안내 확인 |
| 기존 감사 보고서 | 공격 경로/미확인 구간/Findings/토큰 정보를 유지하며 규제 섹션 추가 |
| 규제 증적 JSON/Markdown | 실제 저장된 보고서 조회, 무결성 검증, 브라우저 다운로드와 DB JSON 일치 |
| 접근 제어 | 실제 DB/API의 완료 전 409, DEVELOPER 규제 조회 403, 기존 main 권한 테스트 통과 |

## 수정한 문제와 해결 검증

| 문제 | 해결 | 검증 결과 |
|---|---|---|
| 기존 기능 브랜치가 최신 main 이전 버전 | 최신 main을 새로 clone하고 양쪽 변경을 3-way merge | main 테스트 전체 포함 260개 통과 |
| 감사 생성 API·프런트 충돌 | 기준 감사와 규제 프로필을 함께 전송·저장 | 실제 API 생성 및 워커 초기 상태 테스트 통과 |
| 공격 경로 보고서와 규제 보고서 변경 중첩 | main의 모든 기존 보고서 필드를 보존하고 검증 완료 후 규제 섹션을 봉인 | 단일 저장 보고서에 두 기능 공존, DB/API/브라우저 대조 통과 |
| 규제 마이그레이션 `0005` 중복 | main `0005→0006→0007` 다음에 `0008_regulation_sources` 연결 | 신규 설치·main 0007 업그레이드·기존 데이터 보존 통과 |
| main 자체 마이그레이션 테스트의 기대값이 0006으로 낡음 | 실제 최신 선형 이력 0008까지 검증 | main 기준 1실패/195통과 → 통합본 260통과 |
| DB RAG 테스트의 임시 설정에 rag.yaml 없음 | 테스트 픽스처에 실제 검색 설정 복사 | pgvector 검색·정확 조회·멱등 적재·변조 거부 통과 |
| 로컬 전용 generated 링크는 clone에 따라오지 않음 | 기존 로컬 검토 파일과 원문을 새 폴더에도 보존, 기본 후보로 재생성 가능 | 실원문 시나리오에서 규제 권고 생성 확인 |
| 규제 연결이 0건이어도 실원문 QA가 통과 가능 | 실제 활성 연결 및 위험 예제의 최소 연결 수를 필수 검증 | 위험 manifest 4건 중 3건 연결·1건 보류, 안전 예제 0건 |
| CLI 기본 후보·정책이 개선 전 상태 | 최신 manifest·후보·분류 정책으로 패키지 기본값 동기화 | CLI 9개 통과, 배포 기본 파일 일치 확인 |

## 실행 결과

| 검사 | 결과 | 로그 |
|---|---|---|
| 변경 전 main, 실제 QA DB | 195 통과, 1 실패: 낡은 migration 기대값 | `main-baseline-postgres-tests.txt` |
| 통합본 기본 전체, 실제 QA DB | **260 통과, 0 실패, 0 skip, 별도 대상 3개 deselect** | `postgres-tests.txt` |
| 두 기능 동시 동작 및 DB RAG 집중 검사 | 4 통과 (전체 260개에 포함) | `coexistence-tests.txt` |
| CLI `npm test` | **9 통과** | `cli-tests.txt` |
| 백엔드 `ruff check .` | 통과 | `ruff.txt` |
| `mypy core harness mcp_servers` | 105개 소스 파일 통과 | `mypy.txt` |
| 프런트 `npm run build` | vue-tsc + Vite 통과, 500kB 초과 chunk 경고만 있음 | `frontend-build.txt` |
| 실원문 SQLite 검색 | 8/8 수용 사례 통과 | `corpus-acceptance.txt` |
| 실원문 PostgreSQL/pgvector 검색 | 403개 적재, 8/8 수용 사례 및 정확 인용 대조 통과 | `postgres-corpus-search.json` |
| Alembic 실제 DB 적용 | fresh→0008, main 0007→0008 통과 | `migrations.json` |
| 이전 감사 데이터·baseline 참조·공격 경로 필드 | 유지 | `migration-data-check.txt` |
| 규제 데이터가 있는 상태의 downgrade | 의도대로 거부, 데이터 삭제 방지 | `migration-downgrade-guard.txt` |
| Chrome 실제 API 화면·다운로드 | 보고서·규제·공격 경로 근거·비교·감사 폼 확인 | `browser-checks.json`, `browser-download.json` |

위 로그 경로는 `reports/main-compliance-qa/` 기준이다. Python 명령은 기존 backend 가상환경의 Python 3.12를 사용했다. 일반 셸의 로컬 네트워크 제약 때문에 실제 DB 테스트는 네트워크 접근 가능한 로컬 프로세스로 실행했으며, 테스트 DB만 별도 컨테이너의 55432 포트에 두었다. 기존 실행 중인 서비스·운영 DB는 변경하지 않았다.

## 실제 보고서 생성

원문 5종, 403개 원문/발췌 단위, 검색 후보 110개를 사용했다. AI RMF 보도자료는 참고로만 유지하고, 확인하지 못한 본문은 넣지 않았다. 관련성 `human_confirmed`는 0개이며, AI 검토 결과를 사람 확인으로 승격하지 않았다.

실제 API에서 감사를 생성하고 워커·그래프·MCP의 in-process 경로를 통해 PostgreSQL에 저장했다. Redis는 fakeredis, Kafka 발행은 대역, 에이전트 응답은 실제 샘플 정책을 따르는 시뮬레이터이며 OpenAI 호출은 0회다. 두 시나리오의 외부 대상은 실 LLM 검증이 아니고, 기본 main의 외부 대상 경로는 비교 보고서에 EXTERNAL/공격 경로에 unknown으로 기록된다. 별도 업로드 MOCK 경로의 표시와 판정은 기존 main 통합 테스트에서 검증했다.

| 시나리오 | 완료 상태 | Findings | 공격 경로 | 규제 권고 |
|---|---|---:|---:|---:|
| v1 취약 샘플 | COMPLETED | 53 | 9 | 16 |
| v2 에이전트 정책 보완 샘플 | COMPLETED | 23 | 0 | 16 |

v2의 서버 코드는 같은 샘플이므로 코드 Finding이 계속 남는 것이 정상이다. 샘플 비교 수치는 실제 LLM 방어 성능이나 실환경 취약점 해소율이 아니다.

생성물은 `runtime-v1-report.json/.md`, `runtime-v2-report.json/.md`, `runtime-comparison.json`, `runtime-summary.json`이다. 브라우저의 `compliance_e566c18c.json` 다운로드에 포함된 report_snapshot은 저장된 v1 JSON과 일치했다.

## 남은 범위와 머지 시 적용 사항

- **코드 통합과 기본 회귀 QA는 통과했다.** 이 브랜치를 PR 검토 대상으로 올릴 수 있는 상태다.
- 기본 pytest에서 분리한 실 LLM·Kafka 등 3개 테스트와 실제 Docker sandbox/운영 배포 전체 흐름은 이번에 실행하지 않았다. 목록: `optional-tests.txt`.
- 실 OpenAI 임베딩·관련성 평가·법률 전문가의 조항 적용 검토는 미실행이다. 검색 8건은 고정 수용 사례이며 독립적인 검색 품질 벤치마크가 아니다.
- 실제 원문·추출물·SQLite 인덱스·generated 검토 이력은 기존 방침대로 로컬 전용이다. 이 새 폴더에는 옮겨 두었지만 다른 장비에 clone할 때는 runbook대로 원문 반입/색인/후보 생성을 해야 한다.
- main DB에는 `0008`을 적용한다. 이미 이전 기능 브랜치의 별도 `0005_regulation_sources`를 실제 적용한 DB는 리비전 충돌을 먼저 조사해야 하며 임의 stamp/reset을 해서는 안 된다. 이번 검증 대상은 최신 main의 정상 `0007` DB였다.
- merge 시점에 main이 다시 바뀌면 최신 변경을 합친 후 관련 회귀 검사를 다시 실행한다.

## 재실행

```bash
# 전용 QA PostgreSQL을 준비하고 TEST_DATABASE_URL 지정 (기존 테스트는 auditor_test를 초기화함)
cd backend
OPENAI_API_KEY='' TEST_DATABASE_URL='postgresql+asyncpg://auditor:auditor@localhost:55432/auditor_test' python -m pytest -q
ruff check .
mypy core harness mcp_servers
cd ../frontend && npm run build
cd ../cli && npm test

# 저장소 루트, 로컬 원문/검토 링크가 준비된 경우
python scripts/qa_compliance_evidence.py
# 전용 빈 sentra_qa_* DB에 alembic upgrade head 적용 후 실행
python scripts/qa_main_compliance_runtime.py --database-url 'postgresql+asyncpg://auditor:auditor@localhost:55432/sentra_qa_runtime_new'
```

이 문서는 이전 `COMPLIANCE_MAIN_FEATURE_COMPARE.md`, `COMPLIANCE_REVIEW_AND_UI_QA.md`의 통합 전 판정과 수치를 대체한다.
