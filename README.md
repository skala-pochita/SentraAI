# AI Security Auditor

프로젝트 ZIP을 업로드해 현재 코드의 보안 문제와 근거·권장 조치를 확인하는 읽기 전용 보안감사 서비스. 업로드한 에이전트 정의를 발견하면 권한 검사와 로컬 Docker 격리 실행·공격 검사도 연결한다.
(SKALA 팀 Capstone · LangGraph / LangChain · MCP · Redis · Kafka)

- 명세: [`SPEC.md`](SPEC.md) (원본 `AI_Security_Auditor_SPEC.pdf`)
- 진행 상황: [`checklist.md`](checklist.md)
- 이번 구현·품질 검증: [`QUALITY_IMPLEMENTATION.md`](QUALITY_IMPLEMENTATION.md)
- 설계 변경: [`DECISIONS.md`](DECISIONS.md)

> ⚠️ `targets/` 아래 샘플 코드의 모든 키·비밀번호는 **테스트용 가짜 값**이다. 실제 서비스에서 유효하지 않다.

## 실행

```bash
cp .env.example .env      # OPENAI_API_KEY, LLM_SMALL_MODEL 등 기입
make up                   # 전체 컨테이너 기동 (healthy 까지 대기)
make demo-reset           # 감사 대상 작업공간(/workspace) 초기화
make seed                 # 데모 계정·대상·지식 문서
open http://localhost:5173
```

| 서비스 | 포트 |
|---|---|
| frontend | 5173 |
| api (OpenAPI `/docs`) | 8000 |
| business-mcp / rag-mcp | 8101 / 8102 (`/mcp`) |
| target-agent | 8201 |
| kafka-ui (`docker compose --profile ui up -d kafka-ui`) | 8080 |

## 데모 계정 (운영 금지)

| 이메일 | 역할 | 비밀번호 |
|---|---|---|
| admin@demo.io | ADMIN | `.env`의 `SEED_ADMIN_PASSWORD` |
| dev@demo.io | DEVELOPER | `SEED_DEV_PASSWORD` |
| auditor@demo.io | AUDITOR | `SEED_AUDITOR_PASSWORD` |

## 개발

```bash
make test    # backend 단위·그래프 테스트 (LLM/인프라 불필요)
make lint    # ruff + mypy + vue-tsc
cd frontend && pnpm dev   # http://localhost:5173 (api 프록시)
```

## 프로젝트 업로드 및 결과 확인

감사 실행 화면에서 소스 ZIP(최대 20MiB)을 업로드하면 코드 감사 대상으로 등록된다. 감사를 시작하면 완료 전까지 진행 상태만 표시하며, 완료 후 문제점과 근거·권장 조치를 조회한다. 원본 수정·PR·병합은 수행하지 않는다. 압축 해제 한도는 100MiB·5,000개 항목이다. GitHub URL 등록·clone은 제공하지 않는다.

모델 비교 화면은 작은 모델 단일 프롬프트(A)와 작은 모델 AI Agent(B)를 내장 정답셋으로 비교한다. 품질 페이지는 제거했으며 항목별 검증 범위는 위 Markdown 문서에서 확인한다. 변경한 서비스는 컨테이너 재빌드 후 반영된다.

## 업로드 프로젝트 전체 감사

[전체 감사 사용법 및 실행 계약](UPLOADED_PROJECT_AUDIT.md)을 참고하세요. 에이전트 정의를 발견한 ZIP은 소스·Agent를 함께 등록합니다. 지원되는 Compose/Dockerfile 및 HTTP 계약이 있으면 격리 실행과 공격 검사를 수행합니다. 기존 코드 전용 업로드는 다시 업로드해야 합니다. `sandbox-runner`는 로컬 개발용 Docker 컨트롤러이며 사용자 승인에 따라 Docker 소켓을 연결합니다.

실행 설정에서 mock/live·환경변수·비밀값을 저장할 수 있습니다. 실제 모델 키는 암호화 저장소와 LLM 프록시에만 보관하며, 타깃에는 감사 전용 임시 토큰을 전달합니다. 전용 실행 서버 구성은 [배포 안내](UPLOADED_PROJECT_AUDIT.md#전용-실행-서버-배포)를 참고하세요.
