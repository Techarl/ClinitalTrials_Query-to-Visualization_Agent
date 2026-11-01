"""Bounded native-tool loop with durable decisions, tool observations and recovery."""

import json
import re
import sqlite3
import threading
import time
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from openai import OpenAI

from app.sources import content_hash
from app.v2.context import pack
from app.v2.contracts import ClarifyInput, FinalizeInput, HandleInput, RunResult, StopInput, TaskSpec
from app.v2.costs import estimate
from app.v2.store import TERMINAL
from app.v2.tools import task_execution_hash

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


def _quoted_value(value, quote, field):
    """Conservative lexical evidence, not a claim of general semantic entailment."""
    if isinstance(value, int) and not isinstance(value, bool):
        return bool(re.search(rf"(?<!\w){value}(?!\w)", quote))
    if not isinstance(value, str) or not value:
        return False
    aliases = {
        ("intervention_scope", "comparison"): ("compare", "comparison", "separate cohorts", "separate groups"),
        ("intervention_scope", "population"): ("single population", "combined population", "pooled population"),
        ("filters.intervention_match", "all"): ("both drugs", "both medicines", "all interventions", "together"),
        ("filters.intervention_match", "any"): ("either drug", "either medicine", "any intervention"),
        ("date_range.field", "first_posted"): ("first posted", "first posting"),
        ("date_range.field", "primary_completion"): ("primary completion",),
    }
    candidates = aliases.get((field, value), (value,))
    if field == "filters.phases":
        normalized = {"EARLY_PHASE1": "early phase 1", "PHASE1": "phase 1", "PHASE2": "phase 2",
                      "PHASE3": "phase 3", "PHASE4": "phase 4", "NA": "not applicable"}
        candidates = (value, normalized.get(value, value))
    for candidate in candidates:
        words = re.split(r"[\s_]+", candidate.strip())
        pattern = r"(?<!\w)" + r"[\s_]+".join(re.escape(word) for word in words) + r"(?!\w)"
        if re.search(pattern, quote, re.IGNORECASE):
            return True
    return False


def _quoted_clear(field, quote):
    phrases = {
        "filters.condition": ("all conditions", "any disease", "no disease restriction"),
        "filters.interventions": ("all interventions", "no drug restriction", "no intervention restriction"),
        "filters.sponsor": ("all sponsors", "any sponsor", "no sponsor restriction"),
        "filters.phases": ("all phases", "any phase", "no phase restriction"),
        "filters.statuses": ("all statuses", "any status", "no status restriction"),
        "filters.countries": ("all countries", "any country", "worldwide", "no country restriction"),
        "filters.start_year": ("no lower year bound", "no start year restriction", "all start years"),
        "filters.end_year": ("no upper year bound", "no end year restriction", "all start years"),
        "filters.enrollment_type": ("any enrollment type", "all enrollment types", "actual and estimated enrollment"),
        "date_range": ("all dates", "no date restriction", "no date restrictions", "without date restrictions"),
        "date_range.lower": ("no lower date bound", "no lower bound", "without a lower date bound"),
        "date_range.upper": ("no upper date bound", "no upper bound", "without an upper date bound"),
    }
    return any(_quoted_value(phrase, quote, field) for phrase in phrases.get(field, ()))


def _supports_scope_correction(field, before, after, quote, utterance):
    if not quote or quote not in utterance:
        return False
    if after is None or after == []:
        return _quoted_clear(field, quote)
    if field == "date_range":
        if not isinstance(after, dict):
            return False
        previous = before or {}
        changed = [key for key in ("field", "lower", "upper") if previous.get(key) != after.get(key)]
        return bool(changed) and all(
            _quoted_clear("date_range." + key, quote) if after.get(key) is None
            else _quoted_value(after[key], quote, "date_range." + key)
            for key in changed
        )
    if isinstance(after, list):
        if not all(_quoted_value(value, quote, field) for value in after):
            return False
        dropped = [value for value in before or [] if value not in after]
        # A model may remove its own invented list entries. If the user did mention
        # them, require an explicit restriction/removal phrase instead of just quoting A.
        mentioned = [value for value in dropped if _quoted_value(value, utterance, field)]
        if mentioned:
            # Merely quoting 'only A' from 'only A and B' must not remove B.
            return all(re.search(r"\b(?:exclude|remove|drop|without)\s+(?:the\s+)?"
                                 + re.escape(str(value)) + r"(?!\w)", quote, re.IGNORECASE)
                       for value in mentioned)
        return True
    return _quoted_value(after, quote, field)


