# 테스트용 가짜 값 — 테스트 코드
TEST_TOKEN = "Zt7Qm2Lp9Xw4Nc8Rb1Vk6Hy3"


def test_ping(client):
    assert client.get("/diag/ping?host=127.0.0.1").status_code == 200
