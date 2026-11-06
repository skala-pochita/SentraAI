package demo;

// 파일 이름에 Config/Security 가 없어도 내용(SecurityFilterChain)으로 선택돼야 한다 (SP4)
public class WebAuth {
    public SecurityFilterChain chain(HttpSecurity http) throws Exception {
        http
            .csrf(c -> c
                .disable())
            .headers(h -> h.frameOptions(f -> f.disable()))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/actuator/health", "/actuator/info").permitAll()
                .requestMatchers("/admin/**",
                                 "/login").permitAll()
                .requestMatchers("/actuator/**").permitAll()
                .anyRequest()
                    .permitAll());
        return http.build();
    }

    public SecurityFilterChain api(HttpSecurity http) throws Exception {
        http.csrf(AbstractHttpConfigurer::disable);
        // http.csrf().disable();  주석은 탐지하지 않는다
        return http.build();
    }
}
