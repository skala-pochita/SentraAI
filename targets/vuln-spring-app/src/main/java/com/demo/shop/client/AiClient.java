// 테스트용 가짜 값 — 실제 API 키 아님
package com.demo.shop.client;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.springframework.stereotype.Component;

@Component
public class AiClient {

    private static final String AI_API_KEY = "sk-proj-FAKE0TESTONLY0a1b2c3d4e5f6g7h8i9j0kLmNoPqRsT";
    private static final String ENDPOINT = "https://api.example-llm.test/v1/chat/completions";

    private final HttpClient http = HttpClient.newHttpClient();

    public String summarize(String text) throws Exception {
        String body = "{\"model\":\"small\",\"messages\":[{\"role\":\"user\",\"content\":\"" + text + "\"}]}";
        HttpRequest req = HttpRequest.newBuilder(URI.create(ENDPOINT))
                .header("Authorization", "Bearer " + AI_API_KEY)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        return http.send(req, HttpResponse.BodyHandlers.ofString()).body();
    }
}
