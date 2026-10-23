"""Local browser QA: built UI + deterministic API fixtures. No production DB/auth.

python3 scripts/qa_report_ui_server.py --port 4178 --role ADMIN
"""

from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=4178)
parser.add_argument(
    "--role", choices=["ADMIN", "AUDITOR", "DEVELOPER"], default="ADMIN"
)
args = parser.parse_args()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *values, **kwargs):
        super().__init__(*values, directory=str(ROOT / "frontend/dist"), **kwargs)

    def payload(self, value, content_type="application/json", status=200):
        raw = (
            json.dumps(value, ensure_ascii=False)
            if content_type == "application/json"
            else value
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/v1/auth/mode":
            return self.payload({"mode": "local"})
        if path == "/api/v1/me":
            return self.payload(
                {
                    "id": "qa-user",
                    "email": "qa@example.test",
                    "name": "QA Fixture " + args.role,
                    "role": args.role,
                }
            )
        if path == "/api/v1/audits":
            return self.payload([])
        if path.startswith("/api/v1/audits/"):
            leaf = path.split("/")[-1]
            if leaf in {"qa-local", "qa-error", "qa-empty"}:
                return self.payload({"id": leaf, "status": "COMPLETED"})
            if leaf.startswith("compliance") and args.role == "DEVELOPER":
                return self.payload(
                    {"error": {"code": "FORBIDDEN", "message": "QA role forbidden"}},
                    status=403,
                )
            if "/qa-error/" in path:
                return self.payload(
                    {
                        "error": {
                            "code": "QA_FIXTURE_ERROR",
                            "message": "검증용 보고서 오류",
                        }
                    },
                    status=500,
                )
            report = json.loads(
                (ROOT / "reports/compliance-improvement/unsafe_report.json").read_text()
            )
            if "/qa-empty/" in path:
                md = "# 규제·가이드라인 검토 권고\n\n검증된 원문 연결이 없어 검토 권고를 생성하지 않았습니다.\n\n## 근거 연결 보류\n- Finding QA-EMPTY: 검토된 관련 근거 연결 없음"
                if leaf.endswith(".md"):
                    return self.payload(md, "text/markdown")
            if leaf.endswith(".md"):
                return self.payload(
                    (
                        ROOT / "reports/compliance-improvement/unsafe_report.md"
                    ).read_text(),
                    "text/markdown",
                )
            if leaf == "compliance":
                return self.payload({"report_snapshot": report, **report["compliance"]})
            return self.payload([])
        if path.startswith("/api/"):
            return self.payload({})
        # Vue history fallback for /audits/.../report.
        if not Path(self.translate_path(path)).is_file():
            self.path = "/index.html"
        return super().do_GET()


print(f"QA FIXTURES ONLY: http://127.0.0.1:{args.port}, role={args.role}", flush=True)
ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
