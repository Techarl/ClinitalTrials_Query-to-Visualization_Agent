"""Real HTTP source/export/share acceptance, using manual tools and no model calls."""

import argparse
import json
from pathlib import Path

import httpx

from app.sources import content_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--output", type=Path, default=Path("data/text-provenance-http.json"))
    args = parser.parse_args()
    pointer = "/protocolSection/eligibilityModule/minimumAge"
    with httpx.Client(base_url=args.url, timeout=180, trust_env=False) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        session = request("POST", "/v2/tool-sessions", json={
            "query": "Manual source-provenance acceptance; not a model-quality evaluation",
            "task": {"goal": "核对保存的最低年龄字段与引用来源", "goals": [
                {"id": "answer", "kind": "answer", "description": "核对原始字段"}
            ]},
        })
        rid = session["run_id"]

        def call(name, arguments):
            result = request("POST", f"/v2/tools/{name}", json={
                "run_id": rid, "arguments": arguments,
            })
            if result.get("status") == "error":
                raise AssertionError(result)
            return result

        lookup = call("lookup_trials", {"query": "NCT00046748", "limit": 1})
        match = lookup["matches"][0]
        assert match["nct_id"] == "NCT00046748"
        page = call("read_source", {"source_handle": match["source_handle"], "pointer": pointer})
        assert page["text"] and not page["content_truncated"]
        quotation = page["text"]
        result = call("publish_text_answer", {
            "goal_id": "answer", "answer": f"保存的最低年龄字段为：{quotation}。",
            "findings": [{"source_handle": match["source_handle"], "pointer": pointer,
                          "quotation": quotation}],
            "limitations": ["此结果由手动工具流程生成，仅验证引用和界面，不评估模型理解能力。"],
        })
        final = call("finalize_analysis", {"result_ids": [result["result_id"]]})
        assert final["status"] == "completed"
        assert final["usage"]["model_calls"] == 0
        finding = final["artifacts"][0]["metadata"]["findings"][0]
        provenance = finding["provenance"]
        assert provenance["document_id"] == "NCT00046748"
        assert provenance["url"] == "https://clinicaltrials.gov/study/NCT00046748"
        assert provenance["source_hash"] == match["source_hash"]
        assert provenance["field_value_hash"] == content_hash(quotation)
        assert finding["source_field"]["value"] == quotation
        exported = request("GET", f"/v2/runs/{rid}/export", params={"format": "json"})
        assert exported["artifacts"][0]["metadata"]["findings"][0] == finding
        evidence = request("GET", f"/v2/runs/{rid}/export", params={"format": "evidence"})
        assert evidence["__text_evidence__"]["artifacts"][0]["findings"][0] == finding
        share = request("POST", f"/v2/runs/{rid}/share")
        try:
            shared = request("GET", f"/v2/shares/{share['token']}")
            assert shared["readonly"] is True
            assert shared["result"]["artifacts"][0]["metadata"]["findings"][0] == finding
        finally:
            request("DELETE", f"/v2/runs/{rid}/shares/{share['token']}")
        assert client.get(f"/v2/shares/{share['token']}").status_code == 404
        receipt = {"run_id": rid, "status": "passed", "manual_tools": True,
                   "finding": finding, "usage": final["usage"],
                   "checks": ["HTTP source reading", "server-derived identity and hashes",
                              "JSON export", "text evidence export", "share and revocation"],
                   "limitation": "Manual transport/UI mechanism; not model answer accuracy"}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"run_id": rid, "status": "passed", "receipt": str(args.output)}))


if __name__ == "__main__":
    main()
