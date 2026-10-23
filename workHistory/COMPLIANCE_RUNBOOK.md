# 규제 검토 권고 실행 절차

## 준비

기준 브랜치: dev_js, 기준 커밋 bf06310. Python 3.12, uv, Node 22.13+, pnpm, Docker Desktop.
원문은 이 저장소의 Git이나 이미지에 포함하지 않는다.

```bash
cd backend
uv sync --frozen
cd ../frontend
pnpm install --frozen-lockfile
cd ..
```

PDF 반입 시 호스트에 pdftotext가 필요하다. TXT는 별도 도구 없이 가능하다.
macOS에서 pdftotext는 Poppler에 포함되며, 제공하는 백엔드 이미지에도 설치된다.

## 1. 원문 반입과 조항 정규화

등록한 공개본을 직접 다운로드해 반입하려면 다음을 실행한다.
첨부 URL/GET·POST 방식/공개본 버전은 `config/compliance/downloads.yaml`에서 관리한다.

```bash
cd backend
uv run python -m core.compliance_cli download
# 필요한 자료만: ... download FSC-AI-GL FSC-AI-SEC
cd ..
```

다운로드는 HTTPS PDF만 허용하며 파일당 30 MiB로 제한한다.
원본 PDF, 추출 텍스트, 페이지별 Markdown, 검색 단위와 각 SHA-256을 로컬에 보존한다.
원문 문서 전체와 MD를 단순 중복 색인하지 않으며, 검증된 조항/페이지 검색 단위만 색인한다.

실제 파일 경로로 바꾸어 실행한다. 출처 URL과 버전은 원문에서 확인한 값만 넣는다.

```bash
cd backend
uv run python -m core.compliance_cli import FSC-AI-GL /absolute/path/guideline.pdf
uv run python -m core.compliance_cli import FSC-AI-SEC /absolute/path/security-guide.pdf
uv run python -m core.compliance_cli import KR-AIBA-HIGH-GL /absolute/path/high-impact-guide.pdf
# 법령 원문을 따로 확보한 경우
uv run python -m core.compliance_cli import KR-AIBA /absolute/path/law.txt
# 보도자료는 보관만 하며 규제 조항으로 색인하지 않는다.
uv run python -m core.compliance_cli import FSS-AI-RMF-NOTICE /absolute/path/notice.pdf
uv run python -m core.compliance_cli validate
cd ..
```

반입 결과는 knowledge/sources 아래 로컬 파일로 남는다. 내용/해시/경계 검증 실패는 종료 코드 2이다.
점검표는 '점검 항목'이라고 명시된 영역을 분리한다. 안내서 일반 본문은 PDF 페이지와
검색 단위 번호로 위치를 표시하며 실제 조항 번호처럼 제시하지 않는다.
부록·목차·표도 원문 Markdown에 보존하며, 원본에 없는 내용을 자동 보정하지 않는다.
AI RMF 본문이 없으면 그 상태로 진행한다. 보도자료를 본문으로 대체하지 않는다.

## 2. DB 갱신과 색인

```bash
make up
cd backend
uv run python -m core.compliance_cli index
cd ..
```

make up의 API 시작 명령이 migration 0005까지 적용한다.
호스트에서 실행하는 CLI는 .env의 DATABASE_URL/REDIS_URL을 읽는다. 컨테이너 내부 호스트명 대신 호스트에서 접근할 수 있는 주소를 설정한다.
키가 없으면 기존 해시 임베딩을 사용한다. 의미 검색 품질은 실모델 임베딩과 다르다.
키/임베딩 모델을 바꾼 경우 원문을 다시 색인해야 한다.
원문이 전혀 없으면 색인을 거부하며 기존 DB를 지우지 않는다.

## 3. 초기 연결과 모델 관련성 추정

DB 없이 독립적으로 검색하려면 `REGULATION_INDEX_PATH=/absolute/path/regulations.sqlite3`를 설정하고
같은 `index`, `search`, 정확 조회 기능을 사용한다. 미설정 시 PostgreSQL 동작을 유지한다.
이 로컬 인덱스도 임베딩 제공자·모델과 원문 스냅샷을 고정하며 변경 후 재색인을 요구한다.

