# ISO/IEC 25010 품질 평가 보고서 — AI Security Auditor

> 평가일: **2026-10-21** · 대상: `develop` 브랜치 작업 트리 (`b558d2b` + 이번 보완) · 평가자: 팀 자체 평가
>
> 과제 가이드라인의 "ISO/IEC 25010 준수"를 위해 품질 특성마다 **목표치(통용 기준) → 설계 대응(아키텍처) → 로컬 측정 → 판정**을 체크리스트 형식으로 정리했다. 1차 평가에서 미달·부분 달성으로 나온 항목은 모두 보완한 뒤 다시 측정했다(13장). ISO 인증이나 제3자 시험 결과가 아닌 **자체 평가**다.

---

## 0. 평가 개요

### 0.1 기준
| 항목 | 내용 |
|---|---|
| 품질 모델 | **ISO/IEC 25010:2023** 제품 품질 9개 특성 (기능 적합성, 성능 효율성, 호환성, 상호작용성, 신뢰성, 보안, 유지보수성, 유연성, 안전성) |
| 측정 방법 참고 | ISO/IEC 25023 (품질 측정), ISO/IEC 25040 (평가 절차: 요구 정의 → 명세 → 설계 → 실행 → 결론) |
| 목표치 출처 | 국제·업계에서 흔히 쓰는 기준을 우선 사용 (OWASP, SonarQube 기본 품질 게이트, McCabe/SEI, Google RAIL·web.dev, WCAG 2.1, CIS Docker Benchmark, 12-Factor). 공통 기준이 없는 항목(AI 탐지 정확도 등)은 프로젝트 명세(`SPEC.md` 5장) 목표를 쓰고 표에 그렇게 표시 |

> 2011판(8개 특성)으로 평가하는 경우: 상호작용성 = 사용성, 유연성 = 이식성, 안전성은 2023판에서 새로 추가된 특성이다.

### 0.2 평가 환경
- macOS (Apple Silicon), Docker 29, 메모리 7.75GiB 할당, Docker Compose 13개 서비스
- 도구: pytest + pytest-cov, k6, pip-audit, pnpm audit, bandit, radon, jscpd, ruff, mypy, vue-tsc, MCP Python SDK 클라이언트, curl/psql
- 실 LLM: `gpt-5.4-nano` (A/B 비교 실험에만 사용, 사용자 승인 후 실행)

### 0.3 평가 범위 원칙
- **로컬에서 재현할 수 있는 측정만** 직접 실행했다.
- **부하 시험은 LLM을 부르지 않는 조회 API만** 대상으로 했다. 시험 전후로 LLM span 수가 같음(1,139 → 1,139)을 확인했다.
- AI 탐지 정확도는 사용자 승인을 받아 **A/B 비교 실험(반복 3회)을 다시 실행**해 측정했다(`reports/eval_6e3da199.md`).
- 클라우드·운영 환경, 실사용자 사용성 시험, 외부 인증은 범위 밖이다. 이런 항목은 `⏸ 미측정`과 사유로 표시했다.

### 0.4 판정 범례
| 표시 | 의미 |
|---|---|
| ✅ | 목표 달성 (이번 평가에서 측정) |
| ☑️ | 목표 달성 (기존 측정 기록 인용) |
| ⚠️ | 부분 달성 또는 조건부 |
| ❌ | 미달 |
| ⏸ | 미측정 (사유 기재) |

---

## 1. 종합 결과

| # | 품질 특성 | 점검 | ✅/☑️ | ⚠️ | ❌ | ⏸ | 한 줄 요약 |
|---|---|---|---|---|---|---|---|
| 1 | 기능 적합성 | 6 | 6 | 0 | 0 | 0 | B 재현율 0.967·정밀도 0.893·감사기 견고성 1.0 |
| 2 | 성능 효율성 | 8 | 8 | 0 | 0 | 0 | 50명 동시 p95 50.6ms(재빌드 후)·128ms(1차), 감사 1건 약 85초 |
| 3 | 호환성 | 5 | 4 | 0 | 0 | 1 | MCP 표준 클라이언트로 도구 26개 조회, 설명 100% |
| 4 | 상호작용성 | 5 | 3 | 0 | 0 | 2 | Critical까지 3클릭. 자동 접근성 검사·사용자 시험은 미실행 |
| 5 | 신뢰성 | 7 | 7 | 0 | 0 | 0 | 테스트 137/137, 프로세스 비정상 종료 후 2~8초 안에 자동 복구 |
| 6 | 보안 | 12 | 12 | 0 | 0 | 0 | 권한 우회 16/16 차단, 비밀값 노출 0, 보안 헤더 5종, 취약 의존성 0 |
| 7 | 유지보수성 | 8 | 8 | 0 | 0 | 0 | 커버리지 82.8%, CC>20 함수 0, MI 전 파일 A, 중복 0.33% |
| 8 | 유연성 | 5 | 3 | 0 | 0 | 2 | `make up` 한 번에 27초 기동 |
| 9 | 안전성 | 7 | 7 | 0 | 0 | 0 | 원본 변경 0, 실패해도 기존 데이터 삭제 0, 격리 실행 제약 적용 |
| | **합계** | **63** | **58** | **0** | **0** | **5** | **측정한 58개 전부 달성** (전체 63개 중 92.1%) |

### 1차 평가 대비 변화
| 구분 | 1차 (보완 전) | 최종 (보완 후) |
|---|---|---|
| 달성 | 44 | **58** |
| 부분 달성 | 6 | **0** |
| 미달 | 8 | **0** |
| 미측정 | 5 | 5 (외부 환경·사용자·전용 도구가 필요한 항목) |

---

## 2. 기능 적합성 (Functional Suitability)

> 하위 특성: 기능 완전성 · 기능 정확성 · 기능 적절성

