# 근거가 연결된 공격 경로 — 구현 및 검증

기준일: 2026-10-23

## 구현 결과

- 기존 결합 위험 5개 규칙에 버전이 있는 `steps / edges / evidence_refs` 계약을 적용했다.
- 단계·연결마다 **실행 확인 / 정적 근거 / 조건부 추정**을 표시한다.
- 단계와 연결을 선택하면 관련 Finding, 파일·라인과 마스킹 코드, 해당 probe 시도의 입력·응답·도구 호출을 조회한다.
- 대상 ID와 실제 run ID로 연결한다. 여러 시도의 `tools_hit` 합집합이나 Finding 제목을 경로 판단에 사용하지 않는다.
- 명시된 `side_effect`로 삭제·변경·외부 전송을 구분한다. 승인 필드 누락을 승인 없음으로 간주하지 않는다.
- 도구가 `executed=true`를 반환한 경우에도 관측 출처를 **대상 보고 기반**으로 표시하고 실제 삭제·유출 결과는 조건부로 남긴다.
- 동일 시도의 입력과 호출은 관측 사실이지만 인과 관계를 독립 계측한 것은 아니므로 연결 자체는 조건부로 표시한다.
- LLM 재판정 결과를 제한된 MCP 도구로 DB에 저장하고, 미확인 결과를 차단 성공으로 표시하지 않는다.
- 경로 참조는 해당 감사와 대상에 속해야 한다. 파일 위치와 도구 호출 인덱스·실행 여부도 확인한다.
- 중복 Finding 저장 시 경로를 중복 생성하지 않으며 이전 실행 참조를 보존한다. 저장 결과를 graph로 돌려 API와 보고서의 경로 payload를 일치시킨다.
- 기존 3D 그래프는 명시적인 단계와 연결만 그린다. 조건부 연결은 점선으로 표시하고 실행 전파를 암시하던 파티클은 제거했다.
- 2D 단계/근거 패널은 키보드·모바일·WebGL 미지원 환경에서도 사용할 수 있다.
- 구형 감사의 null payload는 기존 결과로 표시하고 공격 순서를 소급 생성하지 않는다.
- 동작 특성·대상별 실행·도구별 백엔드 참조가 없으면 미확인 사유를 화면과 보고서에 표시한다.

## 규칙의 판정 변경

| 규칙 | 적용한 기준 |
|---|---|
| CORR-001 | 같은 대상·같은 성공 시도에서 승인 요구가 명시적으로 false인 변경/삭제 도구의 실행 보고가 있어야 함. 삭제는 CRITICAL, write는 HIGH이며 실제 피해는 조건부 |
| CORR-002 | 같은 대상의 동일 외부 전송 도구와 제한 누락 Finding, 성공 시도의 호출을 연결. 실제 외부 유출까지 확인하지 않았으므로 HIGH·조건부 영향 |
| CORR-003 | 명시적으로 연결된 소스 프로젝트 안의 동일 변수명만 연결. 값 동일성·런타임 사용·유출은 확정하지 않으며 HIGH |
| CORR-004 | 같은 대상의 프롬프트 비밀 선언과 프롬프트 유출 판정을 연결. 해당 비밀 유출을 확정하거나 원본 Finding을 CRITICAL로 자동 승격하지 않음 |
| CORR-005 | 연결된 repo의 ID/이름과 일치하는 도구별 `backend_api` 선언, 해당 도구의 Finding이 필요함. Agent 전체의 `backend_api`나 같은 ZIP이라는 사실만으로 호출 경로를 생성하지 않음 |

CORR-005는 도구 수준의 백엔드 매핑이 없는 기존 샘플에서는 미확인 사유로 표시된다. 일반 함수 호출 분석으로 API 경로를 추론하는 기능은 포함하지 않았다.

## 데이터 전달 및 저장

- 도구 메타데이터는 `intake`의 Agent별 plan snapshot을 확장해서 결합 단계에 전달한다. 부모 그래프가 이미 보유한 plan을 재사용하므로 AgentAuditOutput에 manifest 전체를 추가하지 않았다.
- Finding에 구조화된 `subject`, RiskPath에 `payload`와 고유한 `correlation_finding_id`, ProbeRun에 최종 `verdict`를 추가했다.
- 마이그레이션은 `0005_attack_paths`다. 초기 리비전이 현재 모델을 생성하는 저장소 특성에 맞춰 새 DB에서도 중복 컬럼 오류가 발생하지 않게 했다.
- `finalize_probe_verdict`는 `probe_judge` 노드에만 허용하고 현재 감사의 미확인 run만 최종 판정할 수 있다. 같은 결과 재전달은 멱등 처리한다.
- 기존 조회 API에 payload를 추가하고 `GET /audits/{audit_id}/risk-paths/{path_id}/evidence`를 추가했다. 감사 완료 전 409 및 대상 접근 제어를 유지한다.
- 감사 대상 원본 소스 수정이나 자동 공격 범위 확장은 수행하지 않는다.

## 검증 결과

| 검증 | 결과 |
|---|---|
| 전체 백엔드 테스트, 실제 임시 PostgreSQL 사용 | **176 passed, 3 deselected**, skip 없음 |
| 새 테스트 | 경로 규칙 단위 16개 + DB/MCP/API 통합 2개 |
| `make lint` | ruff, mypy, vue-tsc 모두 통과 |
| 프론트 production build | 통과. 기존 대형 chunk 경고는 남아 있음 |
| 마이그레이션 | 빈 임시 DB에서 전체 upgrade, 0005→0004 downgrade, 재upgrade 통과 |
| Chrome UI fixture | 단계/연결 선택, 입력·응답·호출 패널, 구형 경로 표시 통과. page error 0 |
| 모바일 390px | 수평 overflow 없음, 단계 버튼과 근거 패널 사용 가능 |
| 키보드 / WebGL 없음 | Enter로 단계 선택 및 WebGL 비활성 환경의 2D 조회 통과 |

