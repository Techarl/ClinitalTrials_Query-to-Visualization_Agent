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
    DecisionPolicy,
    DisabledExperienceStore,
    HarnessProvider,
    ListTraceSink,
    resolve_plan,
)
from app.models import AnalysisPlan, AnalysisRequest, AnalysisResponse, PlannerDecision
from app.providers import GenerationClient, GenerationFailure, OpenAIGenerationClient, usage_is_complete
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
        self,
        settings: Settings,
        service: AnalysisService,
        generator: GenerationClient | None = None,
        *,
        policy: DecisionPolicy | None = None,
        harness_provider: HarnessProvider | None = None,
    ):
        self.settings, self.service = settings, service
        self.generator = generator or OpenAIGenerationClient(settings)
        self.context = ContextBuilder(settings)
        self.harness = (harness_provider or BaselineHarnessProvider()).load(settings.harness_version)
        self.experiences = DisabledExperienceStore()
        self.policy = policy or BaselineDecisionPolicy()
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

    def _accounted_state(self, state: RunState) -> RunState:
        """Reconcile durable requests/outcomes on every planning/finalization path."""
        recorded = self.service.store.run(state["run_id"])
        events = {}
        for event in [
            *(recorded["trace"] if recorded else []),
            *state["trace"],
            *self.service.store.model_journal(state["run_id"]),
        ]:
            event = dict(event)
            key = (
                (event["stage"], event["call"])
                if event["stage"] in ("planner_request", "planner_response", "planner_failure")
                else content_hash(event)
            )
            events[key] = event
        trace = list(events.values())
        requests = {e["call"]: e for e in trace if e["stage"] == "planner_request"}
        outcomes = {e["call"] for e in trace if e["stage"] in ("planner_response", "planner_failure")}
        for event in trace:
            if event["stage"] == "planner_response":
                metadata = event.get("metadata")
                event["usage_unknown"] = not usage_is_complete(
                    metadata.get("usage") if isinstance(metadata, dict) else None
                )
        marked = {e.get("call") for e in trace if e["stage"] == "recovered_unknown_model_outcome"}
        for call, request in requests.items():
            if call not in outcomes and call not in marked:
                trace.append(
                    {
                        "stage": "recovered_unknown_model_outcome",
                        "at": request["at"],
                        "call": call,
                        "usage_unknown": True,
                    }
                )
        trace.sort(key=lambda e: e.get("at", ""))
        return {**state, "calls": max(state["calls"], max(requests, default=0)), "trace": trace}

    def _plan(self, state: RunState) -> dict:
        checkpoint_calls = state["calls"]
        state = self._accounted_state(state)
        trace = list(state["trace"])
        attempted = state["calls"]
        if attempted > checkpoint_calls:
            # A provider outcome committed before a checkpoint-write crash is
            # reusable. Do not pay again or discard a known valid decision.
            outcome = next(
                (
                    e
                    for e in reversed(trace)
                    if e.get("call") == attempted and e["stage"] in ("planner_response", "planner_failure")
                ),
                None,
            )
            if outcome and outcome["stage"] == "planner_response":
                return {"decision": outcome["decision"], "calls": attempted, "trace": trace, "response": None}
            if outcome and not outcome.get("repairable", False):
                return {
                    "decision": None,
                    "calls": attempted,
                    "trace": trace,
                    "response": self._response(state, "failed", f"Planner failed ({outcome['error_type']})."),
                }
            if outcome:
                return {
                    "decision": None,
                    "calls": attempted,
                    "trace": trace,
                    "response": None,
                    "repair_errors": outcome.get("errors", []),
                }
        if attempted >= self.settings.max_model_calls or time.time() >= state["deadline_epoch"]:
            return {
                "response": self._response(state, "failed", "Planning budget exhausted."),
                "decision": None,
                "calls": attempted,
                "trace": trace,
            }
        request = AnalysisRequest.model_validate(state["request"])
        package = self.context.build(
            "plan_repair" if state["repair_errors"] else "planner",
            request,
            state["session"],
            self.harness,
            state["repair_errors"],
        )
        calls = attempted + 1
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
        self.service.store.record_model_event(state["run_id"], trace[-1], trace)
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
            self.service.store.record_model_event(state["run_id"], trace[-1], trace)
            return {"decision": decision.model_dump(), "calls": calls, "trace": trace, "response": None}
        except GenerationFailure as error:
            sink.record(
                {
                    "stage": "planner_failure",
                    "at": utc_now(),
                    "call": calls,
                    "error_type": error.kind,
                    "repairable": error.repairable,
                    "errors": error.errors,
                    "usage_unknown": True,
                }
            )
            self.service.store.record_model_event(state["run_id"], trace[-1], trace)
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
        except Exception as error:
            # Includes SDK JSON/shape errors and unexpected provider exceptions.
            # Preserve the attempt and expose only an error type, never its body.
            sink.record(
                {
                    "stage": "planner_failure",
                    "at": utc_now(),
                    "call": calls,
                    "error_type": type(error).__name__,
                    "usage_unknown": True,
                }
            )
            self.service.store.record_model_event(state["run_id"], trace[-1], trace)
            return {
                "decision": None,
                "calls": calls,
                "trace": trace,
                "response": self._response(state, "failed", f"Planner failed ({type(error).__name__})."),
            }

    @staticmethod
    def _explicit_conflicts(request: AnalysisRequest, plan: AnalysisPlan) -> list[str]:
        if request.filters is None:
            return []
        explicit = request.filters.model_dump(exclude_unset=True)
        conflicts = []
        for field, value in explicit.items():
            if field == "interventions":
                required = {item.casefold() for item in value}
                groups = [{item.casefold() for item in c.filters.interventions} for c in plan.cohorts]
                comparison = request.intervention_scope == "comparison"
                if comparison:
                    valid = (
                        request.filters.intervention_match == "any"
                        and len(groups) == len(required)
                        and all(len(group) == 1 for group in groups)
                        and set().union(*groups) == required
                    )
                else:
                    valid = all(group == required for group in groups)
                if not valid:
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
        decision = self.policy.evaluate(decision, {"request": state["request"], "session": state["session"]})
        decision = PlannerDecision.model_validate(decision)
        if decision.action == "unsupported":
            return {
                "response": self._response(state, "unsupported", decision.explanation),
                "decision": decision.model_dump(),
            }
        if decision.action == "clarify":
            if decision.questions:
                return {
                    "response": self._response(
                        state, "needs_clarification", decision.explanation, decision.questions
                    ),
                    "decision": decision.model_dump(),
                }
        try:
            previous = state["session"].get("last_successful_plan")
            previous = AnalysisPlan.model_validate(previous) if previous else None
            plan = resolve_plan(decision, previous)
            if plan is None:
                raise ValueError("Execution requires a validated plan")
            request = AnalysisRequest.model_validate(state["request"])
            if decision.mode == "clarification":
                pending = state["session"].get("pending_clarification")
                if not pending:
                    raise ValueError("Clarification mode requires a pending clarification")
                pending_filters = pending.get("confirmed_filters", pending["request"].get("filters") or {})
                current_filters = request.filters.model_dump(exclude_unset=True) if request.filters else {}
                merged_request = request.model_dump(exclude_unset=True)
                merged_request["filters"] = {**pending_filters, **current_filters}
                merged_request["intervention_scope"] = self._clarification_scope(request, pending)
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
            return {"plan": plan.model_dump(), "repair_errors": [], "decision": decision.model_dump()}
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
    def _clarification_scope(request: AnalysisRequest, pending: dict) -> str:
        if "intervention_scope" in request.model_fields_set:
            return request.intervention_scope
        return pending.get(
            "confirmed_intervention_scope", pending["request"].get("intervention_scope", "population")
        )

    @staticmethod
    def _route(state: RunState) -> str:
        if state.get("response") is not None:
            return "done"
        return "execute" if state.get("plan") else "repair"

    def _execute(self, state: RunState) -> dict:
        trace = list(state["trace"])
        try:
            if time.time() >= state["deadline_epoch"]:
                raise TimeoutError("Original run deadline expired")
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
        state = self._accounted_state(state)
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
                "usage_complete": not any(event.get("usage_unknown") for event in trace),
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
            decision = state.get("decision") or {}
            prior = session.get("pending_clarification")
            continuing = prior and decision.get("mode") == "clarification"
            pending = (
                dict(prior)
                if continuing
                else {"request": state["request"], "replies": [], "confirmed_filters": {}}
            )
            pending["confirmed_filters"] = {
                **pending.get("confirmed_filters", pending["request"].get("filters") or {}),
                **(state["request"].get("filters") or {}),
            }
            pending["confirmed_intervention_scope"] = self._clarification_scope(
                AnalysisRequest.model_validate(state["request"]), pending
            )
            if continuing:
                pending["replies"] = [*pending.get("replies", []), state["request"]]
            pending.update(questions=response.questions, proposed_decision=state.get("decision"))
            session.update(
                {
                    "pending_clarification": pending,
                    "last_run_id": state["run_id"],
                }
            )
        if response.status in ("ok", "no_data", "needs_clarification"):
            response.meta["session_version"] = state["session_version"] + 1
        completed = self.service.store.finalize_run(
            state["run_id"],
            state["session_version"],
            session,
            response.model_dump(mode="json"),
            trace,
            advance_session=response.status in ("ok", "no_data", "needs_clarification"),
        )
        return AnalysisResponse.model_validate(completed)

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
                    session = self.service.store.run_session(request.parent_run_id) or {
                        **session,
                        "last_successful_plan": parent["meta"]["plan"],
                        "pending_clarification": None,
                    }
                else:
                    branch = self.service.store.run_session(request.parent_run_id)
                    if branch is None:
                        # Legacy completed runs retain their pre-run state in LangGraph.
                        saved = self.graph.get_state({"configurable": {"thread_id": request.parent_run_id}})
                        if not saved.values:
                            raise StateConflict("Historical clarification has no saved branch state")
                        branch = dict(saved.values["session"])
                        branch["pending_clarification"] = {
                            "request": self.service.store.run(request.parent_run_id)["request"],
                            "questions": parent["questions"],
                            "replies": [],
                        }
                    session = branch
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
            run = self.service.store.run(run_id)
            if run["response"] is not None:
                return AnalysisResponse.model_validate(run["response"])
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 20}
            checkpoint = self.graph.get_state(config)
            if not checkpoint.values:
                raise StateConflict("No durable checkpoint exists; submit the request as a new run")
            _, version, _ = self.service.store.session(run["session_id"])
            if version != checkpoint.values["session_version"]:
                raise StateConflict("The session has advanced; resume would overwrite newer state")
            # Do not reset the original hard wall-clock budget on recovery.
            try:
                final = self.graph.invoke(None, config)
                return self._persist(final)
            except StateConflict:
                raise
            except Exception as error:
                current = {**checkpoint.values, **self.graph.get_state(config).values}
                current["response"] = self._response(
                    current, "failed", f"Run failed ({type(error).__name__})."
                )
                current["trace"] = [
                    *current["trace"],
                    {"stage": "run_failure", "at": utc_now(), "error_type": type(error).__name__},
                ]
                return self._persist(current)