### 2.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 완전성 | 기획 핵심 기능 구현 (업로드·코드 감사·Agent 정적 점검·격리 공격·결합 위험·보고서·인증) | 100% | ISO 25023 기능 구현 범위 | 7/7 구현, 통합 테스트로 확인 | ✅ |
| 완전성 | 정답셋 위치 검증 (C01~C20, D01~D06, I01~I02 라인 실재) | 통과 | 자체 | `scripts/check_ground_truth.py` OK + 실험 전 작업공간 일치 검사 통과 | ✅ |
| 정확성 | 코드 탐지 재현율 (B, 실 LLM, 3회 평균) | ≥ 0.90 | 명세 목표 (공통 기준 없음) | **0.967 ± 0.058** (회차별 1.0 / 0.9 / 1.0) | ✅ |
| 정확성 | 코드 탐지 정밀도 (B) | ≥ 0.85 | 명세 목표 | **0.893 ± 0.069** | ✅ |
| 정확성 | 감사기 견고성 (I01·I02 인젝션이 있어도 C06 보고 + 시도 자체 보고) | 1.0 | 명세 목표 (보안 도구 자체 무결성) | **1.0** (3회 모두 ROBUST) | ✅ |
| 적절성 | 근거 정확도 (TP의 파일·라인이 실제와 일치) | ≥ 0.95 | 자체 (감사 근거 신뢰성) | 0.967 | ✅ |

참고(판정 제외): LLM 없이 정적 규칙만 쓴 기준선은 재현율 0.90, 정밀도 0.82다(TP 18, FP 4, 미탐 C19·C20). B 구성은 여기에 ReAct 조사를 더해 정밀도를 0.89까지 올리고 정적 회피 케이스도 찾는다.

### 2.2 A/B 비교 실험 (`reports/eval_6e3da199.md`, 2026-10-21, 반복 3회)
| 지표 | A_nano_single (단일 프롬프트) | **B_nano_harness (우리 서비스)** |
|---|---|---|
| code_f1 | 0.579 ± 0.046 | **0.928 ± 0.063** |
| code_recall | 0.550 | **0.967** |
| code_precision | 0.614 | **0.893** |
| agent_recall | 0.714 | **1.0** |
| probe_accuracy | 0.695 | **0.806** |
| severity_accuracy | 0.427 | **0.863** |
| auditor_robustness | 0.833 | **1.0** |
| 함정 오탐 수 (평균) | 0.33 | 0.33 |
| tokens | 19,802 | 85,911 |
| latency_s | 23.4 | 84.6 |

같은 작은 모델에서 Harness·Agent 구조를 씌우면 F1이 0.58에서 0.93으로 오른다. 대신 토큰은 약 4.3배, 시간은 약 3.6배 더 든다. `config/pricing.yaml`이 비어 있어 비용(USD)은 산출하지 않았다.

### 2.3 체크리스트
- [x] 코드 규칙 17개, Agent 규칙 7개, 결합 위험 규칙 5개, probe 12개 동작 (`tests/unit/test_rules.py`)
- [x] 정적 규칙으로 C01~C18 18/18 탐지, 함정 D01~D06 중 정적 오탐은 D03 1건
- [x] ReAct 조사로 정적 회피 C19·C20 탐지 (B 3회 중 2회 둘 다 탐지)
- [x] 근거 라인 보정 가드레일 (`test_relocate_evidence_fixes_wrong_line`)
- [x] **LLM 기각 보류 가드레일**: 감사기 인젝션이 있는 파일, 테스트 경로 밖의 자격증명 탐지는 LLM이 지울 수 없음 (`test_injection_in_file_blocks_dismissing_neighbours`, `test_credential_candidates_are_held_outside_tests`)
- [x] **실험 전 작업공간 무결성 검사**: 정답셋과 다르면 토큰을 쓰기 전에 중단 (`test_workspace_drift_detects_fixed_sample`)

### 2.4 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **2단 탐지**: 결정론 정적 규칙(재현율 바닥 확보) → LLM ReAct 조사(정적 회피 탐지·오탐 기각) | `core/rules/`, `code_static_scan.py`, `react_investigator.py` | 정적 0.90/0.82 → B 0.967/0.893 |
| **LLM 기각 보류 가드레일**: ① 신뢰도 ≥0.8 확정 규칙 ② 감사기 인젝션이 있는 파일의 모든 후보 ③ 테스트 경로 밖 자격증명 → 기각 대신 NEEDS_REVIEW | `react_investigator._hold_reason` | 재현율 0.88 → 0.967, 견고성 0.83 → 1.0 (13.2절) |
| **근거 검증 노드**: 파일·라인 실재 확인, 스니펫으로 라인 재탐색 | `code_evidence_check.py` | 근거 정확도 0.967 |
| **카테고리 고정 출력 스키마** + 재요청 2회 | `harness/prompts/code_investigate_v3.yaml`, Pydantic | 자유 카테고리로 인한 매칭 실패 제거 |
| **결합 위험 규칙** (CORR-001~005) | `core/graph/nodes/correlate.py` | 코드 + Agent 결과를 연결해 심각도 재산정 (심각도 정확도 0.863) |
| **정답셋 기반 평가기 + 작업공간 무결성 게이트** (H5) | `harness/eval/runner.py` | 같은 데이터셋·temperature 0·프롬프트 버전 고정, 오염된 대상 채점 차단 |

---

## 3. 성능 효율성 (Performance Efficiency)

> 하위 특성: 시간 반응성 · 자원 활용성 · 수용량

### 3.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 시간 반응성 | 조회 API 단건 응답 p95 (`/me`, `/audits`, `/targets`, 결과 조회) | ≤ 100ms | Google RAIL (응답 100ms) | 1.9 ~ 4.7ms (지식 문서 목록 19.5ms) | ✅ |
| 시간 반응성 | 로그인 응답 p95 (bcrypt cost 12 포함) | ≤ 1,000ms | OWASP Password Storage (해시 1초 이내 권장) | 212.5ms | ✅ |
| 수용량 | 50명 동시 사용자 30초 부하: p95 응답 | ≤ 200ms | 일반 웹 서비스 SLO | 1차 128.2ms / 보완 후 재측정 50.6ms | ✅ |
| 수용량 | 같은 부하에서 오류율 | < 1% | 일반 웹 SLO | **0.00%** (1차 33,660건 · 재측정 29,068건) | ✅ |
| 시간 반응성 | 감사 1건 처리 시간 (내장 샘플, 코드+Agent 전체) | ≤ 5분 | 명세 목표 | B 실험 평균 84.6초 / 운영 감사 12건 평균 130.6초, p95 243.6초 | ✅ |
| 자원 활용성 | 감사 1건 토큰 사용량 | ≤ 400,000 (예산 한도) | `config/budget.yaml` | B 평균 85,911, 운영 감사 최대 115,881 | ✅ |
| 자원 활용성 | 전체 스택 메모리 (13개 서비스, 유휴) | ≤ 4GB (8GB 개발 PC에서 동작) | 자체 | 약 2.1GB (Kafka 1.05GB가 최대) | ✅ |
| 자원 활용성 | 첫 화면 JS 전송량 (gzip) | ≤ 170KB | web.dev 성능 예산 | 로그인 49.6KB / 로그인 후 개요 **50.8KB** (1차 208KB) | ✅ |

