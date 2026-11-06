package demo;

// 음성: 정상 설정
public class SafeChain {
    public SecurityFilterChain chain(HttpSecurity http) throws Exception {
        http
            .csrf(c -> c.ignoringRequestMatchers("/webhook"))
            .headers(h -> h.frameOptions(f -> f.disable()))
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/admin/**").hasRole("ADMIN")
                .anyRequest().authenticated());
        return http.build();
    }
}
