"""Asynchronous local runs, durable SSE, shared tools and read-only snapshots."""

import asyncio
import csv
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import Header, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import Field

from app.models import Contract
from app.storage import StateStore
from app.v2.agent import V2Agent
from app.v2.contracts import RunRequest, RunResult, TaskSpec
from app.v2.store import TERMINAL, V2Store
from app.v2.tools import ToolRegistry


class ManualRequest(Contract):
    task: TaskSpec
    query: str = Field(default="Typed tool session", max_length=6000)


class ToolRequest(Contract):
    run_id: str
    arguments: dict


class RunManager:
    def __init__(self, settings, service_factory, store=None, planner=None):
        self.settings, self.service_factory = settings, service_factory
        self.store = store or V2Store(StateStore(settings.data_directory))
        self.planner, self.agent = planner, None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis-v2")
        self.lock, self.futures = threading.Lock(), {}

    def initialize(self):
        with self.lock:
            if self.agent is None:
                registry = ToolRegistry(self.settings, self.service_factory(), self.store)
                from app.v2.integrations import register_integrations

                register_integrations(registry)
                self.agent = V2Agent(self.settings, registry, self.planner)
        return self.agent

    def submit(self, rid):
        with self.lock:
            if rid in self.futures and not self.futures[rid].done():
                return
            self.futures[rid] = self.executor.submit(self._work, rid)

    def _work(self, rid):
        try:
            self.initialize().run(rid)
        except Exception as error:
            state = self.store.run(rid)
            if state["status"] not in TERMINAL:
                state.update(
                    status="failed", message=f"Dependency initialization failed ({type(error).__name__})."
                )
                self.store.save(state, finish=True)
                self.store.event(rid, "run_failed", {"message": state["message"]})

    def recover(self):
        with self.store.base.connect() as conn:
            rows = conn.execute(
                "SELECT id,state FROM v2_runs WHERE status IN ('queued','running')"
            ).fetchall()
        for row in rows:
            if not json.loads(row["state"]).get("manual"):
                self.submit(row["id"])

    def manual_call(self, rid, name, arguments):
        agent = self.initialize()
        with agent.run_lock(rid):
            current = self.store.run(rid)
            if not current.get("manual") or current["status"] in TERMINAL:
                raise ValueError("Direct tool calls require an active manual tool session")
            if current["cancelled"]:
                agent.finish(current, "cancelled", "Cancelled at a safe execution boundary.")
                return self.result(rid)
            current["current_action"] = {"name": name, "arguments": arguments}
            self.store.save(current)
            agent.execute({"run_id": rid})
            updated = self.store.run(rid)
            return self.result(rid) if updated["status"] in TERMINAL else updated["observations"][-1]

    def result(self, rid):
        if self.agent:
            return self.agent.result(rid).model_dump(mode="json")
        from app.v2.agent import usage_report

        state = self.store.run(rid)
        artifacts = []
        for handle in state.get("result_ids", []):
            artifact = self.store.get(handle, state["session_id"], "result")["artifact"]
            artifacts.append({**artifact, "result_id": handle, "artifact_id": "art_" + handle[7:]})
        return RunResult(
            run_id=rid,
            session_id=state["session_id"],
            status=state["status"],
            task=state.get("task"),
            artifacts=artifacts,
            questions=state.get("questions", []),
            message=state.get("message", ""),
            unmet_goals=state.get("unmet_goals", []),
            usage=usage_report(self.store, rid),
        ).model_dump(mode="json")

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.agent:
            self.agent.close()


