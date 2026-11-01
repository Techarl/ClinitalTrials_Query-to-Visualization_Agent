"""Run decision repair/cascades through the real VNext execution chain, opt-in only."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from app.config import Settings
from app.sources import content_hash
from app.v2.agent import V2Agent
from evaluation.contracts import Episode
from evaluation.runtime import BudgetedPlanner, PersistentBudget, run_episode, write
from experiments.decision_v2 import ATOMIC_QUESTIONS, action_for, call_judge


class ReviewedPlanner:
    def __init__(self, settings, budget, registry, *, gate=None, threshold=0.8, inject=False, cheap=False):
        self.settings, self.budget, self.registry = settings, budget, registry
        self.base = BudgetedPlanner(
            settings.model_copy(update={"openai_model": "gpt-6-luna"}) if cheap else settings, budget
        )
        self.strong = BudgetedPlanner(settings, budget)
        self.gate, self.threshold, self.inject = gate, threshold, inject
        self.injected, self.escalate, self.repairs = set(), set(), {}

    def close(self):
        self.base.close()
        self.strong.close()

    def choose(self, context, definitions, deadline):
        rid = context["run_id"]
        planner = self.strong if rid in self.escalate else self.base
        self.escalate.discard(rid)
        action, metadata = planner.choose(context, definitions, deadline)
        if not action or action["name"] != "define_task":
            return action, metadata
        if self.inject and rid not in self.injected:
            action = deepcopy(action)
            action["arguments"].setdefault("filters", {})["countries"] = ["United Kingdom"]
            self.injected.add(rid)
            metadata["synthetic_fault"] = "unrequested country restriction in first proposed task"
        if not self.gate:
            return action, metadata
        payload = json.loads(context["payload"])
        state = {
            "request": payload["request"],
            "history": payload.get("history", []),
            "prior": (payload.get("prior") or {}).get("task"),
            "proposed": {"task": action["arguments"], "analyses": [], "status": "proposed"},
        }
        store = self.registry.store
        signature = "judge:" + content_hash([self.gate, state])
        existing = store.journal(rid, signature)
        if existing and existing["status"] == "completed":
            outcome = existing["output"]
        else:
            reservation = (
                len(json.dumps({"state": state, "questions": ATOMIC_QUESTIONS}).encode()) * 2.5 + 2048 * 10
            ) / 1e6
            store.begin(
                rid,
                signature,
                "model",
                {"reservation_usd": reservation, "model": self.gate, "state_hash": content_hash(state)},
            )
            try:
                outcome = call_judge(self.settings, state, self.gate, self.budget)
            except Exception as error:
                outcome = {
                    "metadata": {"provider": "judge", "usage": None, "error_type": type(error).__name__}
                }
            store.complete(rid, signature, outcome)
        choice = (
            action_for(outcome["assessment"], self.threshold) if outcome.get("assessment") else "repair_plan"
        )
        store.event(
            rid,
            "decision_review",
            {
                "action": choice,
                "variant": self.gate,
                "assessment": outcome.get("assessment"),
                "source": "experimental gate",
            },
        )
        if choice == "execute":
            return action, metadata
        self.repairs[rid] = self.repairs.get(rid, 0) + 1
        if self.repairs[rid] > 2:
            return {
                "name": "stop_with_limit",
                "arguments": {
                    "reason": "Experimental verification could not approve a plan within two system repairs; no request to repeat already-clear requirements."
                },
            }, metadata
        if choice == "repair_plan":
            self.escalate.add(rid)
        feedback = {
            "requested_action": choice,
            "assessment": outcome.get("assessment"),
            "proposed_task": action["arguments"],
            "message": "A clear request with a wrong plan requires system repair, not user confirmation. Correct only mismatches against the original request. For genuinely missing information ask a concrete consumable question; for unsupported capability explain the specific limit.",
        }
        return {"name": "_decision_feedback", "arguments": feedback}, metadata


class ReviewedAgent(V2Agent):
    def execute(self, graph_state):
        state = self.store.run(graph_state["run_id"])
        action = state["current_action"]
        if action["name"] != "_decision_feedback":
            return super().execute(graph_state)
        observation = {"tool": "decision_review", "status": "error", **action["arguments"]}
        state["observations"].append(observation)
        state["current_action"] = None
        state["decision_index"] = state.get("decision_index", 0) + 1
        self.store.save(state)
        self.store.event(state["run_id"], "observation", observation)
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/cascade-v2"))
    parser.add_argument("--judge-study", type=Path, default=Path("data/decision-v2"))
    parser.add_argument("--repair-only", action="store_true")
    args = parser.parse_args()
    policy = json.loads((args.judge_study / "frozen_policy.json").read_text(encoding="utf-8"))
    settings = Settings()
    budget = PersistentBudget(args.output / "ledger.json", max_usd=6, max_calls=1200)
    corpus = [
        Episode.model_validate(e)
        for e in json.loads(Path("evaluation/cases_v2.json").read_text(encoding="utf-8"))["episodes"]
    ]
    episodes = [
        next(e for e in corpus if e.entity_group == entity and e.family == family)
        for entity, family in [
            ("asthma", "phase"),
            ("rheumatoid arthritis", "completion_month"),
            ("asthma", "logic_all"),
            ("asthma", "axis_followup"),
        ]
    ]
    arms = {
        "strong_fixed": {},
        "injected_fixed": {"inject": True},
        "injected_jev_repair": {"inject": True, "gate": "atomic_compact"},
        "injected_generative_repair": {"inject": True, "gate": "generative_compact"},
    }
    eligible = False
    if not args.repair_only:
        report = json.loads((args.judge_study / "report.json").read_text(encoding="utf-8"))
        cal = report["results"]["calibration"]["atomic_compact"]
        samples = json.loads((args.judge_study / "cases.json").read_text(encoding="utf-8"))
        wrong = sum(s["split"] == "calibration" and s["label"] == "repair_plan" for s in samples)
        clean = sum(s["split"] == "calibration" and s["label"] == "execute" for s in samples)
        eligible = (
            wrong > 0
            and clean > 0
            and cal["missed_errors"] / wrong <= 0.3
            and cal["false_blocks"] / clean <= 0.1
            and cal["judge_errors"] == 0
        )
        if eligible:
            arms.update(
                {
                    "cheap_fixed": {"cheap": True},
                    "cheap_jev_cascade": {"cheap": True, "gate": "atomic_compact"},
                    "cheap_generative_cascade": {"cheap": True, "gate": "generative_compact"},
                }
            )
    rows = []
    for episode in episodes:
        for arm, options in arms.items():
            gate = options.get("gate")

            def factory(config, ledger, registry, options=options, gate=gate):
                return ReviewedPlanner(
                    config, ledger, registry, threshold=policy["thresholds"].get(gate, 0.8), **options
                )

            result = run_episode(
                episode,
                settings,
                args.output / episode.id / arm,
                budget,
                Path("examples"),
                planner_factory=factory,
                agent_class=ReviewedAgent,
            )
            rows.append({"arm": arm, **result, "synthetic_fault_injected": options.get("inject", False)})
            write(
                args.output / "report.json",
                {
                    "version": "cascade-v2-execution",
                    "cascade_calibration_eligible": eligible,
                    "rows": rows,
                    "budget": budget.report(),
                    "limits": [
                        "Four paired execution episodes; diagnostic, not a broad cost/accuracy claim.",
                        "Injected semantic failures are separated from natural planning.",
                        "Every gate, repair, ordinary planning and unknown attempt is in both the run journal and persistent ledger.",
                        "Cheap model is gpt-6-luna, verified available via the API models endpoint.",
                        "Escalation only changes the next planning call; numerical kernels, evidence and limits stay fixed.",
                    ],
                },
            )
            print(
                json.dumps(
                    {
                        "episode": episode.id,
                        "arm": arm,
                        "correct": result["correct"],
                        "calls": result["model_calls"],
                        "usd": result["estimated_or_reserved_usd"],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