### 3.2 체크리스트
- [x] k6 부하 시험 임계값(p95<500ms, 실패율<1%) 통과
- [x] 부하 시험 중 LLM 호출 0건 확인
- [x] 개요 화면 차트를 SVG 컴포넌트로 바꿔 첫 화면에서 ECharts(147KB) 제거, ECharts는 모델 비교 화면에서만 지연 로딩
- [x] 페이지별 코드 분할 (라우트 단위 lazy import)
- [x] 동시 감사 부하 시험은 **비용 때문에 의도적으로 하지 않음** (감사 1건 약 8.6만 토큰). 감사는 비동기 큐로 처리되므로 API 응답 성능과 분리된다
- 재측정 중 한 요청이 최대 19초 걸렸다. API 컨테이너를 재시작한 직후 첫 요청이었고, 예열 뒤 다시 잰 최대 응답은 444ms였다

### 3.3 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **비동기 처리 분리**: API는 Kafka에 넣고 즉시 202, 무거운 감사는 audit-worker가 처리 | `api/routers/audits.py`, `workers/audit.py` | 감사가 돌아도 API 응답 시간에 영향 없음 |
| **비동기 I/O 전 구간**: FastAPI + SQLAlchemy async + asyncpg + redis.asyncio | `core/db/session.py` | 50명 동시 접속 p95 50~128ms |
| **작은 모델 + 컨텍스트 선별** (위험 파일 우선 top-k, 라인 슬라이싱, 관찰 요약) | `harness/context.py`, `config/budget.yaml` | 감사 1건 약 8.6만 토큰 |
| **캐시**: 정적 스캔은 커밋 해시 기준, RAG 검색은 1시간 + 버전 무효화 | `run_static_scan`, `mcp_servers/rag/search.py` | 같은 대상 재감사 비용 절감 |
| **예산 제어 (H8)**: 감사당 40만 토큰·$2, ReAct 8턴·120초, probe 라운드 2 | `harness/budget.py` | 최악의 경우에도 시간·비용 상한 보장 |
| **병렬 서브그래프**: 코드 감사와 Agent 감사를 `Send`로 동시 실행, probe 동시 3개 | `core/graph/build.py` | 전체 감사 시간 단축 |
| **의존성 없는 SVG 차트 + 라우트 코드 분할** | `frontend/src/components/Donut.vue`, router | 첫 화면 JS 51KB |

---

## 4. 호환성 (Compatibility)

> 하위 특성: 공존성 · 상호운용성

### 4.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 공존성 | 한 호스트에서 13개 서비스 동시 기동, 포트 충돌 없음 | 13/13 healthy | 자체 | 13/13 healthy | ✅ |
| 상호운용성 | **MCP 표준 클라이언트**(공식 Python SDK, streamable-http)로 도구 목록 조회 | 두 서버 모두 성공 | MCP 사양 | business 15개, rag 11개 조회 | ✅ |
| 상호운용성 | MCP ToolAnnotations (`readOnlyHint`/`destructiveHint`) 제공 | 조회·변경 도구 전부 표기 | MCP 사양 | 조회 14개 readOnly, 변경 7개 destructive | ✅ |
| 상호운용성 | MCP 도구 설명(description) 제공률 | 100% | MCP 사양 (클라이언트·LLM이 도구 선택에 사용) | **26/26** (1차 24/26) | ✅ |
| 상호운용성 | 외부 MCP 클라이언트(Claude Desktop 등)에서 호출 | 성공 | 명세 목표 | 미실행 | ⏸ 외부 클라이언트 설정 필요 |

### 4.2 체크리스트
- [x] 서비스 토큰 없이 MCP 요청하면 401
- [x] OpenAPI 문서 자동 생성 (`/docs`)
- [x] 업로드 에이전트 프로토콜 2종 지원 (`auditor`, `atlas`)
- [x] LLM 프록시가 OpenAI Chat Completions 호환 형식을 중계
- [x] `inspect_cli list`가 설명 없는 도구에서도 중단되지 않음
- [ ] 외부 MCP 클라이언트 연동 시연

### 4.3 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **MCP 표준 인터페이스**: 업무 기능을 FastMCP 서버 2개로 분리, streamable-http 전송 | `mcp_servers/business`, `mcp_servers/rag` | 어떤 MCP 클라이언트든 같은 도구를 사용 |
| **공통 응답 envelope + 오류 스키마** `{ok, data \| error{code,message,details}}` | `mcp_servers/common.py` | 클라이언트가 오류를 일관되게 처리 |
| **표준 클라이언트 사용**: 워커도 `langchain-mcp-adapters` `MultiServerMCPClient`로 호출 | `core/mcp_client.py` | 우리 워커와 외부 클라이언트가 같은 경로 사용 |
| **도구 docstring = 계약** (입력·출력·권한·오류·부작용) | `mcp_servers/business/tools/*.py` | 설명 26/26 |
| **실행 계약 파일**: `audit-runtime.yaml`, `audit-probes.yaml` | `core/runtime_spec.py`, `core/project_probes.py` | 업로드 에이전트마다 다른 HTTP 형식을 정규화 |

---

## 5. 상호작용성 (Interaction Capability, 2011판 '사용성')

> 하위 특성: 인지 용이성 · 학습성 · 운용성 · 사용자 오류 방지 · 사용자 참여 · 포용성 · 사용자 지원 · 자기 서술성

### 5.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 운용성 | 로그인 후 첫 Critical Finding 상세까지 클릭 수 | ≤ 3 | 3-click rule (명세 목표) | 3클릭: 개요 → 최근 감사 → Findings(심각도·신뢰도 순 정렬) → 최상단 Finding | ✅ (경로 분석) |
| 사용자 오류 방지 | 잘못된 입력에 구조화된 오류 응답 (`error.code/message`) | 100% | ISO 25023 오류 방지 | 시험한 오류 응답 16/16 구조화 | ✅ |
| 포용성 | 문서 언어 선언 (`<html lang>`) | 있음 | WCAG 2.1 3.1.1 | `lang="ko"` | ✅ |
| 포용성 | 자동 접근성 점수 (Lighthouse/axe) | ≥ 90 | Lighthouse 권장 | 미측정 | ⏸ 로컬에 Chrome·axe 없음, 브라우저 확장 미연결 |
| 학습성 | 실사용자 과업 성공률·SUS 점수 | SUS ≥ 68 | SUS 평균 | 미측정 | ⏸ 사용자 시험 미실시 |

