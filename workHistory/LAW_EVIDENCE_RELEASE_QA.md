# 규제 증적 기능 최종 통합·보안·배포 QA

검증일: 2026-10-23. 대상 브랜치: `feat/add_law_evidence`.

## 기준과 결론

원격 main `0240c71d24be857c757512b1d19bd231c7e1eb69`를 새로 clone하고 규제 기능을 통합한 `28412a9` 위에서 보완했다. 기존 main의 공격 경로 증적, 소스 조회, 수정본 재업로드, 기준 감사 비교를 유지한다. 기존 파일을 삭제하지 않았고 마이그레이션은 main의 `0005 → 0006 → 0007` 다음에 규제 유형 `0008`을 적용한다.

push 직전 main을 다시 fetch해 README 이미지 추가만 포함한 `4e24d0a`도 merge했다. 해당 이미지와 위 기능·QA 문서를 모두 보존했다.

기본 검사 265개와 별도 실 LLM·Kafka E2E 3개, CLI 9개가 통과했다. 실제 Docker에 전체 서비스를 올린 뒤 ZIP 업로드부터 저장 보고서까지 MOCK/live 각각 완료했다. 이 기록은 아래 환경에서 관찰한 결과이며, 모든 대상·모델·외부 운영 서버의 정상 작동을 보증하지 않는다.

이전 [main 통합 QA](COMPLIANCE_MAIN_INTEGRATION_QA.md)의 “LLM·Kafka·컨테이너 실행 미검증” 범위는 이번 별도 실환경 검사로 보완했다. 이전 문서 자체는 당시 기록으로 유지한다.

## 발견 사항과 해결·검증

| 발견 사항 | 해결 | 검증 결과 |
|---|---|---|
| 보고서 종류 버튼이 옆 감사 탭과 붙어 있고 줄이 맞지 않음 | 동일 flex 행, 중앙 정렬, 그룹 간 20px·줄바꿈 간 12px, 선택 상태 `aria-pressed` | 실제 Docker 프런트 Chrome에서 두 그룹 y·높이 동일, 수평 간격 20px. 규제 탭 전환·원문 출처·MOCK 미점검 표시 확인 |
| 규제 전용 API 권한을 일반 JSON/Markdown 보고서로 우회 가능 | DEVELOPER 응답에서 규제 섹션·프로필·Finding 참조 제거. 보안 보고서의 공격 경로 등은 유지 | 실제 DB 보고서 공존 회귀 검사 통과. ADMIN/AUDITOR 전체 스냅샷 유지 |
| 무결성 키 삭제·잘못된 자료형으로 검증 누락 또는 500 가능 | schema 2의 해시 누락·불일치·잘못된 형식을 일관되게 거부 | 변조/키 삭제/잘못된 자료형을 규제·일반 JSON/MD API에서 모두 거부하는 회귀 검사 통과 |
| sandbox 정리가 같은 Docker daemon의 다른 스택까지 포함할 수 있음 | 생성·조회·만료 정리에 namespace 라벨 적용. CLI의 전용 network·relay 이미지도 구성 | 다른 namespace 정리 차단 단위 검사와 실제 생성·정리 통과 |
| agent 건강 확인 직후 backend 요청이 거절됨 | 선언된 모든 서비스의 TCP 포트 준비를 확인한 뒤 agent health 평가 | 초기 MOCK 전체 감사에서 재현한 문제 수정. 이후 MOCK/live 전체 흐름에서 errors 빈 배열 |
| API 컨테이너 교체 뒤 nginx가 예전 IP를 계속 사용해 502 발생 | Docker DNS를 주기적으로 재조회하는 upstream 구성 | frontend를 교체하지 않고 API만 재생성. 일시 502 후 200 복구 확인 |
| Maven Central 접근 실패가 일반 빌드 오류로만 표시됨 | FATAL 원인 줄을 보존하고 Maven 일반 안내 줄 제외. 공식 Google Maven Central 미러를 쓰는 QA ZIP 생성 도구 추가 | 원본 ZIP/애플리케이션/probe 보존. TLS 검증 유지. QA 사본 실제 빌드·실행 성공 |
| 개발 포트가 외부 인터페이스에도 노출됨 | Compose 공개 포트를 127.0.0.1로 한정 | 실제 QA 스택 바인딩 확인 |
| 로그인 화면에 이미 폐기한 자동 수정·PR 기능 설명이 남음 | 실제 읽기 전용 감사와 규제 검토 기능으로 문구 갱신 | 타입 검사·프런트 전체 빌드 통과 |
| PR 회귀 검사가 없어 merge 전 CI 근거 부족 | DB 회귀·정적 검사·프런트/CLI·이미지 3종 빌드 및 비밀값 검사 CI 추가 | 원격 실행 상태는 PR의 GitHub Actions 기록을 기준으로 확인 |

