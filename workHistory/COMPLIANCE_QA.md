# 규제 검토 권고 구현 QA — 2026-10-23

기준 코드: bf06310, 브랜치: dev_js. 외부 저장소 push 없음.

## 실행 결과

| 검사 | 결과 |
|---|---|
| 전체 백엔드 pytest (기본 설정 not llm) | 159 passed, 37 skipped, 3 deselected |
| 신규 원문/매핑/보고서/HTTP/파이프라인 단위 테스트 | 36 passed |
| 신규 실제 Postgres/pgvector 통합 테스트 | 2 skipped: Postgres 연결 불가 |
| ruff check backend | 통과 |
| mypy core harness mcp_servers | 96 source files, 오류 없음 |
| CLI node --test | 9 passed |
| 변경 Vue SFC 3개 script/template 컴파일 | ReportPage / FindingPage / AuditNewPage 통과 |
| 전체 vue-tsc | 미통과: 기존 3D 화면의 three / OrbitControls / 타입 패키지 없음 |
| migration 0004 → 0005 오프라인 SQL 생성 | 통과: CHECK 제약 갱신과 규제 ref_code 부분 고유 인덱스 |
| Git diff 공백 검사 | 통과 |
| 원문/추출물/로컬 매니페스트 Git 제외 | 통과 |
| Web/CLI Compose 읽기 전용 원문·매핑 마운트 및 컨테이너 설정 경로 | 통과 |

37개 skip은 실제 PostgreSQL·MCP·서비스 환경에 의존하는 테스트들이다. 통합 검사 성공으로 계산하지 않았다.
3개 deselected는 기본 pytest 설정의 실제 LLM 제외에 따른다.

## 새 테스트가 확인한 내용

- 조·항·호와 원문자 점검 항목을 분리하되 원문 문구를 바꾸지 않는다.
- 원문·추출 텍스트·조항·매니페스트 변조와 경로 이탈을 차단한다.
- 인용 문구/해시/원문 해시 불일치를 차단한다.
- rule_id > category > standard_ref 우선순위와 다중 연결, 기각 시 fallback 차단.
- 미해결 상태를 보수적으로 처리하며 오탐을 제외한다.
- MOCK 및 실제 제공자 미확인 probe는 NOT_ASSESSED.
- 오류·정상 대조군 실패는 깨끗한 검사 증거로 승격하지 않는다.
- 민감값 마스킹 후 해시 검증과 마스킹으로 바뀐 인용의 검증 라벨 제거.
- ADMIN/AUDITOR만 별도 규제 JSON/MD export에 접근한다.
- 과거 보고서를 현재 매핑으로 재계산하지 않고 저장 스냅샷을 렌더한다.
- 오프라인 연결 파이프라인의 구조화 출력, untrusted 래핑, 정확 조회 대조, 인용 오류 시 기존 파일 보존.
- 관련성의 사람 확인·기각은 재생성에도 유지한다.
- 보고서 노드가 실제 제공자 호출 검증을 마친 뒤 최종 권고 상태를 계산한다.

## 환경과 미실행 항목

이 환경은 GitHub/패키지 레지스트리의 터미널 DNS 조회가 제한되어 있다.
기존 로컬 의존성을 읽어 테스트했고, 누락된 Docker SDK는 공식 docker/docker-py 7.1.0 태그의 파일을 blob SHA 대조 후 격리된 로컬 테스트 환경에만 두었다. 배포 코드에는 vendoring하지 않았다.
Docker 소켓 접근이 거부되어 컨테이너 기동/DB migration 적용/실제 서비스 전체 감사는 실행하지 못했다.

전달된 첨부 2건은 설계 문서이며 실제 규제 PDF가 아니다.
기본 카탈로그는 0개 조항·0개 연결·원문 미확보 상태다. 이전 Claude 세션에서 확보했다는 PDF나 해시는 이 작업 환경으로 복구되지 않았다.
실제 원문 반입·인덱싱·실제 OpenAI 관련성 평가가 완료됐다고 주장하지 않는다.
초기 후보는 실제 원문 인용 검증을 통과해야 생성되며 현재 상태에서는 검증된 근거를 만들지 않는다.

전체 프런트 빌드는 three 의존성을 설치할 수 있는 환경에서 다시 실행해야 한다. 변경된 컴포넌트의 SFC 컴파일은 별도로 통과했다.
실행 절차는 COMPLIANCE_RUNBOOK.md에 있다.
