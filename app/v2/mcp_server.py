"""Official MCP SDK transports around the same registry used by HTTP and the agent."""

import argparse
import asyncio
import json
import secrets
from contextlib import asynccontextmanager

import anyio
import mcp.types as types
import uvicorn
from mcp.server import Server
from mcp.server.stdio import stdio_server

from app.config import Settings
from app.service import AnalysisService
from app.v2.agent import CONTROLS
from app.v2.api import ManualRequest, RunManager
from app.v2.contracts import RunRequest
from app.v2.integrations import register_integrations
from app.v2.store import TERMINAL
from app.v2.tools import ToolRegistry


def build_server(settings=None):
    settings = settings or Settings()
    service = None

    def service_factory():
        nonlocal service
        if service is None:
            service = AnalysisService(settings)
        return service

    manager = RunManager(settings, service_factory)
    catalog = ToolRegistry(settings, None, manager.store)
    register_integrations(catalog)
    definitions = {
        "create_tool_session": {
            "name": "create_tool_session",
            "description": "Create an isolated typed task session without model calls; returned run_id owns subsequent data handles.",
            "inputSchema": ManualRequest.model_json_schema(),
        }
    }
    for definition in catalog.descriptions() + [
        {"name": name, "description": description, "inputSchema": schema.model_json_schema()}
        for name, (schema, description) in CONTROLS.items()
        if name != "define_task"
    ]:
        schema = definition["inputSchema"]
        definitions[definition["name"]] = {
            **definition,
            "inputSchema": {
                **schema,
                "properties": {
                    **schema.get("properties", {}),
                    "run_id": {"type": "string"},
                    "access_token": {"type": "string"},
                },
                "required": [*schema.get("required", []), "run_id", "access_token"],
            },
        }
    definitions["read_run"] = {
        "name": "read_run",
        "description": "Read status and immutable artifacts for an owned run.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}, "access_token": {"type": "string"}},
            "required": ["run_id", "access_token"],
            "additionalProperties": False,
        },
    }
    definitions["cancel_run"] = {
        **definitions["read_run"],
        "name": "cancel_run",
        "description": "Request cancellation at the next safe tool boundary. Completed artifacts remain accessible.",
    }
    owned = {}

    async def list_tools(context, params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=d["name"],
                    description=d["description"],
                    inputSchema=d["inputSchema"],
                    annotations=types.ToolAnnotations(
                        readOnlyHint=d["name"] in ("inspect_evidence", "read_run", "fetch_trial_details"),
                        destructiveHint=False,
                        idempotentHint=d["name"] != "create_tool_session",
                        openWorldHint=True,
                    ),
                )
                for d in definitions.values()
            ]
        )

    def invoke(name, arguments):
        if name == "create_tool_session":
            request = ManualRequest.model_validate(arguments)
            state = manager.store.create(
                RunRequest(query=request.query, source_mode=request.task.source_mode),
                settings.v2_max_run_seconds,
            )
            state.update(manual=True, status="running")
            manager.initialize().define_task(request.task, state)
            manager.store.save(state)
            token = secrets.token_urlsafe(32)
            owned[state["run_id"]] = token
            return {"run_id": state["run_id"], "session_id": state["session_id"], "access_token": token}
        args = dict(arguments)
        rid = args.pop("run_id", None)
        token = args.pop("access_token", "")
        if rid not in owned or not secrets.compare_digest(owned[rid], token):
            raise ValueError("This MCP server instance does not own that run; create a tool session first")
        if name == "read_run":
            return manager.result(rid)
        if name == "cancel_run":
            manager.store.cancel(rid)
            return {"cancel_requested": True}
        state = manager.store.run(rid)
        if state["status"] in TERMINAL:
            if name == "inspect_evidence":
                tool = manager.initialize().registry.tools[name]
                return tool.execute(tool.schema.model_validate(args), state)
            raise ValueError("Run is already complete")
        return manager.manual_call(rid, name, args)

    async def call_tool(context, params):
        try:
            if params.name not in definitions:
                raise ValueError("Unknown tool")
            output = await anyio.to_thread.run_sync(invoke, params.name, params.arguments or {})
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(output, ensure_ascii=False))],
                structuredContent=output,
                isError=output.get("status") == "error",
            )
        except Exception as error:
            # Do not expose HTTP/provider error bodies or credentials.
            message = str(error) if isinstance(error, (ValueError, KeyError)) else type(error).__name__
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=message[:600])], isError=True
            )

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            manager.close()
            if service:
                service.close()

    server = Server(
        "trialscope",
        version="2.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=lambda name: definitions[name]["inputSchema"] if name in definitions else None,
        lifespan=lifespan,
    )
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    server = build_server()
    if args.transport == "http":
        uvicorn.run(server.streamable_http_app(host="127.0.0.1"), host="127.0.0.1", port=args.port)
    else:

        async def run():
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())

        asyncio.run(run())


if __name__ == "__main__":
    main()
