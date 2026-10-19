---
title: 주요 CWE 요약
source_type: cwe
ref_code: CWE
source_url: https://cwe.mitre.org/
---
# 주요 CWE 요약

## CWE-798 하드코딩된 자격증명 (Use of Hard-coded Credentials)
소스·설정에 비밀번호, API 키, 서명키를 직접 기록. 문자열 분할("sk_live_" + "...")이나 Base64 인코딩도 하드코딩에 해당한다. 대응: 비밀 저장소 주입, 노출 키 폐기.

## CWE-89 SQL 인젝션
문자열 연결로 SQL/JPQL 을 만들 때 발생. 대응: PreparedStatement 바인딩, JPA setParameter.

## CWE-22 경로 조작 (Path Traversal)
`Paths.get(baseDir + filename)` 처럼 요청 값으로 경로를 만들면 `../` 로 기준 디렉터리 밖 파일 접근. 대응: normalize 후 startsWith(base) 검증.

## CWE-284 부적절한 접근 통제
인증·인가 누락. `permitAll()` 남용. 대응: 기본 거부, 명시적 공개 경로.

## CWE-352 CSRF
세션 쿠키 기반 인증에서 CSRF 토큰 없이 상태 변경 요청 허용. 쿠키 없는 Stateless JWT(Authorization 헤더) API 는 CSRF 영향이 낮아 비활성화가 허용된다.

## CWE-532 로그에 민감정보 기록
비밀번호·토큰·카드번호를 로그로 출력. 대응: 마스킹, 로깅 금지 필드 지정.

## CWE-209 오류 메시지 정보 노출
스택트레이스·내부 경로를 응답에 포함. 대응: 운영에서 `include-stacktrace: never`.

## CWE-615 주석 속 민감정보
주석에 계정·비밀번호·내부 IP·호스트명 기록. 대응: 주석 제거, 계정 비활성화.

## CWE-942 과도하게 허용된 CORS
와일드카드 origin 과 credentials 동시 허용. 대응: 허용 origin 목록 제한.