실행 명령:

```bash
# 저장소 루트에서 실행. 검증 전용 임시 PostgreSQL 사용.
TEST_DATABASE_URL=postgresql+asyncpg://auditor:auditor@127.0.0.1:15439/auditor_test \
  OPENAI_API_KEY= UV_NO_SYNC=1 UV_CACHE_DIR=/private/tmp/sentra-uv-cache make test

UV_NO_SYNC=1 UV_CACHE_DIR=/private/tmp/sentra-uv-cache make lint
cd frontend && node_modules/.bin/vue-tsc --noEmit && node_modules/.bin/vite build
```

검증 도구의 의존성 자동 동기화가 외부 네트워크를 요구하여, 백엔드는 기존 venv를 사용했다. 프론트는 기존 lockfile대로 누락 의존성을 복원했으며 package.json/lockfile은 변경하지 않았다.

## 적용 및 남은 검증 범위

- 실행 중인 기존 Docker 서비스에는 배포하지 않았다. 적용 시 새 이미지의 `alembic upgrade head`를 먼저 수행하고 API·워커·MCP·프론트를 함께 갱신해야 한다. 새 워커와 구형 백엔드를 혼용하지 않는다.
- 예전 감사는 기존 형식으로 열리며 새 경로를 보려면 새 감사를 실행한다.
- 실제 LLM/외부 인프라를 요구하는 3개 테스트는 실행 대상에서 제외했다. 실제 모델의 공격 성공률을 이번 결과로 주장하지 않는다.
- 브라우저는 새 production build와 고정된 API fixture로 검증했다. 실제 DB/MCP/API의 동작은 별도 통합 테스트로 검증했다. 실제 LLM을 포함한 배포 환경 전체 데모는 별도 검증 범위다.

## 추가 개선 — 결합 이유에 실제 코드·설정 근거 표시 (2026-10-23)

사용자 피드백에 따라 **심각도 산정은 기존 규칙 그대로 유지**했다. 정상/공격 비교나 피해 검증으로 심각도를 재산정하는 방향은 이번 작업 범위에서 제외했다.

### 변경한 사용자 경험

결합 위험 카드에 ‘결합 판단의 코드·설정 근거’를 추가했다. CRITICAL/HIGH로 분류한 조건을 문장으로 설명하고, 관련 파일·라인·마스킹된 실제 코드 조각을 바로 보여준다.

- 도구 설정의 `side_effect`, `requires_approval`, `scopes`를 YAML 노드의 실제 위치에서 수집한다. 기존의 도구 이름 한 줄만 있는 근거를 보강했다.
- Python의 도구명 일치 함수와 `name/tool_name == 도구명` 분기를 구현 후보로 제시한다. 다른 도구의 elif 분기는 포함하지 않는다.
- 구현 후보와 연결된 repo의 코드 Finding이 파일·라인·내용까지 일치할 때만 그 코드 문제를 결합 근거에 포함한다. 단순히 같은 프로젝트라는 이유로 연결하지 않는다.
- 이름 일치는 런타임 바인딩이나 승인 검사 누락의 증명이 아니므로 ‘구현 후보’와 미검증 범위를 명시한다. 해당 코드가 없으면 ‘구현 코드는 연결하지 못함’을 표시한다.
- 코드 조각은 기존 Finding의 근거 인덱스를 참조한다. 저장된 파일·라인·내용·근거 분류와 다른 payload는 거부한다.
- JSON/Markdown 보고서에도 같은 코드·설정 조각과 결합 이유를 포함한다. 기존 감사에는 조각을 소급 생성하지 않는다.

### 구현 범위와 제한

- Python 소스는 import하거나 실행하지 않고 AST로만 읽는다. 테스트/예제 파일과 루트 밖 symlink를 제외하고 파일 수·바이트·조각 길이를 제한한다.
- 전체 코드의 인가 흐름이나 임의 언어의 도구 등록 관계를 분석하는 기능은 아니다. 확인하지 못한 연결을 코드 취약점으로 확정하지 않는다.
- 내장 샘플의 새 `demo-reset`에는 `app.py`, `policy.py`의 소스 snapshot을 함께 복사한다. 이 snapshot이 실행 중인 이미지와 동일하다는 것은 별도 검증하지 않으며 후보로만 표시한다. 기존 샘플 workspace에 소스가 없으면 설정 근거만 제공한다.
- 업로드 프로젝트는 업로드한 원본 소스에서 근거를 읽는다. 원본 파일은 변경하지 않는다.
- 새 DB migration은 추가하지 않았다. 기존 migration 0005 위의 JSON 근거 계약을 호환 확장했다.

### 검증

- 전체 백엔드 테스트: **182 passed, 3 deselected**, 실제 임시 PostgreSQL 사용. 새 단위 테스트 6개 추가.
- 기존 CRITICAL/HIGH 산정 유지, 다른 파일·라인·내용·프로젝트의 코드 오결합 방지, 구체적인 설정 줄, 실행 없는 AST 분석, symlink/테스트 소스 제외를 확인했다.
- DB/MCP/API 테스트에서 코드 snapshot과 근거 종류 위조를 거부하고, 새 근거의 저장·조회·보고서를 확인했다.
- lint/mypy/vue-tsc 및 production build 통과.
- Chrome fixture: 설정 두 줄과 구현 후보의 코드·파일·라인 표시, 키보드 선택 확인. page error 0, 모바일 390px 수평 overflow 없음.
- 실제 LLM 테스트와 기존 실행 중인 서비스 배포는 수행하지 않았다. 새 감사를 실행해야 이번 근거가 수집된다.
