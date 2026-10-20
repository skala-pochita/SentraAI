# Harness 명세

> Agent = Model + Harness. 모델은 다음 응답이나 호출할 도구를 **제안만** 하고, 실행·한도·기록·복구는 Harness 가 담당한다.
> 이 문서는 강의 표 6-13·6-3 의 8개 구성 요소와 명세 10장 H1~H8 을 1:1 로 대조하고, 권장값과 다른 값은 이유를 남긴다.

## 구현 방식

| 구분 | 이 프로젝트 |
|---|---|
| 감사 Agent (B 구성) | **직접 만든 LangGraph StateGraph** (`backend/core/graph/build.py`, 강의의 'A 방식'). ReAct 조사 노드는 그래프 안의 JSON 스텝 루프 (DECISIONS #17) |
| 비교 기준선 (A/C 구성) | Harness 없는 단일 프롬프트 (`audit_single@v1`) — H1 프롬프트와 H5 채점만 사용 |
| MCP 연결 | `langchain-mcp-adapters` `MultiServerMCPClient` (streamable-http, business-mcp·rag-mcp) |
| 승인 | 변경 도구 `destructiveHint=True` 표시 + `human_approval`/`wait_merge` 노드에서 `interrupt()` → Redis checkpointer 저장 → 사람 결정으로 재개 |

## 구성 요소 대응표

| 강의 구성 요소 | 명세 ID | 적용 위치 | 기준(강의 표 6-3) | 이 프로젝트 값 | 테스트·증거 |
|---|---|---|---|---|---|
| **Instructions** | H1 프롬프트·정책 레지스트리 | `harness/prompts/*_v{n}.yaml`, `harness/prompts/__init__.py` | prompts/*.md, 역할·도구 규칙·금지·출력 형식, 코드 밖·버전 관리 | YAML(id·version·system·user_template·output_schema). 실행 시 `prompt_id@version` 을 trace span 에 기록. **위치 형식 차이 → DECISIONS #23** | 모든 LLM 노드가 `get_prompt()` 로만 프롬프트 사용 |
| **Tools** | H3 도구 레지스트리·권한 게이트 | `core/mcp_client.py`, `harness/tools.py`, `harness/context.truncate_tool_result` | 결과 길이 자르기, 오류는 모델에 돌려주기 | 노드별 allowlist(조사 노드는 조회 도구만), 변경 도구 역할 검사, 노드당 60회. 도구 결과 **8000자** 절단 표시. 오류는 `error CODE: message` 관찰로 반환 | `test_disallowed_tool_is_refused_by_gate_and_loop_continues`, `test_truncate_tool_result`, `test_tool_annotations_mark_destructive` |
| **Agent Loop** | H8 예산 제어 (일부) | `core/graph/nodes/react_investigator.py`, `config/budget.yaml` react | 최대 8턴, 작업 120초, 같은 도구 3회 | 파일당 **LLM 8턴 · 120초 · 같은 도구 3회** (+ 스텝 6, 파일당 12000토큰). 초과 시 `loop.limit` 이벤트 후 final 강제 (DECISIONS #22) | `test_step_cap_forces_final_after_six_steps`(같은 도구 3회), `test_timeout_forces_final` |
| **Context** | H2 컨텍스트 관리자 | `harness/context.py` | 토큰 예산, 넘으면 오래된 대화 요약 | 파일 선별(정적 후보 > 위험 휴리스틱 > 나머지), `wrap_untrusted()` 격리, 관찰 누적 **6000토큰 초과 시 오래된 관찰을 `context_summarize@v1` 로 요약**(최근 2개 원문 유지), RAG 결과 주입 | `test_compact_observations_summarizes_old`, `test_wrap_untrusted_escapes_breakout` |
| **State·Memory** | (8.2·8.6) | `core/graph/state.py`, `workers/audit.py` (AsyncRedisSaver) | thread_id 단위 저장, 승인 대기 후 그대로 재개 | `thread_id=audit:{id}`, TTL 7일, interrupt 2곳. 워커가 죽으면 Kafka 재전달 → 같은 thread_id 로 checkpoint 부터 재개 | `test_graph_flow.py`(새 그래프 인스턴스 재개), Docker e2e: 승인 대기 중 `audit-worker` 재시작 후 재개 |
| **Guardrails** | H4 가드레일 | `harness/guardrails.py`, `harness/masking.py`, `human_approval`, `code_evidence_check` | 변경 도구는 interrupt, 도구 결과 개인정보 마스킹 | 변경 도구 `destructiveHint` + CRITICAL 승인 interrupt, 승인 없는 `apply_fix` 서버에서 FORBIDDEN, 입력 인젝션 탐지(AUDITOR_INJECTION_ATTEMPT), 출력 Pydantic 검증·재요청 2회, 근거(파일·라인) 재확인, **비밀값 + 이메일·전화 마스킹** (DECISIONS #24) | `test_apply_fix_without_approval_forbidden`, `test_pii_masking`, `test_auditor_injection_candidate_cannot_be_dismissed` |
| **Recovery** | H7 신뢰성 | `harness/reliability.py`, `core/kafka/consumer.py`, `workers/retry.py` | 429·503 2회 재시도, 계속 실패하면 대체 모델 또는 FAILED | 노드 안 **2회 재시도(0.5s, 2s)** → Kafka `jobs.retry`(5s/30s/120s, 3회) → `jobs.dlq` → 감사 FAILED. 회로 차단기(연속 5회 실패 시 60s). 대체 모델 없음(선택 사항) | 재시도/DLQ 통합 테스트 7개, `make fault-on N=2`(성공) / `N=12`(DLQ) (DECISIONS #28) |
| **Observability** | H6 관측성 | `harness/observability.py`, `trace_spans`, `/traces`, LangSmith | trace_id 하나로 Node·도구·Token·지연 | node/llm/tool span (토큰·비용·지연·prompt_id). **trace_id = uuid5(audit_id)** 를 Kafka envelope·span·LangSmith metadata 에 공통 사용 (DECISIONS #25) | TracesPage, LangSmith 프로젝트 `ai-security-auditor` |
| (확인) 평가 | H5 평가기 | `harness/eval/{scorer,runner,cli}.py`, `/eval-runs`, `/eval` | 관측과 평가로 확인 | A/B/C 비교 실험(명세 17장), 정답셋 대비 재현율·정밀도·근거 정확도·감사기 견고성·토큰 | `test_eval_scorer.py`, `reports/eval_*.md` |
| (예산) | H8 예산 제어 | `harness/budget.py`, `config/budget.yaml` | — | 감사당 400k 토큰·$2, probe 라운드당 적응형 ≤4, 최대 2라운드 | `budget.exceeded` 이벤트 |

## 요소마다 테스트 1개 이상 (강의 '확인')

```
cd backend && uv run pytest -q \
  tests/unit/test_masking_context.py \        # Context·Guardrails(마스킹)·Tools(절단)
  tests/integration/test_react_investigator.py \  # Agent Loop·Tools 게이트
  tests/unit/test_business_mcp.py \           # Tools 주석·경로 탈출·재시도 가능 오류
  tests/integration/test_graph_flow.py \      # State·Memory·Guardrails(승인)
  tests/unit/test_eval_scorer.py              # 평가
```
