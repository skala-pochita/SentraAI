// 테스트용 가짜 값 — 실제 결제 키 아님
package com.demo.shop.config;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class PaymentConfig {

    // 결제 대행사 가맹점 설정 (인코딩된 값)
    private static final String MERCHANT_CFG = "cGdfbGl2ZV9GQUtFMFRFU1RPTkxZX21lcmNoYW50X3NlY3JldF85OTk5";

    @Bean
    public PaymentSettings paymentSettings() {
        String decoded = new String(Base64.getDecoder().decode(MERCHANT_CFG), StandardCharsets.UTF_8);
        return new PaymentSettings("shop-merchant-01", decoded);
    }

    public record PaymentSettings(String merchantId, String credential) {}
}