def usage_report(store, rid):
    entries = [e for e in store.journal_entries(rid) if e["kind"] == "model"]
    total, unknown, attempts = 0.0, 0, 0
    receipts = []
    for entry in entries:
        reservation = entry["input"]["reservation_usd"]
        metadata = (entry["output"] or {}).get("metadata", {})
        previous_unknown = entry["attempts"] - 1
        attempts += entry["attempts"]
        unknown += previous_unknown
        total += reservation * previous_unknown
        cost = estimate(metadata)
        if cost is not None:
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
        "cost_basis": "Versioned standard short-context model rates or explicit provider usage.cost; unknown attempts keep conservative reservations, not an invoice",
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
        signature = "model:" + str(state.get("decision_index", 0))
        journal = self.store.journal(state["run_id"], signature)
        if journal and journal["status"] == "completed":
            # A paid, durable decision may survive a crash at the last allowed call.
            outcome = journal["output"]
            if not outcome.get("action"):
                self.finish(state, "failed", "The recorded planner attempt did not return an action.")
            else:
                state["current_action"] = outcome["action"]
                self.store.save(state)
                self.store.event(
                    state["run_id"],
                    "action_selected",
                    {
                        "action": outcome["action"]["name"],
                        "replayed": True,
                    },
                )
            return {}
        usage = usage_report(self.store, state["run_id"])
        state["remaining_steps"] = self.settings.v2_max_model_calls - usage["model_calls"]
        if state["remaining_steps"] <= 0:
            self.finish(state, "limited", "Model-call budget exhausted.")
            return {}
        context = pack(state, self.settings.v2_context_chars, self.context_policy, self.skill)
        context["run_id"] = state["run_id"]
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
        latest = self.store.run(state["run_id"])
        if latest["cancelled"] or time.time() >= latest["deadline_epoch"]:
            self.finish(
                latest,
                "cancelled" if latest["cancelled"] else "limited",
                "Execution stopped after the planner returned; its usage is retained.",
            )
            return {}
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
            if (
                "intervention_scope" not in task.model_fields_set
                and "intervention_scope" not in task.clear_fields
            ):
                proposed["intervention_scope"] = prior_task.get("intervention_scope", "population")
        for key, value in (request.get("filters") or {}).items():
            if proposed["filters"].get(key) != value:
                raise ValueError(f"Plan must preserve this turn's explicit filter: {key}")
        for key in ("date_range", "intervention_scope"):
            if key in request and (key == "date_range" or request[key] is not None) and proposed.get(key) != request[key]:
                raise ValueError(f"Plan must preserve this turn's explicit {key}")
        if proposed["source_mode"] != request.get("source_mode", "full_evidence"):
            raise ValueError("Official-count overview requires explicit request permission")
        scope_changed = False
        if state.get("task"):
            if not {g["id"] for g in state["task"]["goals"]} <= {g["id"] for g in proposed["goals"]}:
                raise ValueError("Repair cannot drop already-declared task goals")
            for key in ("filters", "date_range", "intervention_scope", "source_mode"):
                if proposed[key] != state["task"][key]:
                    fields = (
                        [f"filters.{k}" for k in proposed[key] if proposed[key][k] != state["task"][key][k]]
                        if key == "filters"
                        else [key]
                    )
                    for field in fields:
                        correction = next((c for c in task.corrections if c.field == field), None)
                        utterance = request["query"]
                        value = (
                            proposed["filters"][field.split(".")[1]]
                            if field.startswith("filters.")
                            else proposed[key]
                        )
                        previous = (
                            state["task"]["filters"][field.split(".")[1]]
                            if field.startswith("filters.") else state["task"][key]
                        )
                        if not correction or not _supports_scope_correction(
                            field, previous, value, correction.request_quote, utterance
                        ):
                            raise ValueError(
                                "Scope repair requires a verbatim original-request quote supporting changed values or an explicit clear phrase; structured user fields remain binding"
                            )
                    scope_changed = True
        validated = TaskSpec.model_validate(proposed)
        execution_changed = bool(state.get("task")) and task_execution_hash(state["task"]) != task_execution_hash(validated)
        if scope_changed or execution_changed:
            state.setdefault("scope_revisions", []).append(
                {
                    "previous_task": state["task"],
                    "revision_reason": "scope_changed" if scope_changed else "goal_contract_changed",
                    "previous_version": state["constraints_version"],
                    "corrections": [c.model_dump() for c in task.corrections],
                    "superseded_result_ids": list(state["result_ids"]),
                }
            )
            state["result_ids"] = []
        state["task"] = validated.model_dump(mode="json")
        if execution_changed or not state["constraints_version"]:
            state["constraints_version"] += 1
        state["constraint_provenance"] = {
            "structured_user_fields": request.get("filters", {}),
            "natural_language_request": request["query"],
            "interpretation": "model_inference_with_quoted_repairs",
        }
        return {
            "tool": "define_task",
            "status": "ok",
            "task": state["task"],
            "constraint_version": state["constraints_version"],
        }

    def execute(self, graph_state):
        state = self.store.run(graph_state["run_id"])
        if state["status"] in TERMINAL:
            return {}
        if state["cancelled"] or time.time() >= state["deadline_epoch"]:
            self.finish(
                state,
                "cancelled" if state["cancelled"] else "limited",
                "Execution stopped before the next action.",
            )
            return {}
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
                    excerpts = []
                    for finding in args.findings:
                        reads = [
                            o
                            for o in state["observations"]
                            if o.get("source_handle") == finding.source_handle
                            and o.get("pointer", "") == finding.pointer
                            and o.get("status") == "ok"
                            and o.get("text")
                        ]
                        if not any(finding.quotation in o["text"] for o in reads):
                            raise ValueError(
                                "Final source quotation was not present in text read during this run"
                            )
                        excerpts.append(
                            f"Source {finding.source_handle}{finding.pointer}: {finding.quotation}"
                        )
                    state["grounded_findings"] = [f.model_dump() for f in args.findings]
                    # An explicit final selection replaces abandoned attempts. Dependencies
                    # are still traversed and independently verified by finish().
                    if args.result_ids:
                        state["superseded_result_ids"] = [
                            h for h in state["result_ids"] if h not in args.result_ids
                        ]
                        state["result_ids"] = list(dict.fromkeys(args.result_ids))
                    state["finalization_note"] = args.message
                    self.finish(
                        state,
                        "completed",
                        "Results are assembled from validated artifacts. See each chart for its scope, metric, exclusions and source evidence."
                        + ("\n\nRead source excerpts:\n" + "\n\n".join(excerpts) if excerpts else ""),
                    )
                    return {}
            else:
                observation = self.registry.call(name, arguments, state)
                if observation.get("status") == "ok" and name == "read_skill":
                    state["read_skill_ids"] = list(
                        dict.fromkeys(
                            [
                                *state.get("read_skill_ids", []),
                                observation["skill_id"],
                            ]
                        )
                    )
                if observation.get("status") == "ok" and name == "apply_skill":
                    active = [
                        s for s in state.get("active_skills", []) if s["skill_id"] != observation["skill_id"]
                    ]
                    state["active_skills"] = [
                        *active[-2:],
                        {k: observation[k] for k in ("skill_id", "version", "procedure")},
                    ]
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
        pending = list(state.get("result_ids", []))
        seen = set()
        while pending:
            handle = pending.pop(0)
            if handle in seen:
                continue
            seen.add(handle)
            try:
                self.registry.validate(HandleInput(handle=handle), state)
                artifact = self.registry.artifact(handle, state)
                valid.append(handle)
                pending.extend(h for h in artifact.dependencies if h not in seen)
            except (ValueError, KeyError, TimeoutError) as error:
                failures.append({"handle": handle, "error_type": type(error).__name__})
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
        unresolved = []
        for failure in failures:
            try:
                replaced = self.registry.artifact(failure["handle"], state).goal_id in completed_goals
            except (ValueError, KeyError):
                replaced = False
            if not replaced:
                unresolved.append(failure)
        state["rejected_result_attempts"] = failures
        failures = unresolved
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