def install_routes(app, settings, service_factory):
    manager = RunManager(settings, service_factory)
    app.state.v2 = manager

    def state(rid):
        try:
            return manager.store.run(rid)
        except KeyError as error:
            raise HTTPException(404, "Run not found") from error

    @app.post("/v2/runs", status_code=202, tags=["v2"])
    def start(request: RunRequest):
        created = manager.store.create(request, settings.v2_max_run_seconds)
        manager.submit(created["run_id"])
        return {
            "run_id": created["run_id"],
            "session_id": created["session_id"],
            "events_url": f"/v2/runs/{created['run_id']}/events",
        }

    @app.get("/v2/runs", tags=["v2"])
    def history(session_id: str | None = None):
        return {"runs": manager.store.history(session_id)}

    @app.get("/v2/runs/{run_id}", response_model=RunResult, tags=["v2"])
    def get_run(run_id: str):
        state(run_id)
        return manager.result(run_id)

    @app.get("/v2/runs/{run_id}/events", tags=["v2"])
    async def events(
        run_id: str, after: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None)
    ):
        state(run_id)
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError as error:
            raise HTTPException(400, "Last-Event-ID must be an integer") from error

        async def stream():
            nonlocal cursor
            heartbeat = 0
            while True:
                rows = manager.store.events(run_id, cursor)
                for event in rows:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: update\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if state(run_id)["status"] in TERMINAL and not manager.store.events(run_id, cursor, 1):
                    yield "event: end\ndata: {}\n\n"
                    break
                heartbeat += 1
                if heartbeat % 40 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v2/runs/{run_id}/cancel", tags=["v2"])
    def cancel(run_id: str):
        state(run_id)
        manager.store.cancel(run_id)
        return {"cancel_requested": True}

    @app.post("/v2/runs/{run_id}/resume", tags=["v2"])
    def resume(run_id: str, request: RunRequest | None = None):
        existing = state(run_id)
        if request:
            request = request.model_copy(
                update={"session_id": existing["session_id"], "parent_run_id": run_id}
            )
            return start(request)
        if existing["status"] not in TERMINAL:
            manager.submit(run_id)
        return {"run_id": run_id, "session_id": existing["session_id"], "status": existing["status"]}

    @app.get("/v2/results/{result_id}/evidence", tags=["v2"])
    def evidence(
        result_id: str,
        run_id: str,
        evidence_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=500),
    ):
        current = state(run_id)
        try:
            artifact = manager.store.get(result_id, current["session_id"], "result")["artifact"]
            if evidence_id not in artifact["evidence_ids"]:
                raise ValueError("Evidence is not part of this result")
            return manager.store.base.evidence_page(evidence_id, offset, limit)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/v2/runs/{run_id}/export", tags=["v2"])
    def export(run_id: str, format: str = Query("json", pattern="^(json|csv|evidence)$")):
        current = state(run_id)
        result = manager.result(run_id)
        if format == "csv":
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "artifact",
                    "item",
                    "cohort",
                    "category",
                    "value",
                    "x",
                    "y",
                    "source",
                    "target",
                    "evidence_set_id",
                    "bin_start",
                    "bin_end",
                    "nct_id",
                    "statistics_basis",
                    "evidence_coverage",
                ]
            )
            for artifact in result["artifacts"]:
                visual = artifact["visualization"] or {}
                data = visual.get("data", [])
                items = data.get("edges", []) if isinstance(data, dict) else data
                for item in items:
                    writer.writerow(
                        [
                            artifact["title"],
                            item.get("item_id", item.get("id", "")),
                            item.get("cohort", ""),
                            item.get("category", item.get("year", item.get("bin_start", ""))),
                            item.get("trial_count", item.get("weight", "")),
                            item.get("x", ""),
                            item.get("y", ""),
                            item.get("source", ""),
                            item.get("target", ""),
                            item.get("evidence_set_id", ""),
                            item.get("bin_start", ""),
                            item.get("bin_end", ""),
                            item.get("nct_id", ""),
                            artifact["coverage"]["statistics_basis"],
                            artifact["coverage"]["evidence_coverage"],
                        ]
                    )
            return Response(
                stream.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'},
            )
        if format == "evidence":
            result = {
                ev: manager.store.base.evidence_page(ev, 0, 1000000)
                for artifact in result["artifacts"]
                for ev in artifact["evidence_ids"]
            }
        return Response(
            json.dumps(result, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{current["run_id"]}-{format}.json"'},
        )

    @app.post("/v2/runs/{run_id}/share", tags=["v2"])
    def share(run_id: str):
        state(run_id)
        result = manager.result(run_id)
        public = {k: v for k, v in result.items() if k not in ("session_id", "run_id", "usage", "questions")}
        evidence = {}
        for artifact in public["artifacts"]:
            for ev in artifact["evidence_ids"]:
                page = manager.store.base.evidence_page(ev, 0, 1000000)
                evidence[ev] = {k: v for k, v in page.items() if k != "run_id"}
        token = manager.store.share(run_id, {"result": public, "evidence": evidence, "readonly": True})
        return {
            "token": token,
            "url": "/s/" + token,
            "visibility": "Anyone with this local/self-hosted link can read this result and its trial evidence.",
        }

    @app.get("/v2/shares/{token}", tags=["v2"])
    def shared(token: str):
        try:
            return manager.store.shared(token)
        except KeyError as error:
            raise HTTPException(404, "Share not found or revoked") from error

    @app.delete("/v2/runs/{run_id}/shares/{token}", tags=["v2"])
    def revoke(run_id: str, token: str):
        state(run_id)
        try:
            manager.store.revoke(run_id, token)
        except KeyError as error:
            raise HTTPException(404, "Share not found") from error
        return {"revoked": True}

    @app.post("/v2/tool-sessions", tags=["v2 tools"])
    def manual(request: ManualRequest):
        created = manager.store.create(
            RunRequest(query=request.query, source_mode=request.task.source_mode), settings.v2_max_run_seconds
        )
        created["manual"] = True
        created["status"] = "running"
        manager.initialize().define_task(request.task, created)
        manager.store.save(created)
        return {"run_id": created["run_id"], "session_id": created["session_id"]}

    @app.post("/v2/tools/{name}", tags=["v2 tools"])
    def call_tool(name: str, request: ToolRequest):
        state(request.run_id)
        try:
            return manager.manual_call(request.run_id, name, request.arguments)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return manager
