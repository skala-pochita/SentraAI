// 테스트용 가짜 값 — 실제 결제 키 아님
package com.demo.shop.client;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.springframework.stereotype.Component;

@Component
public class PaymentClient {

    private static final String BASE_URL = "https://api.example-pay.test/v1";
    private final String auth = "sk_live_" + "51FAKEtestOnly" + "0000NotARealKey9999";

    private final HttpClient http = HttpClient.newHttpClient();

    public String charge(long orderId, long amount) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(BASE_URL + "/charges"))
                .header("Authorization", "Bearer " + auth)
                .POST(HttpRequest.BodyPublishers.ofString("{\"order\":" + orderId + ",\"amount\":" + amount + "}"))
                .build();
        return http.send(req, HttpResponse.BodyHandlers.ofString()).body();
    }
}
