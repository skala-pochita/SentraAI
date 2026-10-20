# AI Security Auditor

사내 AI 에이전트를 배포 전에 직접 공격해보고, 에이전트가 쓰는 코드까지 함께 감사해서 수정·재검증까지 닫아주는 보안감사 에이전트.
(SKALA 팀 Capstone · LangGraph / LangChain · MCP · Redis · Kafka)

- 명세: [`SPEC.md`](SPEC.md) (원본 `AI_Security_Auditor_SPEC.pdf`)
- 진행 상황: [`checklist.md`](checklist.md)
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
