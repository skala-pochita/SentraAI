# 팀별 Capstone 평가표

**표 6-14** Capstone 평가 항목과 배점

| 항목 | 주요 내용 | 배점 |
|---|---|---|
| Biz. 아이템 | - 참신한 Biz. 아이템이면서 B2B나 B2B2C 대상<br>- 상용화되었을 때 효용성이 매우 크다고 판단되는 아이템은 팀 프로젝트에서 연속성 유지 가능 (팀 프로젝트 연속성 유지는 운영진과 교수진이 동의한 경우에만 가능) | 10 |
| 아키텍처 설계 | - Biz. 아이템을 위한 전체 AI 아키텍처 설계<br>- ISO/IEC 25010(구 ISO/IEC-9126) 품질평가를 고려한 아키텍처 설계 | 10 |
| AI Agent 설계 | - State 구조와 Graph 흐름(Node, 분기, 반복)이 업무에 맞게 설계됨<br>- Agentic AI 요소가 설계됨 | 15 |
| AI Agent 구현 | - 설계한 Graph가 코드에 반영되고, 모델이 도구를 선택해 근거와 함께 답함<br>- 상태 저장·재개와 진행 이벤트가 동작함<br>- Agentic AI 요소가 구현됨 | 15 |
| MCP 설계·구현 | - 업무에 필요한 MCP 설계·구현<br>- business-mcp, rag-mcp가 조회·변경 도구와 설명을 갖추고 Agent가 연결해 호출함<br>- 오류 처리와 예외 처리가 적절히 동작함 | 20 |
| Harness | - Harness의 주요 구성 요소가 적절하게 적용됨 | 20 |
| 인증 | - 사용자 인증 처리가 적용됨 | 10 |
| 가산점 (대기열) | - RabbitMQ, Kafka 같은 대기열 솔루션으로 작업을 큐에 넣어 처리하고, 실패한 작업을 재시도함 | 10 |
| **기본 합계** | | **100** |
| **가산점 포함** | | **110** |

---

# Harness Summary

**표 6-13** Harness 요점

| 구분 | 요점 |
|---|---|
| 정의 | Agent = Model + Harness. 모델은 다음 응답이나 호출할 도구를 제안만 하고, 실행·한도·기록·복구는 Harness가 담당한다 |
| 8개 구성 요소 | Instructions, Tools, Context, State·Memory, Agent Loop, Guardrails, Recovery, Observability |
| 구현 방식 | B는 create_agent + middleware, A는 직접 만든 StateGraph이다 |
| 승인 | 변경 도구는 destructiveHint로 표시하고, 실행 전에 멈춰 checkpointer에 상태를 저장한 뒤 사람의 결정을 기다린다 |
| 확인 | 관측(trace)과 평가로 확인하고, 요소마다 테스트를 하나씩 반영한다 |

---

# 구성 요소별 구현 기준

각 요소의 적용 위치와 최소 기준이다. 값은 권장값이고, 팀이 바꾸면 Harness 명세에 남긴다.

**표 6-3** Harness 구현 기준

| 구성 요소 | 적용 위치 | 최소 기준 |
|---|---|---|
| Instructions | prompts/*.md | 역할, 도구 사용 규칙, 금지 사항, 출력 형식. 코드에 직접 넣지 않고 버전 관리 |
| Tools | MCP Client → 도구 변환 | 도구 결과는 길이를 잘라 넘기고, 오류는 ToolMessage로 돌려준다 |
| Agent Loop | LangGraph StateGraph | 최대 8턴, 작업 120초, 같은 도구 3회까지 |
| Context | 메시지 정리·RAG 주입 | Token 예산을 정하고, 넘으면 오래된 대화를 요약한다 |
| State·Memory | checkpointer·store | thread_id 단위로 저장하고, 승인 대기 후 그대로 재개 |
| Guardrails | approval Node 또는 HITL middleware | 변경 도구는 interrupt로 멈춘다. 도구 결과의 개인정보는 마스킹 처리 |
| Recovery | 모델 호출·Agent 서버 | 429, 503은 2회 재시도, 계속 실패하면 대체 모델 또는 FAILED |
| Observability | LangSmith | trace_id 하나로 Node, 도구, Token, 지연 여부를 모니터링 |