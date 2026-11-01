"""Bounded native-tool loop with durable decisions, tool observations and recovery."""

import json
import sqlite3
import threading
import time
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from openai import OpenAI

from app.providers import usage_is_complete
from app.sources import content_hash
from app.v2.context import pack
from app.v2.contracts import ClarifyInput, FinalizeInput, HandleInput, RunResult, StopInput, TaskSpec
from app.v2.store import TERMINAL

CONTROLS = {
    "define_task": (
        TaskSpec,
        "Define or repair the typed task, its confirmed constraints and every requested output goal before data tools.",
    ),
    "clarify_user": (
        ClarifyInput,
        "Ask specific missing information or entity identity questions; preserve pending state.",
    ),
    "finalize_analysis": (
        FinalizeInput,
        "Publish validated result handles; unmet goals cause partial status, never complete.",
    ),
    "stop_with_limit": (
        StopInput,
        "Stop with a concrete unsupported capability, source, budget or no-progress limitation.",
    ),
}


class NativePlanner:
    def __init__(self, settings):
        self.settings = settings
        self.client = None

    def close(self):
        if self.client:
            self.client.close()

    def choose(self, context, definitions, deadline):
        if self.client is None:
            self.client = OpenAI(
                api_key=self.settings.openai_api_key.get_secret_value(),
                base_url=str(self.settings.openai_base_url),
                max_retries=0,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Model deadline exhausted")
        started = time.monotonic()
        response = self.client.responses.create(
            model=self.settings.openai_model,
            input=[
                {"role": "system", "content": context["system"]},
                {"role": "user", "content": context["payload"]},
            ],
            tools=[
                {
                    "type": "function",
                    "name": item["name"],
                    "description": item["description"],
                    "parameters": item["inputSchema"],
                    "strict": False,
                }
                for item in definitions
            ],
            tool_choice="required",
            parallel_tool_calls=False,
            reasoning={"effort": self.settings.openai_reasoning_effort},
            max_output_tokens=4096,
            timeout=min(remaining, self.settings.openai_timeout_seconds),
            store=False,
        )
        metadata = {
            "provider": "openai",
            "requested_model": self.settings.openai_model,
            "reported_model": response.model,
            "response_id": response.id,
            "usage": response.usage.model_dump() if response.usage else None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        calls = [item for item in response.output if item.type == "function_call"]
        if len(calls) != 1:
            return None, {**metadata, "error": "Expected one function call"}
        try:
            arguments = json.loads(calls[0].arguments)
        except (ValueError, TypeError):
            return None, {**metadata, "error": "Invalid function arguments"}
        return {"name": calls[0].name, "arguments": arguments}, metadata


class LoopState(TypedDict):
    run_id: str


def usage_report(store, rid):
    entries = [e for e in store.journal_entries(rid) if e["kind"] == "model"]
    total, unknown, attempts = 0.0, 0, 0
    receipts = []
    for entry in entries:
        reservation = entry["input"]["reservation_usd"]
        metadata = (entry["output"] or {}).get("metadata", {})
        usage = metadata.get("usage")
        previous_unknown = entry["attempts"] - 1
        attempts += entry["attempts"]
        unknown += previous_unknown
        total += reservation * previous_unknown
        if usage_is_complete(usage):
            details = usage["input_tokens_details"]
            ordinary = usage["input_tokens"] - details["cached_tokens"] - details["cache_write_tokens"]
            cost = (
                ordinary * 2
                + details["cached_tokens"] * 0.1
                + details["cache_write_tokens"] * 2.5
                + usage["output_tokens"] * 10
            ) / 1000000
            total += cost
        else:
            unknown += 1
            total += reservation
        receipts.append(metadata)
    return {
        "model_calls": attempts,
        "usage_complete": unknown == 0,
        "unknown_attempts": unknown,
        "estimated_or_reserved_usd": round(total, 8),
        "receipts": receipts,
        "cost_basis": "GPT-6.1 Sol standard list-rate estimate; unknown attempts keep byte-based reservations, not an invoice",
    }


class V2Agent:
    def __init__(self, settings, registry, planner=None, context_policy="relevant", skill=""):
        self.settings, self.registry, self.store = settings, registry, registry.store
        self.planner = planner or NativePlanner(settings)
        self.context_policy, self.skill = context_policy, skill
        self.locks, self.lock_guard = {}, threading.Lock()
        self.checkpoint_connection = sqlite3.connect(
            settings.data_directory / "v2-checkpoints.sqlite3", check_same_thread=False
        )
        graph = StateGraph(LoopState)
        graph.add_node("choose_action", self.choose)
        graph.add_node("execute_and_observe", self.execute)
        graph.add_edge(START, "choose_action")
        graph.add_conditional_edges(
            "choose_action", self.route_choose, {"execute": "execute_and_observe", "done": END}
        )
        graph.add_conditional_edges(
            "execute_and_observe", self.route_execute, {"continue": "choose_action", "done": END}
        )
        self.graph = graph.compile(checkpointer=SqliteSaver(self.checkpoint_connection))

    def close(self):
        if hasattr(self.planner, "close"):
            self.planner.close()
        self.checkpoint_connection.close()

    def definitions(self):
        return self.registry.descriptions() + [
            {"name": name, "description": description, "inputSchema": schema.model_json_schema()}
            for name, (schema, description) in CONTROLS.items()
        ]

    def run(self, rid):
        with self.run_lock(rid):
            return self._run(rid)

    def run_lock(self, rid):
        with self.lock_guard:
            return self.locks.setdefault(rid, threading.RLock())

    def _run(self, rid):
        state = self.store.run(rid)
        if state["status"] in TERMINAL:
            return self.result(rid)
        state["status"] = "running"
        self.store.save(state)
        self.store.event(rid, "run_started", {"resuming": bool(state.get("observations"))})
        try:
            self.graph.invoke({"run_id": rid}, {"configurable": {"thread_id": rid}, "recursion_limit": 150})
        except Exception as error:
            state = self.store.run(rid)
            if state["status"] not in TERMINAL:
                self.finish(
                    state,
                    "failed",
                    f"Run failed ({type(error).__name__}); durable attempts and artifacts are retained.",
                )
        return self.result(rid)

    def choose(self, graph_state):
        state = self.store.run(graph_state["run_id"])
        if state["status"] in TERMINAL:
            return {}
        if state["cancelled"]:
            self.finish(state, "cancelled", "Cancelled at a safe execution boundary.")
            return {}
        if time.time() >= state["deadline_epoch"]:
            self.finish(state, "limited", "The original run deadline is exhausted.")
            return {}
        if state.get("current_action"):
            return {}
        usage = usage_report(self.store, state["run_id"])
        state["remaining_steps"] = self.settings.v2_max_model_calls - usage["model_calls"]
        if state["remaining_steps"] <= 0:
            self.finish(state, "limited", "Model-call budget exhausted.")
            return {}
        context = pack(state, self.settings.v2_context_chars, self.context_policy, self.skill)
        definitions = self.definitions()
        context_bytes = len((context["system"] + context["payload"] + json.dumps(definitions)).encode())
        reservation = (context_bytes * 2.5 + 4096 * 10) / 1000000
        if usage["estimated_or_reserved_usd"] + reservation > self.settings.v2_max_estimated_usd:
            self.finish(state, "limited", "Estimated model budget exhausted; unknown usage stays reserved.")
            return {}
        signature = "model:" + str(state.get("decision_index", 0))
        journal = self.store.journal(state["run_id"], signature)
        if journal and journal["status"] == "completed":
            outcome = journal["output"]
        else:
            self.store.begin(
                state["run_id"],
                signature,
                "model",
                {"context": context, "reservation_usd": reservation, "model": self.settings.openai_model},
            )
            try:
                action, metadata = self.planner.choose(context, definitions, self.registry.deadline(state))
                outcome = {"action": action, "metadata": metadata}
            except Exception as error:
                outcome = {"action": None, "metadata": {"error_type": type(error).__name__, "usage": None}}
            self.store.complete(state["run_id"], signature, outcome)
        self.store.event(state["run_id"], "usage_updated", usage_report(self.store, state["run_id"]))
        if not outcome["action"]:
            self.finish(
                state, "failed", "The planner did not return a valid action; call accounting is retained."
            )
            return {}
        state["current_action"] = outcome["action"]
        self.store.save(state)
        self.store.event(
            state["run_id"],
            "action_selected",
            {"action": outcome["action"]["name"], "context_packing": context["packing"]},
        )
        return {}

    def route_choose(self, graph_state):
        return "done" if self.store.run(graph_state["run_id"])["status"] in TERMINAL else "execute"

    def route_execute(self, graph_state):
        return "done" if self.store.run(graph_state["run_id"])["status"] in TERMINAL else "continue"

    def define_task(self, task, state):
        request = state["request"]
        prior_task = (state.get("prior") or {}).get("task")
        proposed = task.model_dump(mode="json")
        if task.mode in ("followup", "clarification") and prior_task:
            # Omitted values inherit; explicit clear_fields remove prior values.
            fields = task.filters.model_fields_set
            filters = dict(prior_task["filters"])
            for key in task.clear_fields:
                if key in filters:
                    filters[key] = (
                        []
                        if isinstance(filters[key], list)
                        else "any"
                        if key == "intervention_match"
                        else None
                    )
            filters.update({key: proposed["filters"][key] for key in fields})
            proposed["filters"] = filters
            if "date_range" not in task.model_fields_set and "date_range" not in task.clear_fields:
                proposed["date_range"] = prior_task.get("date_range")
            if "intervention_scope" not in task.model_fields_set:
                proposed["intervention_scope"] = prior_task.get("intervention_scope", "population")
        for key, value in (request.get("filters") or {}).items():
            if proposed["filters"].get(key) != value:
                raise ValueError(f"Plan must preserve this turn's explicit filter: {key}")
        for key in ("date_range", "intervention_scope"):
            if request.get(key) is not None and proposed.get(key) != request[key]:
                raise ValueError(f"Plan must preserve this turn's explicit {key}")
        if proposed["source_mode"] != request.get("source_mode", "full_evidence"):
            raise ValueError("Official-count overview requires explicit request permission")
        if state.get("task"):
            for key in ("filters", "date_range", "intervention_scope", "source_mode"):
                if proposed[key] != state["task"][key]:
                    raise ValueError("Repair cannot change already-confirmed task scope within a run")
        validated = TaskSpec.model_validate(proposed)
        state["task"] = validated.model_dump(mode="json")
        state["constraints_version"] += 1
        return {
            "tool": "define_task",
            "status": "ok",
            "task": state["task"],
            "constraint_version": state["constraints_version"],
        }

    def execute(self, graph_state):
        state = self.store.run(graph_state["run_id"])
        action = state["current_action"]
        name, arguments = action["name"], action["arguments"]
        try:
            if name in CONTROLS:
                args = CONTROLS[name][0].model_validate(arguments)
                if name == "define_task":
                    observation = self.define_task(args, state)
                elif name == "clarify_user":
                    state["questions"] = args.questions
                    state["missing_fields"] = args.missing_fields
                    self.finish(
                        state, "needs_clarification", "Please resolve the specific questions to continue."
                    )
                    return {}
                elif name == "stop_with_limit":
                    self.finish(state, "limited", args.reason)
                    return {}
                else:
                    state["result_ids"] = list(dict.fromkeys([*state["result_ids"], *args.result_ids]))
                    state["finalization_note"] = args.message
                    self.finish(
                        state,
                        "completed",
                        "Results are assembled from validated artifacts. See each chart for its scope, metric, exclusions and source evidence.",
                    )
                    return {}
            else:
                observation = self.registry.call(name, arguments, state)
                if observation.get("status") == "ok" and observation.get("result_id"):
                    state["result_ids"] = list(
                        dict.fromkeys([*state["result_ids"], observation["result_id"]])
                    )
            state["observations"].append(observation)
            repeated = sum(
                content_hash({k: v for k, v in item.items() if k not in ("elapsed_seconds", "reused")})
                == content_hash(
                    {k: v for k, v in observation.items() if k not in ("elapsed_seconds", "reused")}
                )
                for item in state["observations"]
            )
            if repeated >= 3:
                self.finish(state, "limited", "Repeated actions produced no new information.")
                return {}
        except (ValueError, KeyError) as error:
            state["observations"].append(
                {
                    "tool": name,
                    "status": "error",
                    "message": str(error)[:600],
                    "next_action": "Repair the action without weakening the user constraints.",
                }
            )
        state["decision_index"] = state.get("decision_index", 0) + 1
        state["current_action"] = None
        self.store.save(state)
        self.store.event(state["run_id"], "observation", state["observations"][-1])
        if state["observations"][-1].get("status") == "ok" and state["observations"][-1].get("result_id"):
            self.store.event(
                state["run_id"], "artifact_ready", {"result_id": state["observations"][-1]["result_id"]}
            )
        return {}

    def finish(self, state, status, message):
        valid, completed_goals = [], set()
        failures = []
        for handle in state.get("result_ids", []):
            try:
                self.registry.validate(HandleInput(handle=handle), state)
                artifact = self.registry.artifact(handle, state)
                valid.append(handle)
            except (ValueError, KeyError, TimeoutError) as error:
                failures.append(type(error).__name__)
        state["result_ids"] = valid
        goals = (state.get("task") or {}).get("goals", [])
        by_goal = {goal["id"]: [] for goal in goals}
        for handle in valid:
            artifact = self.registry.artifact(handle, state)
            by_goal.get(artifact.goal_id, []).append(artifact)
        for goal in goals:
            for artifact in by_goal[goal["id"]]:
                dependencies = {
                    self.registry.artifact(h, state).goal_id for h in artifact.dependencies if h in valid
                }
                if set(goal["depends_on"]) <= dependencies & completed_goals:
                    completed_goals.add(goal["id"])
                    break
        unmet = [goal["id"] for goal in goals if goal["id"] not in completed_goals]
        if status == "completed" and (unmet or not goals or failures):
            status = "partial" if valid else "limited"
            message = (message + " Some declared goals are not satisfied.").strip()
        elif status in ("failed", "limited") and valid:
            status = "partial"
        state.update(status=status, message=message, unmet_goals=unmet, current_action=None)
        state["usage"] = usage_report(self.store, state["run_id"])
        self.store.save(state, finish=True)

    def result(self, rid):
        state = self.store.run(rid)
        return RunResult(
            run_id=rid,
            session_id=state["session_id"],
            status=state["status"],
            task=state.get("task"),
            artifacts=[self.registry.artifact(handle, state) for handle in state.get("result_ids", [])],
            questions=state.get("questions", []),
            message=state.get("message", ""),
            unmet_goals=state.get("unmet_goals", []),
            usage=usage_report(self.store, rid),
        )
