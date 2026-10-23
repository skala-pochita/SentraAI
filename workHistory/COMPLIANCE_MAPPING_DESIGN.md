# 규제·가이드라인 검토 권고 — 구현 설계 v2.1

기준: `bf06310b8cf618bc117c5bdc8f8b0d3b2dc885a0`, 로컬 브랜치 `dev_js`.
사용자가 전달한 「붙여넣은 텍스트(1).txt」(v1)과 「붙여넣은 텍스트(2).txt」(v2), 후속 대화의 결정을 반영했다.
이 문서는 구현 계약을 설명한다. 이전 세션의 원문 확보·법령 시행일·해시 대조 주장을 재검증한 문서가 아니다.

## 최종 동작

등록된 원문에서 조항을 만들고 RAG에 조항당 한 청크로 색인한다. 관련성 연결은 별도 명령에서 생성한다.
감사 중에는 로컬 카탈로그로 결정적으로 매핑하며, 추가 LLM 호출을 하지 않는다.
Finding/DB 스키마는 유지하고 `report_json.compliance.schema = 2`에 결과를 저장한다.

| 단계 | 구현 |
|---|---|
| P1 | `core/regulation_sources.py`, `core/compliance_cli.py import`: PDF/TXT 반입, 원본·텍스트 SHA256, 조·항·호/원문자 점검항목/절 경계, 원문 포함 대조 |
| P2 | `mcp_servers/rag/regulations.py`, MCP `search_regulation`/`get_clause`, migration `0005`: 조항당 문서·청크 하나, 정확 조회 시 로컬 원문과 재대조 |
| P3 | `compliance_cli link/bootstrap/review`: 검색 후보 → 구조화된 모델 관련성 추정 → 정확 조항 재조회 → 인용/해시 검증 → YAML 연결 |
| P4 | `core/compliance.py`, report 노드, 결과 API와 화면: 검토 권고·원문 인용·관련성·조건·미점검 사유 |
| P5 | 선택 프로필: 업종/고영향 여부를 감사 요청·워커·체크포인트·보고서에 전달 |

## 원문 관리

- `knowledge/sources/MANIFEST.yaml`: 문서 ID·이름·발행자·추출 형식의 기본 목록. 실제 원문이 없는 기본 상태는 MISSING이다.
- `MANIFEST.local.yaml`: 반입한 파일의 원본/텍스트 해시, 출처 URL·버전(사용자 입력), 취득 시각, 로컬 경로.
- `raw/`, `text/`, `clauses/`: 로컬 전용. Git과 Docker 이미지에서 제외한다.
- 사용자 제공본을 등록했다는 사실만 기록한다. 공식본 동일성이 검증됐다고 표시하지 않는다.
- AI RMF 보도자료는 `format: notice`, `regulatory_evidence: false`이며 본문으로 색인하지 않는다. 미확보 RMF 본문도 후보로 사용하지 않는다.
- 금융위 가이드라인의 거버넌스 원칙은 오프라인 추가 질의에서 후보를 검색한다. 실제 원문이 없거나 인용을 검증할 수 없으면 연결하지 않는다.
- PDF 추출에는 `pdftotext -layout`을 쓴다. 백엔드 이미지에 poppler-utils를 포함한다. 스캔본에는 별도 OCR이 필요하다.
- 파서가 조항 경계나 중복 조항 ID를 확정할 수 없으면 반입을 중단한다. 인용을 맞추기 위해 문장이나 구두점을 고치지 않는다.

## 연결 데이터와 검증

`config/compliance/catalog.yaml`의 버전과 조항·연결 내용으로 카탈로그 digest를 만든다.
`links/*.yaml`의 각 연결에는 다음을 기록한다.

- 키: rule_id > category > standard_ref 우선. 감사 실행 참고 자료는 audit 키.
- 원문 조항 ID, 짧은 연속 인용, 인용 SHA256, 원문 텍스트 SHA256.
- 관련성: ai_estimated / human_confirmed / rejected. 사람 결정에는 확인자와 시각이 필요하다.
- 생성 모델·프롬프트·시각, 연결 이유. 인용 일치는 관련성의 정확성을 보증하지 않는다.

