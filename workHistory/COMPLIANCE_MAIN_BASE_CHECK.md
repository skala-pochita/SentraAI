# 최신 main 기준 재확인 — 2026-10-23

## 원격 확인 결과

- 원격 저장소: https://github.com/skala-pochita/SentraAI
- 확인한 main: `0240c71d24be857c757512b1d19bd231c7e1eb69` — PR #5, 결합 위험 근거 개선.
- 현재 작업의 기준 main: `bf06310b8cf618bc117c5bdc8f8b0d3b2dc885a0`.
- 두 main 사이: 원격 비교 API 기준 12개 커밋 차이.
- 작업 HEAD: `b7b10edb48ca3a54cfef43820d3845f2dea00d7e`, 로컬 dev_js. 이후 개선은 미커밋 작업 트리에 존재.

원격 main에는 `backend/core/compliance.py`가 없고, 보고서 노드에 규제 근거 섹션 생성 호출이 없으며, ReportPage에 규제 탭이 없다. 따라서 현재 결과는 최신 main에 기능이 반영되거나 통합 검증된 상태가 아니다.

## 통합 전 주의점

- 원격 main의 공격 경로·수정본 비교 기능을 유지하면서 보고서/API/모델/화면 변경을 통합해야 한다.
- 원격 main은 migration 0005~0007을 사용한다.
- 규제 기능 브랜치도 별도의 migration revision `0005`를 사용한다. 그대로 결합하면 revision이 중복된다.
- 최신 0007 다음에 규제 migration을 별도 revision으로 연결하고 새 DB·기존 DB 업그레이드를 검증해야 한다. 예: 아직 번호가 비어 있다면 0008.
- 183개 테스트 및 화면 QA는 이전 main 기반 dev_js의 결과다. 최신 main 통합 후 다시 검사해야 한다.

## 앞선 보고서 오류 화면의 의미

브라우저 QA 서버의 `/audits/qa-error/report` 경로는 오류 화면 검증용으로 HTTP 500 및 “검증용 보고서 오류” 응답을 의도적으로 반환했다. 이는 운영 서비스에서 실제 감사가 실패했다는 로그가 아니다.

화면 검증은 로컬 테스트 API와 실제 5종 원문 기반 합성 보고서 스냅샷을 사용했다. 실제 감사 실행→운영 DB 저장→조회가 성공했는지는 아직 통합 검증하지 않았다. `three` 의존성 누락은 프런트 빌드를 막았던 별개 문제이며, 이제 빌드는 통과한다.

출처: GitHub main 커밋 조회, main의 report.py·ReportPage.vue·migration 파일 조회, bf06310...0240c71 원격 비교, 로컬 git log/status.
