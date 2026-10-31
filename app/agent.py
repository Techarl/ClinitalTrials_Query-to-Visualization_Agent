"""Bounded LangGraph orchestration with SQLite checkpoints and structured session state."""

import platform
import sqlite3
import threading
import time
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.context import ContextBuilder
from app.extensions import (
    BaselineDecisionPolicy,
    BaselineHarnessProvider,
    DisabledExperienceStore,
    ListTraceSink,
)
from app.models import AnalysisPlan, AnalysisRequest, AnalysisResponse, PlannerDecision
from app.providers import GenerationClient, GenerationFailure, OpenAIGenerationClient
from app.service import AnalysisService
from app.sources import content_hash, source_query, utc_now
from app.storage import StateConflict


class RunState(TypedDict, total=False):
    request: dict
    session: dict
    session_id: str
    session_version: int
    run_id: str
    deadline_epoch: float
    calls: int
    decision: dict | None
    plan: dict | None
    response: dict | None
    repair_errors: list[str]
    trace: list[dict]


class AgentRunner:
    def __init__(
        self, settings: Settings, service: AnalysisService, generator: GenerationClient | None = None
    ):
        self.settings, self.service = settings, service
        self.generator = generator or OpenAIGenerationClient(settings)
        self.context = ContextBuilder(settings)
        self.harness = BaselineHarnessProvider().load(settings.harness_version)
        self.experiences = DisabledExperienceStore()
        self.policy = BaselineDecisionPolicy()
        self.connection = sqlite3.connect(
            settings.data_directory / "checkpoints.sqlite3", check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self.connection)
        self.checkpointer.setup()
        self.session_locks: dict[str, threading.Lock] = {}
        self.locks_guard = threading.Lock()
        source_root = Path(__file__).resolve().parent
        self.code_hash = content_hash(
            {
                str(path.relative_to(source_root)): path.read_text(encoding="utf-8")
                for path in sorted(source_root.rglob("*.py"))
            }
        )
        workflow = StateGraph(RunState)
        workflow.add_node("plan", self._plan)
        workflow.add_node("validate", self._validate)
        workflow.add_node("execute", self._execute)
        workflow.add_edge(START, "plan")
        workflow.add_edge("plan", "validate")
        workflow.add_conditional_edges(
            "validate", self._route, {"repair": "plan", "execute": "execute", "done": END}
        )
        workflow.add_edge("execute", END)
        self.graph = workflow.compile(checkpointer=self.checkpointer)

    def close(self):
        if hasattr(self.generator, "close"):
            self.generator.close()
        self.connection.close()

    def _lock(self, session_id: str):
        with self.locks_guard:
            return self.session_locks.setdefault(session_id, threading.Lock())

    @staticmethod
    def _deadline(state: RunState) -> float:
        return time.monotonic() + max(0, state["deadline_epoch"] - time.time())

    @staticmethod
    def _response(state: RunState, status: str, message: str, questions: list[str] | None = None):
        return AnalysisResponse(
            status=status,
            run_id=state["run_id"],
            session_id=state["session_id"],
            message=message,
            questions=questions or [],
        ).model_dump(mode="json")

    def _plan(self, state: RunState) -> dict:
        trace = list(state["trace"])
        if state["calls"] >= self.settings.max_model_calls or time.time() >= state["deadline_epoch"]:
            return {
                "response": self._response(state, "failed", "Planning budget exhausted."),
                "decision": None,
            }
        request = AnalysisRequest.model_validate(state["request"])
        package = self.context.build(
            "plan_repair" if state["repair_errors"] else "planner",
            request,
            state["session"],
            self.harness,
            state["repair_errors"],
        )
        calls = state["calls"] + 1
        sink = ListTraceSink(trace)
        sink.record(
            {
                "stage": "planner_request",
                "at": utc_now(),
                "call": calls,
                "harness": self.harness.version,
                "context": package,
            }
        )
        # Persist an attempt before calling an external provider. A crash may still leave its usage unknown.
        self.service.store.record_trace(state["run_id"], trace)
        try:
            decision, metadata = self.generator.generate(package, self._deadline(state))
            sink.record(
                {
                    "stage": "planner_response",
                    "at": utc_now(),
                    "call": calls,
                    "decision": decision.model_dump(),
                    "metadata": metadata,
                }
            )
            return {"decision": decision.model_dump(), "calls": calls, "trace": trace, "response": None}
        except GenerationFailure as error:
            sink.record(
                {
                    "stage": "planner_failure",
                    "at": utc_now(),
                    "call": calls,
                    "error_type": error.kind,
                    "usage_unknown": True,
                }
            )
            response = (
                None
                if error.repairable and calls < self.settings.max_model_calls
                else self._response(state, "failed", f"Planner failed ({error.kind}).")
            )
            return {
                "decision": None,
                "calls": calls,
                "trace": trace,
                "repair_errors": error.errors,
                "response": response,
            }

    @staticmethod
    def _explicit_conflicts(request: AnalysisRequest, plan: AnalysisPlan) -> list[str]:
        if request.filters is None:
            return []
        explicit = request.filters.model_dump(exclude_unset=True)
        conflicts = []
        for field, value in explicit.items():
            if field == "interventions":
                proposed = {
                    item.casefold() for cohort in plan.cohorts for item in cohort.filters.interventions
                }
                if proposed != {item.casefold() for item in value}:
                    conflicts.append(field)
            else:

                def comparable(item):
                    if isinstance(item, str):
                        return item.casefold()
                    if isinstance(item, list):
                        return sorted(comparable(v) for v in item)
                    return item

                if any(
                    comparable(getattr(cohort.filters, field)) != comparable(value) for cohort in plan.cohorts
                ):
                    conflicts.append(field)
        return conflicts

    def _validate(self, state: RunState) -> dict:
        if state.get("response") is not None:
            return {}
        if state.get("decision") is None:
            if state["calls"] >= self.settings.max_model_calls:
                return {"response": self._response(state, "failed", "Plan repair budget exhausted.")}
            return {}
        decision = PlannerDecision.model_validate(state["decision"])
        if decision.action == "unsupported":
            return {"response": self._response(state, "unsupported", decision.explanation)}
        if decision.action == "clarify":
            if decision.questions:
                return {
                    "response": self._response(
                        state, "needs_clarification", decision.explanation, decision.questions
                    )
                }
        try:
            previous = state["session"].get("last_successful_plan")
            previous = AnalysisPlan.model_validate(previous) if previous else None
            plan = self.policy.evaluate(decision, previous)
            if plan is None:
                raise ValueError("Execution requires a validated plan")
            request = AnalysisRequest.model_validate(state["request"])
            if decision.mode == "clarification":
                pending = state["session"].get("pending_clarification")
                if not pending:
                    raise ValueError("Clarification mode requires a pending clarification")
                pending_filters = pending["request"].get("filters") or {}
                current_filters = request.filters.model_dump(exclude_unset=True) if request.filters else {}
                merged_request = request.model_dump(exclude_unset=True)
                merged_request["filters"] = {**pending_filters, **current_filters}
                request = AnalysisRequest.model_validate(merged_request)
            conflicts = self._explicit_conflicts(request, plan)
            if conflicts:
                questions = [
                    f"Please resolve the mismatch between explicit filters and interpreted plan: {', '.join(conflicts)}."
                ]
                if request.language == "zh":
                    questions = [
                        f"明确填写的筛选字段与解析计划不一致，请明确这些条件：{', '.join(conflicts)}。"
                    ]
                return {
                    "response": self._response(
                        state, "needs_clarification", "Explicit filter conflict.", questions
                    )
                }
            for cohort in plan.cohorts:
                source_query(cohort.filters)
            return {"plan": plan.model_dump(), "repair_errors": []}
        except ValueError as error:
            if state["calls"] >= self.settings.max_model_calls:
                return {
                    "response": self._response(
                        state, "failed", "Plan validation failed after bounded repair."
                    ),
                    "repair_errors": [str(error)[:1000]],
                }
            return {"plan": None, "repair_errors": [str(error)[:1000]]}

    @staticmethod
    def _route(state: RunState) -> str:
        if state.get("response") is not None:
            return "done"
        return "execute" if state.get("plan") else "repair"

    def _execute(self, state: RunState) -> dict:
        trace = list(state["trace"])
        try:
            plan = AnalysisPlan.model_validate(state["plan"])
            response = self.service.execute_plan(
                plan, state["run_id"], state["session_id"], trace, self._deadline(state)
            )
            return {"response": response.model_dump(mode="json"), "trace": trace}
        except Exception as error:
            trace.append(
                {
                    "stage": "execution_failure",
                    "at": utc_now(),
                    "error_type": type(error).__name__,
                    "error_code": getattr(error, "code", None),
                }
            )
            return {
                "response": self._response(state, "failed", f"Execution failed ({type(error).__name__})."),
                "trace": trace,
            }

    def _persist(self, state: RunState) -> AnalysisResponse:
        response = AnalysisResponse.model_validate(state["response"])
        trace = state["trace"]
        metadata = [event["metadata"] for event in trace if event["stage"] == "planner_response"]
        response.meta.update(
            {
                "model": self.settings.openai_model,
                "provider": "openai",
                "reasoning_effort": self.settings.openai_reasoning_effort,
                "harness_version": self.harness.version,
                "prompt_version": self.harness.prompt_version,
                "field_catalog_version": self.harness.field_catalog_version,
                "code_hash": self.code_hash,
                "python_version": platform.python_version(),
                "model_calls": state["calls"],
                "model_usage": metadata,
                "experiments": {"decision": False, "harness": False},
                "langgraph_thread_id": state["run_id"],
            }
        )
        session = dict(state["session"])
        if response.status in ("ok", "no_data"):
            session.update(
                {
                    "last_successful_plan": state["plan"],
                    "last_result_id": state["run_id"],
                    "active_dataset_ids": [
                        item["dataset_id"] for item in response.provenance.get("datasets", [])
                    ],
                    "pending_clarification": None,
                    "last_run_id": state["run_id"],
                }
            )
        elif response.status == "needs_clarification":
            session.update(
                {
                    "pending_clarification": {
                        "request": state["request"],
                        "questions": response.questions,
                        "proposed_decision": state.get("decision"),
                    },
                    "last_run_id": state["run_id"],
                }
            )
        if response.status in ("ok", "no_data", "needs_clarification"):
            self.service.store.commit_session(state["session_id"], state["session_version"], session)
            response.meta["session_version"] = state["session_version"] + 1
        self.service.store.finish_run(state["run_id"], response.model_dump(mode="json"), trace)
        return response

    def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        session_id = request.session_id or "session_" + uuid4().hex
        with self._lock(session_id):
            session_id, version, session = self.service.store.session(session_id)
            run_id = self.service.store.start_run(
                session_id, request.parent_run_id, request.model_dump(exclude_unset=True)
            )
            if request.parent_run_id:
                parent = self.service.store.run(request.parent_run_id)["response"]
                if parent["status"] not in ("ok", "no_data", "needs_clarification"):
                    raise StateConflict("Parent run must be a completed analysis or clarification")
                if parent["status"] in ("ok", "no_data"):
                    session = {
                        **session,
                        "last_successful_plan": parent["meta"]["plan"],
                        "pending_clarification": None,
                    }
            state: RunState = {
                "request": request.model_dump(exclude_unset=True),
                "session": session,
                "session_id": session_id,
                "session_version": version,
                "run_id": run_id,
                "deadline_epoch": time.time() + self.settings.max_run_seconds,
                "calls": 0,
                "decision": None,
                "plan": None,
                "response": None,
                "repair_errors": [],
                "trace": [
                    {
                        "stage": "start",
                        "at": utc_now(),
                        "session_version": version,
                        "code_hash": self.code_hash,
                    }
                ],
            }
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 20}
            try:
                final = self.graph.invoke(state, config)
                return self._persist(final)
            except StateConflict:
                raise
            except Exception as error:
                current = self.graph.get_state(config).values or state
                current = {**state, **current}
                current["response"] = self._response(
                    current, "failed", f"Run failed ({type(error).__name__})."
                )
                current["trace"].append(
                    {"stage": "run_failure", "at": utc_now(), "error_type": type(error).__name__}
                )
                return self._persist(current)

    def resume(self, run_id: str) -> AnalysisResponse:
        run = self.service.store.run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["response"] is not None:
            return AnalysisResponse.model_validate(run["response"])
        with self._lock(run["session_id"]):
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 20}
            checkpoint = self.graph.get_state(config)
            if not checkpoint.values:
                raise StateConflict("No durable checkpoint exists; submit the request as a new run")
            _, version, _ = self.service.store.session(run["session_id"])
            if version != checkpoint.values["session_version"]:
                raise StateConflict("The session has advanced; resume would overwrite newer state")
            # Do not reset the original hard wall-clock budget on recovery.
            final = self.graph.invoke(None, config)
            return self._persist(final)