원문·조항·인용·해시 불일치, 없는 조항 참조, 중복 연결은 로더가 거부한다.
세부 키에 기각 기록이 있으면 상위 범주의 fallback으로 우회하지 않는다.
`generated.yaml`에 초기 후보와 모델 출력을 모으고, 재실행 시 human_confirmed/rejected는 보존한다.
초기 후보는 이전 설계의 관련성 추정임을 기록하며 실제 LLM 측정 결과로 표시하지 않는다.
원문이 없는 초기 후보는 생성 결과에 포함하지 않는다. 보안 안내서는 가장 좁은 일치 절이 유일한 경우에만 연결한다.

## 출력 상태

| 상태 | 의미 |
|---|---|
| REVIEW_RECOMMENDED | 관련 미해결 Finding. FIXING·MANUAL_REQUIRED·ACCEPTED_RISK도 해결로 간주하지 않는다. FALSE_POSITIVE 제외 |
| NO_RELATED_FINDING | 검증 가능한 실행 증적이 있고 관련 미해결 Finding이 없음. 운영 절차의 이행 여부를 뜻하지 않음 |
| NOT_ASSESSED | 원문/연결 없음, MOCK, 실제 제공자 호출 미확인, 오류/미실행, 정상 대조군 실패 등 |
| OUT_OF_SCOPE | 조항 메타데이터에 조직 절차 등 점검 불가 범위로 지정 |

깨끗한 공격 검사 증거는 LIVE_VERIFIED, 검사 완료, 관련 probe 실행, 오류 없음, 정상 대조군 성공을 함께 요구한다.
정적 Finding이 없는 것만으로 점검 완료를 추론하지 않는다.

모든 근거는 적용 조건을 표시한다. 업종·고영향 입력은 사용자 판단값이며 자동 법적 판단이 아니다.
일반 업종에서도 금융 자료는 참고 자료로 표시한다. 원문 인용과 발행자·문서명·위치·해시를 함께 남긴다.

## 저장·조회·권한

- 보고서 생성 시점의 결과를 JSONB에 저장한다. 기존 보고서를 현재 카탈로그로 자동 재매핑하지 않는다.
- v1(DRAFT, 이전 상태 이름)과 규제 섹션 없는 과거 보고서도 조회 가능하다.
- `/api/v1/audits/{id}/compliance` 및 `.md`: ADMIN/AUDITOR, 완료된 감사만 허용.
- JSON 내보내기는 무결성 검증에 필요한 전체 `report_snapshot`을 포함한다.
- Finding 상세 참조는 저장된 보고서에서 읽고 기존 대상 접근권한을 적용한다.
- 프로필은 기존 `Audit.summary` JSON에서 워커 초기 상태로 전달하고 완료 요약·보고서에 보존한다.
- 무결성은 마스킹 후 실제 저장될 JSON에서 `compliance.integrity`만 제외한 canonical JSON의 SHA256이다.
- 이는 전자서명이나 진본 인증이 아니다. 저장 JSON이 변하면 별도 증적 조회를 차단한다.
- 마스킹으로 인용이 변하면 '인용 일치 확인됨' 근거에서 제외한다.

## 배포

`docker-compose.yml`은 원문 디렉터리와 매핑 설정을 API·RAG·감사/평가 워커에 읽기 전용 마운트한다.
원문 반입과 연결 생성은 호스트에서 하고, 런타임은 읽기만 한다. CLI도 빈 기본 매니페스트·설정을 초기화하며 기존 원문·사람 검토 결과를 덮어쓰지 않는다.

## 이번 작업의 검증 한계

현재 전달받은 파일 2건은 설계 문서이다. 원래 Claude 환경의 PDF, 추출 텍스트, 로컬 커밋은 전달되지 않았다.
따라서 실제 원문을 넣은 공식 문서 인용·실제 OpenAI 관련성 평가·운영 DB migration까지 실행했다고 주장하지 않는다.
합성 원문/가짜 모델 테스트와 실제 코드 회귀 검증은 별도 QA 기록에 남긴다.
