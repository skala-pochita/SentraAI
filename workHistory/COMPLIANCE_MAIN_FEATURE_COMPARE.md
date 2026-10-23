# 기존 main 기능 유지 여부 비교

비교 대상: 현재 원격 main `0240c71` ↔ 규제 기능 작업본 `dev_js` (`bf06310` 기반 + fa3090d/b7b10ed 및 로컬 개선).

## 결론

예전 main `bf06310`에서 규제 기능을 추가한 개발은 맞다. 그러나 현재 main `0240c71`의 기존 기능을 모두 유지한 채 규제 기능만 얹은 통합본은 아니다.

이것은 원격 push·merge 여부가 아니라 **코드 내용의 차이**다.

| 기능 | 현재 main | 규제 기능 작업본 |
|---|---|---|
| 기존 기본 보안 검사·보고서 구조 | 존재 | 예전 main 기반 구조 유지 |
| 국내 규제 RAG·인용/해시·규제 보고서 탭 | 없음 | 추가 구현 |
| 공격 경로의 단계·연결별 근거 | attack_paths/path_schema/path_validation | 최신 모듈 없음 |
| 경로의 코드·설정 근거 수집 | tool_code_evidence 및 새 agent_static_check | 새 모듈 없음, 기존 수집 노드도 예전 버전 |
| 공격 경로 상세·실행/정적/조건부 구분 UI | AttackPathDetails와 개선 RisksPage | 상세 컴포넌트 없음, RisksPage 예전 버전 |
| 수정본 재업로드·기준 감사 연결 | project_versions, baseline_audit_id | 해당 모듈·필드 없음 |
| 수정 전후 비교 API·화면 | compare router, audit_compare, ComparePage 및 경로 | 모듈·화면·경로 없음 |
| 보고서의 새 공격 경로 상세·미확인 구간 | correlation_gaps, steps/edges/code_reasons 출력 | 상세 출력 없음, 규제 출력만 별도 추가 |
| DB migration | 기존 0005→0006→0007 | 규제 전용 0005: 번호 중복 충돌 |

## 주요 근거

- main에만 있는 핵심 모듈: `backend/core/{attack_paths,audit_compare,path_schema,path_validation,project_versions,tool_code_evidence}.py`.
- main에만 있는 화면: `frontend/src/components/AttackPathDetails.vue`, `frontend/src/pages/ComparePage.vue`.
- main의 `backend/core/db/models.py`에는 `baseline_audit_id`, Finding의 `subject`, RiskPath의 `payload` 및 `correlation_finding_id`가 추가됐다. 규제 작업본에는 해당 변경이 없다.
- main의 보고서 노드는 `correlation_gaps`와 경로 payload를 출력한다. 규제 작업본의 보고서 노드는 예전 버전에 규제 섹션을 추가한 상태다.
- `bf06310`의 기존 파일 자체가 작업본에서 삭제된 것은 없다. 현재 main에 이후 추가·개선된 기능이 작업본에 반영되지 않은 상태다.

## 필요한 통합

최신 main을 기준으로 규제 관련 변경을 이식하고, 보고서/API/DB/화면에서 양쪽 변경을 함께 유지해야 한다. 규제 migration은 기존 최신 revision 다음으로 재배치하고 전체 회귀 테스트를 다시 수행해야 한다.

현재의 183개 통과는 이 규제 작업본의 테스트 결과이며, 최신 main의 새 기능까지 보존됐음을 입증하지 않는다.

상세 파일 분류는 `COMPLIANCE_MAIN_FEATURE_COMPARE.json`에 기록했다. 이번 비교에서는 소스를 병합하거나 기존 main을 수정하지 않았다.
