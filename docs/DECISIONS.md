# 설계 변경 기록 (DECISIONS)

명세(`docs/SPEC.md`)와 다르게 구현하는 경우 여기에 먼저 기록한다.

| # | 날짜 | 항목 | 결정 | 이유 |
|---|------|------|------|------|
| 1 | 2026-10-19 | 저장소 루트 | 명세의 `ai-security-auditor/` 대신 기존 `capstoneAIAgent/` 폴더를 루트로 사용 | IDE 프로젝트가 이미 이 폴더로 열려 있음. 내부 구조는 19장과 동일 |
| 2 | 2026-10-19 | 진행 추적 | 루트에 `checklist.md` 추가 | 세션 중단 시 이어서 개발하기 위함 |
| 3 | 2026-10-19 | Node 버전 | Node 20 → Node 22 (프론트 이미지 `node:22-alpine`) | pnpm 11 이 Node ≥ 22.13 을 요구. 기능 차이 없음 |
| 4 | 2026-10-19 | 임베딩 오프라인 대체 | `OPENAI_API_KEY` 가 없으면 결정론적 해시 임베딩(1536차원 feature hashing) 사용, 하이브리드 가중치는 `config/rag.yaml` 에서 provider 별로 조정 | 키 없이도 rag-mcp·시드·테스트가 동작해야 함. 키가 있으면 명세대로 `EMBEDDING_MODEL` 사용 (임베딩 교체 시 `make seed` 재실행으로 재인덱싱) |
| 5 | 2026-10-19 | rag-mcp 도구 추가 | `list_documents` 조회 도구 추가 | 대시보드 지식 관리 화면(16장)의 문서 목록용 |