## 실행 결과

| 검사 | 결과 | 증적 |
|---|---|---|
| PostgreSQL 포함 기본 pytest | **265 passed, 3 deselected**, skip 0 | [backend-final.txt](../reports/release-law-qa/backend-final.txt) |
| 분리 E2E: Kafka 감사 완료, SSE, 실제 LLM P01/P03/P05 smoke | **3 passed** | [e2e-live-infra.txt](../reports/release-law-qa/e2e-live-infra.txt) |
| ruff / mypy | 통과 / 105개 소스 통과 | [ruff](../reports/release-law-qa/ruff-final.txt), [mypy](../reports/release-law-qa/mypy-final.txt) |
| 프런트 vue-tsc·Vite build | 통과. 500kB 초과 청크 경고는 남음 | [frontend-final.txt](../reports/release-law-qa/frontend-final.txt) |
| CLI | **9 passed**, skip 0 | [cli-final.txt](../reports/release-law-qa/cli-final.txt) |
| Docker 이미지 | backend·target-agent·frontend 실제 빌드 성공 | [build](../reports/release-law-qa/docker-build.txt), [frontend](../reports/release-law-qa/frontend-docker-build.txt) |
| 이미지 내 환경파일·키 패턴 검사 | 3종 통과 | [image-secrets-final.txt](../reports/release-law-qa/image-secrets-final.txt) |
| 운영 Python 의존성 / 프런트 의존성 감사 | Python 88개에서 알려진 취약점 0 / 프런트 0 | [Python](../reports/release-law-qa/dependency-audit.json), [frontend](../reports/release-law-qa/frontend-audit.json) |
| 실제 sandbox 격리 | 컨테이너 3개, 비루트·읽기 전용 루트·privileged 금지·호스트 bind/포트 없음. 직접/간접 공격 실행 후 정리 | [sandbox-isolation.txt](../reports/release-law-qa/sandbox-isolation.txt) |
| API 재시작 후 nginx 복구 | 같은 frontend 컨테이너에서 502→200 | [nginx-recovery.json](../reports/release-law-qa/nginx-recovery.json) |
| 실제 보고서 화면 | 정렬·간격·규제 탭·경고 없음 | [browser-checks.json](../reports/release-law-qa/browser-checks.json) |

기본 검사에서 deselect한 3개를 위 E2E로 별도 실행했다. PostgreSQL을 제공했으므로 기본 DB 통합 검사를 건너뛰지 않았다. Python 의존성 감사는 고정 lock에서 운영 의존성을 추출해 수행했으며, 알려지지 않은 취약점이나 base OS 전체 안전성까지 증명하지 않는다.

### 실제 ZIP → 보고서

전용 Compose 프로젝트 `sentra-law-qa`에 13개 서비스를 배포했다. PostgreSQL·Redis·Kafka·API·감사/평가/재시도 워커·MCP 2종·LLM proxy·sandbox controller·내장 target·frontend가 healthy인 상태에서 API로 ZIP을 업로드했다. 기존 개발 스택은 별도로 유지했다.

| 항목 | MOCK | live |
|---|---|---|
| 감사 ID | `d34b5d40-91ac-4acb-bdcd-b58569fdaff8` | `ed0ff07e-16fb-40e8-af86-d77ea76c45ef` |
| 상태 | COMPLETED | COMPLETED |
| 모델 확인 | MOCK | LIVE_VERIFIED |
| Findings / 규제 권고 | 5 / 16 | 4 / 16 |
| 업로드 probe | UP-BENIGN-01, UP-PATH-01, UP-RAG-01 | 동일 3종 |
| 실제 제공자 성공 호출 | 0 | **4**, 청구 토큰 1,518, gpt-5.4-nano |
| 보고서 errors | 없음 | 없음 |
| 일반·규제 API의 저장 Finding 스냅샷 대조 | 일치 | 일치 |

