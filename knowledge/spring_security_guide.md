---
title: Spring Security 올바른 설정 가이드
source_type: framework
ref_code: SPRING-SECURITY-6
source_url: https://docs.spring.io/spring-security/reference/
---
# Spring Security 올바른 설정 가이드 (Spring Boot 3 / Security 6)

## SecurityFilterChain 기본 원칙과 permitAll 올바른 설정
`anyRequest().permitAll()` 은 모든 요청을 인증 없이 허용하므로 쓰지 않는다. 올바른 설정은 공개 경로만 `requestMatchers(...).permitAll()` 로 명시하고 마지막에 `anyRequest().authenticated()` 로 나머지를 막는 것이다.
```java
http.authorizeHttpRequests(auth -> auth
    .requestMatchers("/actuator/health", "/login").permitAll()
    .requestMatchers("/admin/**").hasRole("ADMIN")
    .anyRequest().authenticated());
```
`/actuator/health`, `/login` 같은 공개 엔드포인트에만 permitAll 을 거는 것은 정상 설정이다.

## CSRF 설정
세션·폼 로그인(쿠키 인증) 체인에서는 CSRF 를 켜 둔다 (`CookieCsrfTokenRepository.withHttpOnlyFalse()` 등).
쿠키를 쓰지 않고 `Authorization: Bearer` 헤더만 쓰는 Stateless JWT API 체인(`SessionCreationPolicy.STATELESS`)은 `csrf.disable()` 이 허용된다. 체인을 `securityMatcher` 로 분리해 판단한다.

## CORS 설정
`allowedOriginPatterns("*")` 와 `allowCredentials(true)` 를 함께 쓰면 임의 사이트가 사용자 쿠키로 API 를 호출할 수 있다. 허용 origin 을 실제 프론트엔드 도메인으로 제한한다.

## PasswordEncoder
`NoOpPasswordEncoder` 는 평문 비교로 운영 금지. `PasswordEncoderFactories.createDelegatingPasswordEncoder()` 또는 `new BCryptPasswordEncoder()` 를 사용한다.

## H2 콘솔과 frameOptions
H2 콘솔은 개발 프로파일에서만 켜고 `headers.frameOptions().disable()` 도 개발 체인에 한정한다. 운영 `spring.h2.console.enabled=false`.

## Actuator 노출
`management.endpoints.web.exposure.include=health,info` 로 제한하고 나머지 엔드포인트는 ADMIN 인증 뒤에 둔다.

## 비밀값 주입
`application.yml` 에는 `password: ${DB_PASSWORD}` 처럼 플레이스홀더만 두고 실제 값은 Vault·환경변수로 주입한다. JWT 서명키도 `@Value("${jwt.secret}")` 로 주입한다.
