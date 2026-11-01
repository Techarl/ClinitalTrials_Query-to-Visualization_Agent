"""Real HTTP/Neo4j chart acceptance; leaves inspectable runs in the local UI."""

import argparse
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--output", type=Path, default=Path("data/vnext-http-acceptance.json"))
    args = parser.parse_args()
    outputs = []
    with httpx.Client(base_url=args.url, timeout=180, trust_env=False) as client:
        for condition, drug, charts in [
            (
                "psoriasis",
                "secukinumab",
                [
                    {"operation": "category", "group_by": "country", "title": "银屑病试验开展国家"},
                    {
                        "operation": "scatter",
                        "x": "enrollment",
                        "y": "trial_duration",
                        "title": "入组人数与试验持续时间",
                    },
                ],
            ),
            (
                "melanoma",
                "pembrolizumab",
                [
                    {
                        "operation": "network",
                        "relationship": "sponsor_shared_drug",
                        "title": "通过共同药物连接的申办机构",
                    },
                ],
            ),
        ]:
            filters = {"condition": condition, "interventions": [drug]}
            task = {
                "goal": charts[0]["title"],
                "filters": filters,
                "goals": [
                    {
                        "id": f"g{i}",
                        "description": c["title"],
                        "operation": c["operation"],
                        "parameters": {k: v for k, v in c.items() if k not in ("operation", "title")},
                    }
                    for i, c in enumerate(charts)
                ],
            }
            response = client.post("/v2/tool-sessions", json={"task": task, "query": task["goal"]})
            response.raise_for_status()
            rid = response.json()["run_id"]

            def call(name, arguments):
                response = client.post(f"/v2/tools/{name}", json={"run_id": rid, "arguments": arguments})
                response.raise_for_status()
                output = response.json()
                if output.get("status") == "error":
                    raise AssertionError(output)
                return output

            search = call("search_trials", {"filters": filters})
            results = [
                call("analyze", {"goal_id": f"g{i}", "scope_ids": [search["scope_id"]], **chart})
                for i, chart in enumerate(charts)
            ]
            final = call("finalize_analysis", {"result_ids": [r["result_id"] for r in results]})
            assert final["status"] == "completed"
            for artifact in final["artifacts"]:
                evidence = client.get(
                    f"/v2/results/{artifact['result_id']}/evidence",
                    params={
                        "run_id": rid,
                        "evidence_id": artifact["evidence_ids"][0],
                    },
                )
                evidence.raise_for_status()
                assert evidence.json()["total_contributors"] > 0
            outputs.append(final)
            print(
                json.dumps({"run_id": rid, "status": final["status"], "artifacts": len(results)}), flush=True
            )
    args.output.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
