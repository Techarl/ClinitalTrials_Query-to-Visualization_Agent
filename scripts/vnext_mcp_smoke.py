"""Actual official MCP client calls through stdio or local Streamable HTTP."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


async def exercise(read, write):
    async with ClientSession(read, write) as client:
        await client.initialize()
        catalog = await client.list_tools()
        names = [tool.name for tool in catalog.tools]
        assert {"search_trials", "analyze", "search_pubmed", "inspect_evidence"} <= set(names)

        async def call(name, arguments):
            if name != "create_tool_session":
                arguments = {**arguments, "access_token": created["access_token"]}
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError(name + ": " + str(result.content)[:400])
            return result.structured_content or json.loads(result.content[0].text)

        created = await call(
            "create_tool_session",
            {
                "task": {
                    "goal": "MCP real-source validation",
                    "filters": {"condition": "melanoma", "interventions": ["pembrolizumab"]},
                    "goals": [
                        {"id": "chart", "description": "Phase counts", "operation": "category"},
                        {"id": "literature", "description": "PubMed sources", "kind": "literature"},
                    ],
                }
            },
        )
        rid = created["run_id"]
        search = await call(
            "search_trials",
            {"run_id": rid, "filters": {"condition": "melanoma", "interventions": ["pembrolizumab"]}},
        )
        result = await call(
            "analyze",
            {"run_id": rid, "scope_ids": [search["scope_id"]], "goal_id": "chart", "operation": "category"},
        )
        evidence_id = next(row["evidence_set_id"] for row in result["summary"]["rows"] if row["trial_count"])
        evidence = await call(
            "inspect_evidence",
            {"run_id": rid, "result_id": result["result_id"], "evidence_id": evidence_id, "limit": 2},
        )
        literature = await call(
            "search_pubmed",
            {
                "run_id": rid,
                "goal_id": "literature",
                "query": "pembrolizumab melanoma randomized trial",
                "limit": 2,
            },
        )
        final = await call(
            "finalize_analysis", {"run_id": rid, "result_ids": [result["result_id"], literature["result_id"]]}
        )
        assert final["status"] == "completed" and len(final["artifacts"]) == 2
        assert evidence["citations"] and literature["articles"]
        return {
            "status": "passed",
            "tools": names,
            "run_id": rid,
            "matched_count": search["matched_count"],
            "phase_rows": result["summary"]["rows"],
            "evidence_contributors": evidence["total_contributors"],
            "pmids": [a["pmid"] for a in literature["articles"]],
            "model_calls": final["usage"]["model_calls"],
        }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--url", default="http://127.0.0.1:8002/mcp")
    args = parser.parse_args()
    if args.transport == "stdio":
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.v2.mcp_server"],
            env={**os.environ, "APP_DATA_DIR": "data/vnext-mcp", "PYTHONUTF8": "1"},
        )
        async with stdio_client(params) as (read, write):
            report = await exercise(read, write)
    else:
        import httpx2

        async with httpx2.AsyncClient(trust_env=False, timeout=180) as http:
            async with streamable_http_client(args.url, http_client=http) as streams:
                report = await exercise(streams[0], streams[1])
    report["transport"] = args.transport
    path = Path("data/vnext-mcp-" + args.transport + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
