"""Per-audit fixed-destination relay. This is not a general HTTP proxy."""

import hmac
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx


class Relay(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        expected = "Bearer " + os.environ["AUDIT_PROXY_TOKEN"]
        if self.path != "/v1/chat/completions" or not hmac.compare_digest(
            self.headers.get("Authorization", ""), expected
        ):
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 128 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(size)
            with httpx.Client(timeout=100, trust_env=False, follow_redirects=False) as client:
                response = client.post(
                    os.environ["AUDIT_PROXY_URL"].rstrip("/") + "/chat/completions",
                    content=body,
                    headers={"Authorization": expected, "Content-Type": "application/json"},
                )
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        except (httpx.HTTPError, ValueError, OSError):
            self.send_error(502, "Model relay unavailable")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Relay).serve_forever()