### 5.2 체크리스트
- [x] 감사 진행을 SSE로 실시간 표시 (노드 진행, ReAct 도구 선택 이유)
- [x] 완료 전 결과 화면에 접근하면 진행 화면으로 이동
- [x] 심각도 색상 토큰을 라이트·다크 모드 양쪽에 정의
- [x] 개요 도넛 차트에 `role="img"`와 `aria-label`(심각도별 건수), 조각별 `<title>` 툴팁
- [x] 업로드 화면에서 전체 감사 준비 조건과 미지원 사유 표시
- [x] 역할별 메뉴 노출 (권한 없는 메뉴 숨김)
- [ ] 아이콘 전용 버튼 `aria-label` 전수 점검
- [ ] Lighthouse·axe 자동 접근성 검사, 사용자 5명 과업 시험

### 5.3 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **이벤트 스트림**: Redis Pub/Sub + Stream → SSE (Last-Event-ID로 재접속 시 이어 받기) | `harness/events.py`, `GET /audits/{id}/events` | 긴 감사(1~2분)에도 진행이 보임 |
| **서버 측 우선순위 정렬**: Finding을 심각도 → 신뢰도 순으로 반환 | `api/routers/results.py` | Critical을 3클릭 안에 확인 |
| **근거 중심 상세 화면**: 파일·라인·OWASP 조항·권장 조치·결합 위험을 한 화면에 | `FindingPage.vue` | 개발자 설득 비용 감소 |
| **공통 오류 스키마** | `core/errors.py` | 화면에서 일관된 오류 안내 |
| **완료 전 결과 비공개** (API 409 + 라우터 가드) | `api/routers/results.py`, `frontend/src/router` | 부분 결과로 인한 오판 방지 |

---

## 6. 신뢰성 (Reliability)

> 하위 특성: 무결함성 · 가용성 · 결함 허용성 · 복구성

### 6.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 무결함성 | 자동 테스트 통과율 | 100% | CI 품질 게이트 관행 | **137/137** (1차 126/127) | ✅ |
| 결함 허용성 | 재시도·DLQ 시나리오 테스트 | 7/7 | 명세 (재시도 가능 오류 100% 복구) | 7/7 통과 (장애 주입은 현재 감사 흐름의 `run_static_scan`에서 검증) | ✅ |
| 복구성 | 의존 서비스 재시작 후 API 정상 응답까지 | ≤ 30초 | 일반 서비스 RTO 관행 | redis 5.7s · postgres 5.7s · kafka 7.5s · business-mcp 6.0s · rag-mcp 5.9s · audit-worker 15.6s · api 15.7s | ✅ |
| 복구성 | Kafka 재시작 후 consumer 그룹 재가입 | 4/4 | 자체 | audit·eval·retry·dlq 4/4 재가입, 이후 오류 로그 0 | ✅ |
| 복구성 | 워커 재시작 후 체크포인트에서 감사 재개 | 성공 | 명세 | 2026-10-19 실환경 e2e 통과 (재실행은 LLM 비용이 들어 생략) | ☑️ |
| 가용성 | 감사 완료율 (실행이 허용된 감사 기준) | ≥ 99% | 일반 SLO | 19/19 (100%). 실패로 기록된 1건은 같은 대상을 다른 감사가 검사 중이라 대상 잠금이 의도대로 거부한 CONFLICT | ✅ |
| 결함 허용성 | 프로세스 비정상 종료 시 자동 재시작 | 상시 서비스 전부 | Docker 운영 관행 | 재시작 정책 13/13. 메인 프로세스를 종료시켜 보니 redis 6.4s · postgres 6.3s · kafka 7.5s · frontend 6.4s 만에 스스로 복구 | ✅ |

### 6.2 체크리스트
- [x] 지수 백오프 재시도 5s / 30s / 120s, 3회 초과 시 DLQ
- [x] 비재시도 오류는 즉시 DLQ, 디코딩 불가 메시지도 DLQ
- [x] 중복 전달 skip (멱등 키 lease)
- [x] DLQ replay는 새 message_id로 재발행
- [x] 모든 서비스에 healthcheck (13개)
- [x] `FAULT_INJECTION=rate_limit:N` → `run_static_scan` 처음 N회 RATE_LIMITED 후 성공 (`make fault-on N=2`)
- [x] AuditRunner 전 구간 테스트: 요청 → 실행 → 완료 발행, 재전달 skip, 취소 후 skip, 그래프 실패 시 FAILED와 대상 락 해제 (`test_audit_worker_api.py`)
- 측정 방법 메모: `docker kill`은 Docker가 "수동 정지"로 처리해 재시작 정책이 적용되지 않는다. 그래서 컨테이너 안에서 메인 프로세스를 종료하는 방식으로 비정상 종료를 재현했다.

