# 로컬 실행 안내

`dev_js`의 구현 코드, 공식 게시물에서 다운로드한 PDF 4건, Markdown 4건,
정규화한 검색 단위 301개, 로컬 SQLite 인덱스, 초기 연결 25개가 포함되어 있습니다.
실제 비밀키, 기존 가상환경, node_modules는 포함하지 않습니다. GitHub push는 하지 않았습니다.

## 1. RAG 원문 검색부터 확인

`run_rag.command`를 더블클릭합니다. Python 3만 있으면 동작합니다.
검색어를 입력하면 원문 내용과 PDF 페이지, 게시처가 표시됩니다. `q`로 종료합니다.
이 간편 실행기는 키워드 검색을 제공하며 모델 답변이나 법규 판정을 생성하지 않습니다.

```bash
python3 scripts/corpus_search.py '프롬프트 인젝션' --source FSC-AI-SEC
python3 scripts/corpus_search.py --verify
```

PDF는 `knowledge/sources/raw/`, Markdown은 `knowledge/sources/markdown/`,
추출 텍스트는 `text/`, 검색 단위는 `clauses/`, SQLite 및 검증 결과는 `index/`에 있습니다.
Markdown은 자동 추출한 원문 배치를 페이지별 코드 블록으로 보존합니다.
표의 의미를 임의로 재작성하거나 누락된 도식 내용을 생성하지 않습니다.
목차·도식·서식의 추출물도 포함될 수 있으므로 검색 결과는 후보로 다룹니다.

## 2. 전체 SentraAI 앱 실행

Docker Desktop을 먼저 켜고 `run_local.command`를 더블클릭합니다.
최초 실행에는 인터넷 연결과 Docker 이미지 빌드 시간이 필요합니다.
이 스크립트는 의존성 설치, migration, 초기 계정 생성, 원문 DB 인덱싱을 실행합니다.
별도 Compose 프로젝트 `sentraai-dev-js`를 사용합니다. 같은 포트를 쓰는 서비스가 있으면 먼저 정리해야 합니다.

- 화면: http://localhost:5173
- API: http://localhost:8000/docs
- 데모 관리자: `admin@demo.io` / `Admin!demo1234`
- 종료: `stop_local.command` (DB 볼륨 보존)

`.env`가 없으면 예시 설정에서 생성하며 JWT/서비스 토큰은 새 난수로 바꿉니다.
OpenAI 키는 비어 있습니다. 키 없이 정적 검사·오프라인 검색을 실행하며,
실제 모델 호출·관련성 재평가를 하려면 `.env`의 키와 모델 ID를 설정해야 합니다.
전체 컨테이너 실행은 이 작업 환경에서 Docker 소켓 접근이 막혀 검증하지 못했습니다.
검증된 범위는 백엔드 테스트, 타입/정적 검사, CLI 테스트 및 실제 로컬 원문 검색입니다.

## 3. 개발 환경에서 하이브리드 RAG 사용

Python 3.12와 uv를 사용합니다. 간편 검색기와 달리 백엔드는 벡터+키워드 검색을 수행합니다.

```bash
cd backend
uv sync --frozen
export REGULATION_INDEX_PATH="$PWD/../knowledge/sources/index/regulations.sqlite3"
export OPENAI_API_KEY=''
uv run python -m core.compliance_cli validate
uv run python -m core.compliance_cli search '입출력 필터링' --source FSC-AI-GL
```

`REGULATION_INDEX_PATH`를 설정하지 않으면 기존 PostgreSQL/pgvector를 사용합니다.
SQLite 제공본은 `hash-v1` 임베딩으로 만들었습니다. API 키/임베딩 모델을 바꾸면 다시 `index`해야 합니다.
키가 없는 해시 방식은 오프라인 검색 확인용이며 실모델 의미 검색 품질을 보장하지 않습니다.

## 확보한 문서와 한계

| 자료 | PDF 페이지 | 규제 조항 RAG 검색 단위 |
|---|---:|---:|
| 금융분야 인공지능 가이드라인 | 91 | 99 |
| 금융분야 인공지능 보안 안내서 | 72 | 71 |
| 고영향 인공지능 사업자 책무 가이드라인 | 111 | 131 |
| AI RMF 도입 보도자료 | 7 | 0 — 참고 보관만 |

AI RMF 본문과 법률·시행령의 별도 원문은 미확보 상태입니다.
초기 연결 25개는 제공된 이전 설계에서 인용을 실제 원문에 대조한 결과이며,
실제 모델로 관련성을 새로 평가한 결과가 아닙니다. 관련성은 AI 추정으로 표시합니다.
공개본 버전, 다운로드 주소, 원본·추출물 SHA-256은 로컬 매니페스트에 보존했습니다.
현재 법령 전체의 최신성을 확인했다는 의미는 아닙니다.

## 이어서 개발하기

구현 설계와 실행 상세는 `workHistory/COMPLIANCE_MAPPING_DESIGN.md`,
`COMPLIANCE_RUNBOOK.md`, `COMPLIANCE_QA.md`에 있습니다.
원본·Markdown·인덱스와 생성 링크는 Git에서 제외되며 이 로컬 실행 묶음에만 들어 있습니다.
