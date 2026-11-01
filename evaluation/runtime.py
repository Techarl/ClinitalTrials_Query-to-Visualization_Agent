"""Real tool/Neo4j episode execution with frozen sources and persistent paid-call ledger."""

import json
import threading
import time
from pathlib import Path

from app.service import AnalysisService
from app.v2.agent import NativePlanner, V2Agent
from app.v2.contracts import RunRequest
from app.v2.costs import estimate
from app.v2.integrations import register_integrations
from app.v2.store import V2Store
from app.v2.tools import ToolRegistry
from evaluation.replay import FrozenSource
from evaluation.scorers import grade
from experiments.budget import Budget


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class PersistentBudget(Budget):
    def __init__(self, path, max_usd=15, max_calls=3000):
        super().__init__(max_usd, max_calls)
        self.path, self.lock = Path(path), threading.RLock()
        if self.path.exists():
            old = json.loads(self.path.read_text(encoding="utf-8"))
            self.events = old["events"]
            self.estimated_usd = old["estimated_or_reserved_usd"]

    def before(self, context_bytes, output_tokens=4096):
        with self.lock:
            event = super().before(context_bytes, output_tokens)
            write(self.path, self.report())
            return event

    def after(self, event, metadata):
        with self.lock:
            cost = estimate(metadata)
            if cost is not None:
                if event["status"] != "completed":
                    self.estimated_usd += cost - event["reserved_upper_estimate_usd"]
                    event.update(
                        status="completed",
                        metadata=metadata,
                        estimated_cost_usd=cost,
                        cost_basis="versioned_model_rate_or_provider_usage_cost",
                    )
            else:
                event.update(status="completed_usage_unknown", metadata=metadata)
            write(self.path, self.report())


class BudgetedPlanner:
    def __init__(self, settings, budget):
        self.planner, self.budget = NativePlanner(settings), budget

    def choose(self, context, definitions, deadline):
        event = self.budget.before(
            len((context["system"] + context["payload"] + json.dumps(definitions)).encode())
        )
        action, metadata = self.planner.choose(context, definitions, deadline)
        self.budget.after(event, metadata)
        return action, metadata

    def close(self):
        self.planner.close()


def run_episode(
    episode,
    settings,
    directory,
    budget,
    frozen,
    *,
    skill="",
    policy="relevant",
    planner_factory=None,
    agent_class=V2Agent,
):
    directory = Path(directory)
    if (directory / "summary.json").exists():
        return json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    directory.mkdir(parents=True, exist_ok=False)
    settings = settings.model_copy(update={"app_data_dir": directory / "runtime"})
    service = AnalysisService(settings)
    store = V2Store(service.store)
    registry = ToolRegistry(settings, service, store, FrozenSource([frozen, Path("evaluation/frozen")]))
    registry.replay_pubmed_root = Path("evaluation/frozen")
    register_integrations(registry)
    planner = (
        planner_factory(settings, budget, registry) if planner_factory else BudgetedPlanner(settings, budget)
    )
    agent = agent_class(settings, registry, planner, context_policy=policy, skill=skill)
    turns, ids, session = [], [], None
    started = time.monotonic()
    try:
        for turn in episode.turns:
            # Future utterances and expected answers never enter the runtime context.
            parent = ids[turn.parent_turn] if turn.parent_turn is not None else ids[-1] if ids else None
            state = store.create(
                RunRequest(query=turn.query, session_id=session, parent_run_id=parent),
                settings.v2_max_run_seconds,
            )
            session = state["session_id"]
            result = agent.run(state["run_id"])
            state = store.run(state["run_id"])
            journal = store.journal_entries(state["run_id"])
            scored = grade(result, turn.expected, registry, state)
            row = {
                "response": result.model_dump(mode="json"),
                "grade": scored,
                "state": state,
                "events": store.events(state["run_id"], limit=2000),
                "journal": journal,
            }
            turns.append(row)
            ids.append(state["run_id"])
            write(directory / f"turn-{len(turns)}.json", row)
        summary = {
            "episode_id": episode.id,
            "family": episode.family,
            "split": episode.split,
            "origin": episode.origin,
            "correct": all(t["grade"]["correct"] for t in turns),
            "turns": len(turns),
            "model_calls": sum(t["response"]["usage"]["model_calls"] for t in turns),
            "estimated_or_reserved_usd": sum(
                t["response"]["usage"]["estimated_or_reserved_usd"] for t in turns
            ),
            "unknown_attempts": sum(t["response"]["usage"]["unknown_attempts"] for t in turns),
            "invalid_tools": sum(
                o.get("status") == "error" for t in turns for o in t["state"]["observations"]
            ),
            "failures": [t["grade"]["failures"] for t in turns],
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "runtime_inputs": "current and past utterances only; no labels or future answers",
            "skill": skill,
            "context_policy": policy,
        }
        write(directory / "summary.json", summary)
        return summary
    finally:
        agent.close()
        service.close()
