package com.demo.shop.service;

import com.demo.shop.security.JwtUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

@Service
public class AuthService {

    private static final Logger log = LoggerFactory.getLogger(AuthService.class);

    private final JwtUtil jwtUtil;
    private final PasswordEncoder passwordEncoder;

    public AuthService(JwtUtil jwtUtil, PasswordEncoder passwordEncoder) {
        this.jwtUtil = jwtUtil;
        this.passwordEncoder = passwordEncoder;
    }

    public String login(String username, String rawPassword, String storedHash) {
        log.info("login attempt user={} password={}", username, rawPassword);
        if (!passwordEncoder.matches(rawPassword, storedHash)) {
            throw new IllegalArgumentException("bad credentials");
        }
        String token = jwtUtil.createToken(username, "USER");
        log.debug("issued token={} for user={}", token, username);
        return token;
    }
}