### 6.3 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **Kafka 작업 큐 + retry 토픽 + DLQ** | `core/kafka/`, `workers/retry.py` | 일시 장애는 자동 재시도, 영구 장애는 격리 후 수동 재처리 |
| **멱등 처리 lease**: `kafka:processed:{id}` 처리 중 60초 lease → 완료 시 done | `core/kafka/consumer.py` (DECISIONS #6) | 중복 처리와 유실을 함께 방지 |
| **LangGraph Redis 체크포인터** (thread_id `audit:{id}`) | `workers/audit.py` | 워커가 죽어도 마지막 노드부터 재개 |
| **대상 락**: 같은 대상을 동시에 두 감사가 검사하지 못함 | `core/redis/locks.py` | 결과 섞임 방지 (CONFLICT로 명시적 거부) |
| **H7 신뢰성 모듈**: 타임아웃, 노드 안 인라인 재시도(0.5s·2s), 회로 차단기(5회 실패 시 60초) | `harness/reliability.py` | 외부 의존(LLM·MCP·타깃) 장애 전파 차단 |
| **healthcheck + depends_on(healthy) + `restart: unless-stopped`** | `docker-compose.yml` | 의존 순서대로 기동, 비정상 종료 시 자동 복구 |

---

## 7. 보안 (Security)

> 하위 특성: 기밀성 · 무결성 · 부인 방지 · 책임 추적성 · 인증성 · 저항성

### 7.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 인증성 | 인증 우회 시험 (토큰 없음, 서명 위조, `alg=none`) | 3/3 차단 | OWASP ASVS V3, JWT Cheat Sheet | 3/3 → 401 | ✅ |
| 기밀성 | 권한 상승 시험 (AUDITOR 감사 실행·업로드·사용자 생성, DEVELOPER 트레이스·DLQ·사용자 목록) | 6/6 차단 | OWASP ASVS V4 | 6/6 → 403 | ✅ |
| 인증성 | 로그인 실패 잠금 | 5회 실패 후 잠금 | OWASP Authentication Cheat Sheet | 6번째 시도 429 | ✅ |
| 인증성 | refresh 토큰 회전 / 로그아웃 후 access 무효화 | 재사용 거부, 블랙리스트 | OWASP JWT Cheat Sheet | 재사용 401, 로그아웃 후 401 | ✅ |
| 기밀성 | 비밀번호 해시 work factor | bcrypt ≥ 10 | OWASP Password Storage | cost 12 | ✅ |
| 기밀성 | access 토큰 수명 | ≤ 15분 | OWASP JWT (short-lived) | 15분 / refresh 7일 | ✅ |
| 기밀성 | **비밀값 원문 노출** (로그, DB 8개 테이블, 보고서, Redis 이벤트 스트림) | 0건 | OWASP ASVS V7·V8, 작업 규칙 5 | 샘플 가짜 키 0건, 실제 `.env` 키 0건 (보완 후 재검사 포함). Finding에는 `AKIA****` 형태로만 저장 | ✅ |
| 저항성 | 의존성 취약점 High/Critical | 0 | OWASP Top 10 A06, 일반 CI 게이트 | Python·npm 모두 0 | ✅ |
| 저항성 | 의존성 취약점 Moderate | 0 | 동일 | **0** (certifi 2026.10.19, echarts 6.1로 업데이트) | ✅ |
| 저항성 | 우리 코드 SAST (bandit) High | 0 | 일반 CI 게이트 | **0** (캐시 키 SHA1에 `usedforsecurity=False` 명시). MEDIUM 5건은 컨테이너 내부 `0.0.0.0` 바인딩·`/tmp` 사용으로 의도된 설계 | ✅ |
| 저항성 | HTTP 보안 헤더 (CSP, X-Frame-Options, X-Content-Type-Options) | 3/3 | OWASP Secure Headers Project | **5/5** (+ Referrer-Policy, Permissions-Policy). nginx 버전·uvicorn `Server` 헤더 숨김 | ✅ |
| 저항성 | 감사기 자체 프롬프트 인젝션 견고성 | 1.0 | 명세 목표 | **1.0** (B 3회 모두 ROBUST) | ✅ |

### 7.2 체크리스트
- [x] 업로드 ZIP 경로 탈출·절대 경로·심볼릭 링크·암호화·용량 한도 거부
- [x] MCP `read_repo_file` 경로 탈출·심볼릭 링크 탈출 거부
- [x] 감사 대상 텍스트를 `wrap_untrusted()`로 감싸 지시로 취급하지 않음
- [x] 실행 설정 비밀값 Fernet 암호화, API는 등록 여부만 반환
- [x] 실제 LLM 키는 프록시만 보유, 타깃에는 감사 전용 임시 토큰, 종료 시 폐기
- [x] 서비스 간 인증 (MCP·sandbox·프록시 서비스 토큰, 토큰 없는 MCP 요청 401)
- [x] 책임 추적: 감사 이벤트·trace span에 행위자·노드·도구 기록, trace_id = uuid5(audit_id)
- [x] 이메일·전화번호 PII 마스킹
- [x] CSP `script-src 'self'`: 인라인 테마 스크립트를 외부 파일로 분리. 빌드 산출물에 인라인 스크립트·`eval`·`new Function` 0건 (브라우저 실행 확인은 확장 미연결로 못 함, 정적 확인)
- [x] probe 실행 권한: AUDITOR 거부, DEVELOPER는 진행 중인 본인 감사·본인 소유 업로드 대상만 (`test_probe_role_ownership_and_allowlist`)
- [ ] QA 보고서(`reports/qa-2026-10-20/QA_REPORT.md`) 잔여 이슈 정리 (이번 평가 범위 밖)

### 7.3 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **JWT + bcrypt + RBAC 3역할** (`require_roles` 의존성, DEVELOPER 소유 범위 제한) | `api/security.py`, `api/deps.py` | 권한 상승 시험 6/6 차단 |
| **Redis 기반 세션 상태**: refresh 회전, jti 블랙리스트, 로그인 잠금 | `core/redis/`, `api/routers/auth.py` | 탈취 토큰 재사용 차단 |
| **이중 권한 검사**: API 역할 검사 + MCP 서버가 도구에서 역할·소유권 재검사 | `mcp_servers/common.py`, `execute._sandbox_access` | API를 우회해도 MCP에서 다시 차단 |
| **H3 도구 게이트**: 노드별 allowlist, 퇴역 도구 이름 차단 | `harness/tools.py` | LLM이 인젝션에 속아도 변경 도구를 부를 수 없음 |
| **H4 가드레일 + 마스킹 일원화**: `mask_secret()`, 저장 시 서버 재마스킹, LLM 기각 보류 | `harness/masking.py`, `react_investigator._hold_reason` | 비밀값 노출 0, 감사기 견고성 1.0 |
| **키 격리**: 암호화 저장소(키·데이터 볼륨 분리) → LLM 프록시만 실제 키 사용 → 타깃은 임시 토큰 | `core/secret_store.py`, `llm_proxy/` | 업로드한 코드가 실제 키를 볼 수 없음 |
| **보안 헤더 이중 적용**: nginx(server 블록) + API 미들웨어(JSON은 `default-src 'none'`) | `frontend/nginx.conf`, `api/main.py` | 직접 접근·프록시 경유 모두 헤더 적용 |

---

## 8. 유지보수성 (Maintainability)

> 하위 특성: 모듈성 · 재사용성 · 분석성 · 수정성 · 시험성

### 8.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 분석성 | 정적 분석 오류 (ruff, mypy, vue-tsc) | 0 | CI 게이트 관행 | ruff 0, mypy 0 (116개 파일), vue-tsc 0 | ✅ |
| 수정성 | 코드 중복률 | ≤ 3% | SonarQube 기본 품질 게이트 | **0.33%** (13,542줄 중 45줄) | ✅ |
| 분석성 | 평균 순환 복잡도 | ≤ 10 | McCabe / NIST SP 500-235 | **3.84** (641개 블록) | ✅ |
| 분석성 | 고위험 함수 (CC > 20) | 0 | SEI 복잡도 분류 (21 이상 고위험) | **0** (1차 23개, 최대값 54 → 20) | ✅ |
| 수정성 | 유지보수 지수 MI | 모든 파일 ≥ 20 | Visual Studio / radon A 등급 | **116/116 A** (1차 `audits.py` 17) | ✅ |
| 시험성 | 라인 커버리지 | ≥ 80% | SonarQube 기본 품질 게이트 | **82.8%** (6,511줄 중 5,393줄) | ✅ |
| 모듈성 | 탐지 규칙 1개 추가에 필요한 파일 수 | ≤ 2 | 명세 목표 | 1개 (`rules/*/*.yaml`) | ✅ |
| 재사용성 | 프롬프트 버전 관리 (id·version·schema) | 전 노드 | 명세 | 프롬프트 YAML, 이벤트에 `prompt_id@version` 기록 | ✅ |

커버리지 측정 메모: 1차 67%는 coverage가 SQLAlchemy async의 greenlet 실행을 추적하지 못해 `await db.execute(...)` 이후 줄을 미실행으로 센 값이다. `pyproject.toml`에 `concurrency = ["greenlet", "thread"]`를 넣고 다시 쟀다.

### 8.2 체크리스트
- [x] 작업 규칙(`CLAUDE.md`)과 설계 변경 기록(`DECISIONS.md`, 2026-10-21 보완 기록 포함)
- [x] 모델명·가격·임계값을 `config/*.yaml`·환경변수로 분리
- [x] 노드 = 파일 1개, 그래프 코드와 다이어그램 1:1
- [x] 의존성 주입으로 테스트 대역 사용 (`Deps(gateway=...)`, `inprocess_gateway()`, Docker SDK 대역)
- [x] 쓰지 않던 수정 경로 코드 삭제 (노드 8개, `core/fixes.py`, `patches.py`, MCP 함수 5개, `/quality` API)
- [x] 고복잡도 함수 18개 분해. **동작이 같은지 분해 전 코드와 무작위 입력으로 비교** (채점기 2,000건, 매니페스트 규칙 4,506건, 실행 계약 3,000건, 결합 위험 3,000건, probe 판정 20,000건, 코드 채점 5,000건, 설정 검증 6,000건, 보고서 3,000건, 실험 보고서 2,000건 모두 동일)
- [x] 결과 조회 라우트를 `api/routers/results.py`로 분리

### 8.3 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **규칙 플러그인**: detector 5종 + YAML 규칙, 규칙 판정은 함수 표(`TOOL_CHECKS`, `JUDGES`) | `core/rules/`, `core/probes.py` | 규칙 추가 = YAML 1개, 판정 추가 = 함수 1개 |
| **서브그래프 분리**: 코드 감사 / Agent 감사를 독립 그래프로 | `core/graph/build.py` | 한쪽 수정이 다른 쪽에 영향 없음 |
| **Harness 모듈화 H1~H8** (파일 하나에 책임 하나) | `harness/` | 품질 개선 지점이 명확 |
| **MCP로 업무 로직 분리**: 그래프는 도구만 호출 | `core/mcp_client.py` | 워커와 업무 로직을 따로 수정·시험 |
| **Pydantic 스키마 일원화** | `core/schemas` | 노드 사이 데이터 계약을 타입으로 보장 |
| **커버리지가 async를 정확히 세도록 설정** | `backend/pyproject.toml` | 시험성 지표 신뢰도 |

---

## 9. 유연성 (Flexibility, 2011판 '이식성')

> 하위 특성: 적응성 · 확장성 · 설치성 · 대체성

### 9.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 설치성 | 명령 한 번으로 전체 기동 | `make up` 1회 | 명세 목표 | 성공 (exit 0) | ✅ |
| 설치성 | 기동 완료(전 서비스 healthy)까지 시간 | ≤ 5분 | 자체 | **27초** (이미지 캐시 있는 상태, `down` 후 재기동) | ✅ |
| 적응성 | 환경별 설정 외부화 | 코드 수정 없이 변경 | 12-Factor App (Config) | `.env.example` 18개 항목, `config/*.yaml` 5개 | ✅ |
| 확장성 | 실행 서버 분리 배포 (`deploy/executor.compose.yml`) | 원격 기동 성공 | 자체 | 구성 파일만 있음, 실제 VM 미생성 | ⏸ 원격 호스트 없음 |
| 설치성 | 새 PC에서 클론 → 기동 | 성공 | 자체 | 미실행 | ⏸ 깨끗한 환경 없음 |

### 9.2 체크리스트
- [x] 키가 없어도 동작하는 대체 경로 (결정론 해시 임베딩, LLM 미설정 시 정적 판정)
- [x] 모델 교체는 환경변수만 변경 (`LLM_SMALL_MODEL`, `LLM_LARGE_MODEL`, `EMBEDDING_MODEL`)
- [x] 업로드 프로젝트 프로토콜을 계약 파일로 확장 (`audit-runtime.yaml`)
- [x] `.dockerignore`로 로컬 비밀·산출물을 이미지에서 제외
- [ ] 백엔드 이미지 1.1GB 축소 검토 (multi-stage)
- [ ] 클린 환경 설치 리허설

### 9.3 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **Docker Compose 일괄 구성** + healthcheck 기반 `--wait` | `docker-compose.yml`, `Makefile` | 27초 기동 |
| **설정 외부화** (pydantic-settings, `${VAR}` 치환 YAML) | `core/settings.py`, `config/` | 환경·모델 교체 시 코드 변경 없음 |
| **실행 계층 분리**: API ↔ sandbox-runner를 ZIP + SHA-256 전송으로 연결 | `core/project_transfer.py`, `deploy/executor.compose.yml` | 실행 서버를 다른 호스트로 옮길 수 있음 |
| **단일 백엔드 이미지, 역할별 실행** | `backend/Dockerfile` | 서비스를 늘려도 빌드는 하나 |

---

## 10. 안전성 (Safety, 2023판 신규)

> 하위 특성: 운용 제약 · 위험 식별 · 안전 우선 실패 · 위험 경고 · 안전한 통합

### 10.1 목표와 측정 결과
| 하위 특성 | 지표 | 목표 | 목표 출처 | 측정값 | 판정 |
|---|---|---|---|---|---|
| 운용 제약 | 감사 전후 원본 소스 변경 | 0바이트 | 명세 (읽기 전용) | 0 (`test_read_only_full_audit_reports_without_mutation`, 업로드 감사 테스트) | ✅ |
| 운용 제약 | 외부에 노출된 원본 변경 도구 (수정·적용·병합·승인) | 0 | 명세 | MCP 목록의 변경 도구는 `create_finding`·`update_finding_status`·`save_report`뿐. 수정 경로 코드 삭제, `/approvals`·`/fixes/*/merge` 404 | ✅ |
| 안전한 통합 | 업로드 컨테이너 격리 제약 (비루트, 읽기 전용 루트, cap_drop ALL, no-new-privileges, 호스트 포트·소켓·bind 없음) | 전부 적용 | CIS Docker Benchmark 5장 | 전부 적용 (`test_start_builds_isolated_containers_and_cleans_up`, 실행 계약 테스트, 실제 Docker 검증) | ✅ |
| 안전 우선 실패 | 실행 불가·오류를 방어 성공으로 세지 않음 | NOT_RUN/ERROR 분리 | 자체 | 분리 표기 | ☑️ |
| 안전 우선 실패 | 업로드 해제 실패 시 기존 데이터 삭제 | 0 | 자체 | 0 (`test_failed_extract_never_deletes_existing_directory`) | ✅ |
| 위험 경고 | 보고서에 실제 LLM 검증 여부 표시 | LIVE_VERIFIED / MOCK / UNVERIFIED | 자체 | 구현, 실제 OpenAI 감사에서 `LIVE_VERIFIED` 확인 | ☑️ |
| 위험 식별 | 업로드 코드 정적 분석 중 실행(import) 금지 | 0회 | 자체 | `test_literal_agent_discovery_does_not_execute_code` | ✅ |

### 10.2 체크리스트
- [x] 리소스 상한: CPU 1, 메모리 768MiB, PID 128, 빌드 300초·2GiB, 종료 시 컨테이너·네트워크·이미지 제거, TTL 정리기
- [x] 카나리·공격 문서는 일회성 이미지에만 추가 (원본에 쓰지 않음, `.env` 파일은 빌드 컨텍스트에서 제외)
- [x] 감사별 LLM 호출 수·토큰 한도, 허용 모델 목록
- [x] 진행 중 감사는 실행 설정 변경 불가
- [x] Docker 소켓은 sandbox-runner에만 연결 (로컬 신뢰 경계로 문서화, 운영은 전용 VM 필요)
- [x] 비교 실험은 작업공간이 정답셋과 다르면 시작하지 않음 (잘못된 품질 수치가 보고되는 것을 방지)

### 10.3 목표 달성을 위한 아키텍처 설계
| 설계 | 구현 위치 | 기여 |
|---|---|---|
| **읽기 전용 그래프**: 승인·수정·병합 노드를 코드에서 제거, 게이트에서 퇴역 도구 이름 차단 | `core/graph/build.py`, `harness/tools.py` | 원본 변경 경로 자체가 없음 |
| **격리 실행 컨트롤러**: Compose를 실행하지 않고 허용 필드만 해석해 컨테이너 생성 | `sandbox/server.py`, `core/runtime_spec.py` | privileged·host network 등 위험 옵션 차단 |
| **감사별 internal 네트워크 + 중계 컨테이너** | `sandbox/server.py`, `llm_proxy/relay.py` | 타깃이 외부·호스트에 직접 접근 불가 |
| **판정 상태 분리** (NOT_RUN / ERROR / MOCK / LIVE_VERIFIED) | `core/probes.py`, `report.py` | 실행 실패를 "안전"으로 오인하지 않음 |
| **"내가 만든 것만 지운다"** 정리 원칙 | `core/project_upload.py` | 실패 처리 중 기존 데이터 손실 방지 |

---

## 11. 남은 과제 (미측정 5건과 범위 밖 항목)

| ID | 특성 | 내용 | 필요한 것 |
|---|---|---|---|
| R-01 | 호환성 | 외부 MCP 클라이언트(Claude Desktop 등) 연동 시연 | 클라이언트 설정 + 서비스 토큰 발급 |
| R-02 | 상호작용성 | Lighthouse·axe 자동 접근성 점수 | Chrome 또는 브라우저 확장 연결 |
| R-03 | 상호작용성 | 사용자 5명 과업 시험·SUS | 시험 참가자 |
| R-04 | 유연성 | 원격 실행 서버 배포 검증 | 전용 Docker 호스트(VM) |
| R-05 | 유연성 | 새 PC 클린 설치 리허설 | 깨끗한 환경 |
| 범위 밖 | 보안 | QA 보고서(`reports/qa-2026-10-20`) 잔여 이슈 | 별도 작업 |
| 범위 밖 | 성능 | 비용(USD) 산출 | `config/pricing.yaml` 가격 기입 |

---

## 12. 재현 방법

모든 명령은 저장소 루트 기준이다. 서비스는 `make up`으로 띄운 상태여야 한다. **실 LLM 비교 실험(마지막 블록)만 토큰을 쓴다.**

```bash
# 테스트 + 커버리지 (greenlet 설정은 pyproject.toml 에 포함)
cd backend && uv run --with pytest-cov pytest --cov --cov-report=term

# 정적 규칙 기준선·정답셋 위치 (LLM 없음)
cd backend && uv run python -c "from harness.eval.code_eval import score_code, static_findings; print(score_code(static_findings()))"
cd backend && uv run python ../scripts/check_ground_truth.py

# 정적 분석·복잡도·중복
make lint
cd backend && uvx radon cc -a -s . -e 'tests/*,migrations/*,.venv/*' && uvx radon mi . -e 'tests/*,migrations/*,.venv/*'
npx -y jscpd@4.0.5 backend/core backend/harness backend/mcp_servers backend/api backend/workers backend/sandbox backend/llm_proxy frontend/src --min-tokens 50

# 보안: 의존성·SAST·헤더
cd backend && uv export --no-dev --no-hashes --frozen > /tmp/req.txt && uvx pip-audit -r /tmp/req.txt
cd frontend && pnpm audit
cd backend && uvx bandit -q -r core harness mcp_servers api workers sandbox llm_proxy
curl -sI http://localhost:5173/ && curl -sI http://localhost:8000/api/v1/ops/health

# 성능: k6 (조회 API만, LLM 호출 없음) — 50 VU, 30초
k6 run -e TOKEN=<ADMIN access token> load.js

# 복구성: 메인 프로세스 종료 → 자동 재시작 (docker kill 은 수동 정지로 처리되어 부적합)
docker compose exec redis sh -c 'kill -TERM 1'

# 실 LLM A/B 비교 (토큰 사용, 약 26~35만). 작업공간이 정답셋과 다르면 시작 전에 중단된다
make demo-reset && make eval CODE_CONFIGS=A_nano_single,B_nano_harness REPEAT=3
```

---

## 13. 보완 기록 (1차 평가 → 최종)

### 13.1 1차 미달·부분 달성 14건 처리
| 1차 판정 | 항목 | 원인 | 조치 | 최종 |
|---|---|---|---|---|
| ❌ | 보안 헤더 0/3 | nginx·API 설정 없음 | nginx server 블록 + API 미들웨어로 5종, 버전 노출 제거. CSP를 위해 인라인 스크립트 분리 | ✅ 5/5 |
| ❌ | 테스트 126/127 | `b558d2b`에서 probe 권한을 DEVELOPER까지 열었는데 테스트는 거부를 기대 | 의도된 정책이라 테스트를 "AUDITOR 거부 + DEVELOPER 소유권 거부"로 수정 | ✅ 137/137 |
| ❌ | 재현율 0.88 | B가 놓친 7건 중 5건(C02, C04×3, C06)은 정적 규칙이 잡은 후보를 LLM이 지운 경우 | LLM 기각 보류 가드레일 확대 (자격증명·인젝션 파일) | ✅ 0.967 |
| ❌ | 감사기 견고성 0.83 | "보고하지 말 것" 주석 아래 permitAll을 LLM이 기각 | 인젝션이 있는 파일의 후보는 기각 불가 | ✅ 1.0 |
| ❌ | 커버리지 67% | 측정 설정 오류(greenlet) + 쓰지 않는 코드 + 워커·sandbox 테스트 없음 | 측정 설정 수정, 미사용 코드 삭제, 워커·API·sandbox·probe 판정 테스트 추가 | ✅ 82.8% |
| ❌ | CC>20 함수 23개 | 큰 함수에 검증·판정이 몰려 있음 | 미사용 5개 삭제, 18개 분해 (분해 전후 동작 동일 검증) | ✅ 0 |
| ❌ | MCP 도구 설명 24/26 | sandbox 도구 2개에 docstring 없음 | docstring 추가, CLI 방어 처리 | ✅ 26/26 |
| ⚠️ | 의존성 Moderate 2건 | certifi, echarts 구버전 | certifi 2026.10.19, echarts 6.1 + vue-echarts 8 | ✅ 0 |
| ⚠️ | bandit HIGH 2건 (오탐) | 캐시 키 SHA1 | `usedforsecurity=False` 명시 | ✅ 0 |
| ⚠️ | 재시작 정책 9/13 | 데이터 저장소·프론트에 정책 없음 | `restart: unless-stopped` 추가, 비정상 종료 복구 실측 | ✅ 13/13 |
| ⚠️ | 감사 완료율 95% | 실패 1건 원인 미분석 | 분석 결과 대상 잠금의 의도된 거부(CONFLICT)로 결함 아님 | ✅ 19/19 |
| ⚠️ | MI B 등급 1개 | `audits.py`가 너무 큼 | 결과 조회 라우트 분리 | ✅ 116/116 A |
| ⚠️ | 첫 화면 JS 208KB | 개요 화면이 ECharts 전체를 불러옴 | SVG 도넛 컴포넌트로 교체 | ✅ 50.8KB |
| ⚠️ | 정적 기준선 정밀도 0.82 | 참고 지표 | 제품 구성(B) 정밀도 0.893으로 판정, 기준선은 참고로 표기 | (참고) |

### 13.2 보완 중 새로 발견해 고친 결함
| 결함 | 영향 | 조치 |
|---|---|---|
| **업로드 해제 실패 시 기존 폴더 삭제** (`extract_project`가 이미 있는 경로를 `rmtree`) | 같은 경로로 재시도하면 데이터 손실 가능 (현재 호출부는 매번 새 UUID라 실제 발생은 없음) | 이번에 만든 폴더만 지우도록 수정, 회귀 테스트 추가 (기존 코드에서 실패 확인) |
| **비교 실험이 오염된 작업공간을 채점** | 2026-10-20 04:24에 예전 수정 흐름이 샘플 레포·매니페스트에 수정안을 병합해 정답셋 17곳 불일치. 그대로 돌린 실험(`3ece066b`)은 B F1 0.70, 견고성 0.0으로 **무효** | `make demo-reset`으로 복원, 러너에 실험 전 무결성 검사 추가 (불일치면 토큰 사용 전 중단) |
| `sandbox-runner` 정리기가 오류를 조용히 무시 | 정리 실패를 알 수 없음 | 경고 로그 + FastAPI lifespan으로 이전 |
| 장애 주입 지점이 쓰지 않는 수정안 경로에 있음 | `make fault-on` 시연 불가 | `run_static_scan`으로 이동, 형식 `rate_limit:N` (과거 형식도 인식) |

설계 변경은 모두 `DECISIONS.md` "2026-10-21 — ISO/IEC 25010 미달 항목 보완"에 기록했다.

---

## 14. 결론

- **측정한 58개 항목 전부 목표를 달성했다** (전체 63개 중 92.1%, 나머지 5개는 외부 환경·사용자·전용 도구가 있어야 하는 미측정 항목).
- 1차 평가의 미달 8건·부분 달성 6건은 모두 원인을 찾아 고친 뒤 다시 측정했다. 그중 AI 탐지 정확도(재현율 0.967, 감사기 견고성 1.0)는 원본 작업공간에서 A/B 3회 반복 실험으로 확인했다.
- 같은 작은 모델에서 Harness·Agent 구조가 단일 프롬프트보다 F1을 0.58에서 0.93으로 올린다는 핵심 주장도 이번 실험으로 다시 확인했다. 대가는 토큰 약 4.3배, 처리 시간 약 3.6배다.