증적: [MOCK 요약](../reports/release-law-qa/deployed-mock.json), [live 요약](../reports/release-law-qa/deployed-live.json), [MOCK 보고서](../reports/release-law-qa/deployed-mock-report.md), [live 보고서](../reports/release-law-qa/deployed-live-report.md). 실제 키를 대상 ZIP·컨테이너에 복사하지 않고 암호화 vault → proxy → 감사 전용 relay 경로를 사용했다.

원문은 로컬의 5종 403개 단위, 검색 후보 110개를 사용했다. AI RMF는 보도자료만 보존하며 확인하지 못한 본문을 근거로 생성하지 않았다. SQLite/PG 고정 수용 질의 각 8개와 인용 대조는 [main 통합 QA](COMPLIANCE_MAIN_INTEGRATION_QA.md)의 결과를 함께 따른다. 사람 확인 완료로 승격한 후보는 0개이며, 실제 LLM 공격 확인과 법적 적용 판단을 구분한다.

## 재현

자격증명은 환경변수로만 전달하고 출력하지 않는다. 기존 업무 DB와 분리된 `auditor_test` DB에서만 pytest를 실행한다.

```bash
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://auditor:auditor@localhost:55432/auditor_test \
  OPENAI_API_KEY= uv run pytest -q -rs
uv run ruff check .
uv run mypy core harness mcp_servers

# 실행 중인 전용 QA 스택·검사용 계정/모델 설정이 필요하다.
API_URL_HOST=http://127.0.0.1:58001/api/v1 TARGET_AGENT_URL_HOST=http://127.0.0.1:58202 \
  uv run pytest -m 'llm or infra' tests/e2e
cd ..
python scripts/prepare_runtime_qa_fixture.py
# QA_PASSWORD 및 live의 QA_PROVIDER_API_KEY를 안전하게 환경으로 제공한다.
python scripts/qa_deployed_stack.py --mode mock --zip workspace/qa-runtime.zip
python scripts/qa_deployed_stack.py --mode live --zip workspace/qa-runtime.zip
```

로컬 프런트 최종 검사는 이미 설치된 잠금 의존성으로 `npm run build`를 사용했다. 로컬 pnpm의 자동 재설치가 공유 node_modules 디렉터리에 대해 TTY를 요구했기 때문이다. 실제 이미지 빌드는 별도 깨끗한 환경의 frozen-lockfile 설치로 완료했다.

## 배포 경계와 남은 한계

- CI는 PR 및 main/feat push에서 실행한다. 기존 release는 버전 태그에서 GHCR 이미지와 CLI를 게시한다. 이번 작업은 새 버전 발행이나 사용자가 지정하지 않은 운영 서버 배포를 수행하지 않는다. 실제 Docker 전체 흐름 검증과 원격 운영 배포 완료를 혼동하지 않는다.
- SHA256은 저장 데이터의 일관성 검사이며 서명이나 관리자에 의한 악의적 DB 변경 방지 수단은 아니다. 원문·검토 이력·DB·vault master key의 접근 제어와 백업은 별도로 필요하다.
- Docker는 VM 수준 격리가 아니다. controller의 Docker socket은 신뢰되는 전용 실행 서버에서 관리해야 한다.
- 원문과 생성 인덱스는 사용자 결정대로 Git/이미지에 넣지 않는다. 새 설치에는 원문 반입·색인·관련성 검토가 필요하다. 누락 시 NOT_ASSESSED로 처리한다.
- 법령 최신성 감시, 전문가의 적용 조건/관련성 확인, 운영 절차 증적은 자동화된 코드 검사로 대체하지 않는다. 고정 질의 8개는 독립적인 법률 정확도 평가가 아니다.
- 프런트의 큰 청크 경고는 기능 실패가 아니며, 향후 실제 로딩 측정과 분할 최적화 대상이다.
- 추적 소스에서 `contributed by claude`/Claude 공동 작성 footer는 검색되지 않았다. 신규 커밋·PR에는 해당 footer를 넣지 않는다. 기존 공유 main의 과거 커밋 이력은 재작성하지 않는다.
