"""Exercise the deployed API -> Kafka -> worker -> MCP -> Docker -> report path.

Only use a dedicated QA stack: this creates uploaded targets and audit records.
QA_PASSWORD and QA_PROVIDER_API_KEY are read from the environment, never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:58001/api/v1")
    parser.add_argument("--mode", choices=["mock", "live"], required=True)
    parser.add_argument("--email", default="admin@demo.io")
    parser.add_argument("--zip", type=Path, default=ROOT / "artifacts/leak-validation.zip")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--audit-id", help="Resume validation of an already submitted QA audit")
    args = parser.parse_args()
    output = ROOT / "reports/release-law-qa"
    output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.api, timeout=45, trust_env=False) as client:
        def call(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            if not response.is_success:
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
            return response.json() if "json" in response.headers.get("content-type", "") else response.text

        login = call("POST", "/auth/login", json={"email": args.email, "password": os.environ["QA_PASSWORD"]})
        client.headers["Authorization"] = "Bearer " + login["access_token"]
        if args.audit_id:
            aid = args.audit_id
        else:
            uploaded = call("POST", "/targets/upload", files={"file": (args.zip.name, args.zip.read_bytes(), "application/zip")})
            settings = {"mode": args.mode, "model": "gpt-5.4-nano", "max_requests": 24, "max_total_tokens": 150000, "max_output_tokens": 768}
            if args.mode == "live":
                settings["provider_api_key"] = os.environ["QA_PROVIDER_API_KEY"]
            configured = call("PUT", f"/targets/{uploaded['id']}/execution", json=settings)
            assert configured["readiness"]["ready"], configured["readiness"]
            started = call("POST", "/audits", json={"target_ids": [uploaded["id"]], "mode": "full", "compliance_profile": {"sector": "finance", "high_impact_ai": "UNDETERMINED"}})
            aid = started["audit_id"]
            (output / f"deployed-{args.mode}-started.json").write_text(json.dumps({"audit_id": aid, "target_id": uploaded["id"]}))
            print(json.dumps({"started": aid, "mode": args.mode, "target": uploaded["id"]}), flush=True)
        deadline = time.monotonic() + args.timeout
        phases = []
        while time.monotonic() < deadline:
            audit = call("GET", f"/audits/{aid}")
            phase = [audit["status"], audit.get("current_node")]
            if not phases or phases[-1] != phase:
                phases.append(phase)
                print(json.dumps({"status": phase[0], "node": phase[1]}), flush=True)
            if audit["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(2)
        else:
            call("POST", f"/audits/{aid}/cancel")
            raise RuntimeError("QA audit timed out and was cancelled")
        assert audit["status"] == "COMPLETED", audit["status"]
        report = call("GET", f"/audits/{aid}/report")
        markdown = call("GET", f"/audits/{aid}/report.md")
        compliance = call("GET", f"/audits/{aid}/compliance")
        probes = call("GET", f"/audits/{aid}/probes")
        expected = "LIVE_VERIFIED" if args.mode == "live" else "MOCK"
        assert report["agent_coverage"] and all(c["model_verification"] == expected for c in report["agent_coverage"]), report["agent_coverage"]
        assert report["findings"] and report["compliance"]["recommendations"]
        assert len(probes["probes"]) >= 2
        assert not report["errors"], report["errors"]
        assert compliance["report_snapshot"]["findings"] == report["findings"]
        if args.mode == "live":
            assert report["target_model_usage"]["successful_requests"] > 0
        else:
            assert not report["target_model_usage"].get("successful_requests")
        result = {"mode": args.mode, "audit_id": aid, "target_id": next(t["target_id"] for t in report["targets"] if t["type"] == "repo"), "status": audit["status"], "model_verification": expected,
                  "findings": len(report["findings"]), "recommendations": len(report["compliance"]["recommendations"]),
                  "probe_ids": [p["probe_id"] for p in probes["probes"]], "model_usage": report["target_model_usage"], "phases": phases}
        (output / f"deployed-{args.mode}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        (output / f"deployed-{args.mode}-report.json").write_text(json.dumps(compliance["report_snapshot"], ensure_ascii=False, indent=2))
        (output / f"deployed-{args.mode}-report.md").write_text(markdown)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