```bash
cd backend
export REGULATION_INDEX_PATH="$PWD/../knowledge/sources/index/regulations.sqlite3"
OPENAI_API_KEY='' uv run python -m core.compliance_cli index
OPENAI_API_KEY='' uv run python -m core.compliance_cli search '프롬프트 인젝션' --source FSC-AI-SEC
cd ..
```

```bash
make compliance-bootstrap
make compliance-link
make compliance-validate
```

bootstrap은 첨부 설계의 초기 후보 중 실제 반입된 조항에 인용이 존재하는 것만 생성한다.
모델의 재평가가 아니며 generated_by.model은 prior_design_estimate이다.
link는 OPENAI_API_KEY가 필요하다. 조항을 검색하고 get_clause와 같은 정확 조회 경로로 재검증한 뒤 모델을 호출한다.
프롬프트는 compliance_link@v1이며 원문/규칙을 untrusted 데이터로 감싼다.
추가 질의에는 probe 카테고리와 거버넌스·결합 위험 후보가 포함된다.
config/compliance/link.yaml에서 후보 수·관련성 임계값·쌍 수·토큰·비용 상한을 관리한다.
예산 초과나 잘못된 인용이 나오면 기존 연결 파일을 바꾸지 않고 종료한다. 필요하면 상한을 조정한 뒤 다시 실행한다.

## 4. 선택적인 사람 확인/기각

```bash
cd backend
uv run python -m core.compliance_cli review LINK_ID human_confirmed --reviewer reviewer-name
uv run python -m core.compliance_cli review LINK_ID rejected --reviewer reviewer-name
cd ..
```

LINK_ID는 보고서 JSON의 evidence.link_id 또는 [key, clause] canonical JSON의 SHA256이다.
확인하지 않아도 관련성은 AI 추정으로 표시된다. 기각 기록은 재생성 시 보존된다.
새 매핑은 다음 감사부터 반영된다. 과거 보고서의 내용은 바뀌지 않는다.

## 5. 감사와 결과 확인

새 감사 화면에서 업종/고영향 여부를 선택하거나 미입력·미판정으로 둔다.
완료 후 보고서의 '규제 검토 권고'에서 원문·관련성·조건을 확인한다.
ADMIN/AUDITOR는 별도 JSON/Markdown을 내려받을 수 있다. 일반 보고서는 기존 대상 권한을 따른다.

```text
GET /api/v1/audits/{audit_id}/compliance
GET /api/v1/audits/{audit_id}/compliance.md
```

원문이 없으면 '미점검/원문 미확보'가 표시되는 것이 정상이다.
MOCK와 실제 제공자 호출을 확인하지 못한 공격 검사는 실제 방어 결과로 표시하지 않는다.

JSON 내보내기 검증:

```python
import json
from core.compliance import verify_report
export = json.load(open('compliance.json'))
assert verify_report(export['report_snapshot'])
```

## CLI 로컬 설치

초기화 경로는 ~/.sentraai/compliance/sources와 ~/.sentraai/compliance/config이다.
저장소에서 원문/링크 CLI를 사용할 때 KNOWLEDGE_DIR을 ~/.sentraai/compliance,
COMPLIANCE_CONFIG_DIR을 ~/.sentraai/compliance/config로 지정하면 같은 자료를 쓴다.
COMPLIANCE_SOURCES_DIR/COMPLIANCE_CONFIG_DIR은 Compose의 호스트 마운트 위치를 바꿀 때 사용한다.
기본 자료는 npm 패키지 resources에 있으며 사용자 원문이나 실제 키는 포함하지 않는다.

## 변경 사항 검증

```bash
make test
make lint
cd cli && npm test
```

실제 Postgres 테스트는 연결할 수 없으면 skip한다. 합성 원문 테스트는 원문 PDF 없이 실행된다.
실제 모델 평가는 키와 실제 원문을 준비한 환경에서 별도로 실행해야 한다.
