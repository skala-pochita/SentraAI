---
title: OWASP Top 10 (2021) 요약
source_type: owasp_top10
ref_code: OWASP-TOP10-2021
source_url: https://owasp.org/Top10/
---
# OWASP Top 10 (2021) 요약

> 팀이 감사 규칙 근거용으로 요약·재작성한 문서. 원문은 상단 링크 참고.

## A01:2021 접근 통제 실패 (Broken Access Control)
정의: 사용자가 권한 밖의 데이터·기능에 접근할 수 있는 상태. 가장 흔한 웹 취약점 분류.
예시: Spring Security 에서 `anyRequest().permitAll()` 로 모든 요청을 인증 없이 허용, 다른 사용자 ID 로 주문 조회(IDOR), 경로 조작(Path Traversal)으로 서버 파일 읽기, CORS 오설정.
대응: 기본 거부(deny by default), 서버 측 권한 검사, 소유자 검사, 파일 경로 정규화 후 기준 디렉터리 검증. 관련 CWE-284, CWE-22, CWE-352.

## A02:2021 암호화 실패 (Cryptographic Failures)
정의: 민감 데이터가 평문 저장·전송되거나 약한 알고리즘을 쓰는 문제.
예시: `NoOpPasswordEncoder` 로 비밀번호 평문 저장, 하드코딩된 암호키.
대응: bcrypt/argon2 등 적응형 해시, 키는 KMS·Vault 관리. 관련 CWE-256, CWE-327.

## A03:2021 인젝션 (Injection)
정의: 신뢰할 수 없는 입력이 쿼리·명령의 일부로 해석되는 문제. SQL, JPQL, OS 명령, LDAP 등.
예시: `jdbcTemplate.query("... WHERE status = '" + status + "'")`, `createQuery("... LIKE '%" + keyword + "%'")`.
대응: 바인딩 파라미터(`?`, `:name`), ORM 파라미터 바인딩, 입력 검증. 관련 CWE-89.

## A05:2021 보안 설정 오류 (Security Misconfiguration)
정의: 기본값·불필요 기능·과도한 노출 설정.
예시: Actuator `exposure.include: "*"`, 운영에서 H2 콘솔 활성화, `include-stacktrace: always`, 와일드카드 CORS + 자격증명 허용.
대응: 환경별 설정 분리, 최소 노출, 보안 헤더 유지. 관련 CWE-200, CWE-209, CWE-942.

## A07:2021 식별 및 인증 실패 (Identification and Authentication Failures)
정의: 인증 메커니즘 약점, 하드코딩된 자격증명, 세션 관리 미흡.
예시: 소스코드·설정 파일에 DB 비밀번호·API 키·JWT 서명키 하드코딩, 주석에 임시 계정.
대응: 비밀값은 Vault·환경변수 주입, 노출된 키 즉시 폐기·재발급. 관련 CWE-798, CWE-259.

## A09:2021 보안 로깅 및 모니터링 실패
정의: 공격 탐지를 위한 로깅 부재, 또는 로그에 민감정보를 기록.
예시: `log.info("password={}", rawPassword)`, 토큰을 debug 로그로 출력.
대응: 민감 필드 마스킹, 로그 접근 통제. 관련 CWE-532.
