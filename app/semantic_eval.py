"""Opt-in real-model query-to-plan evaluation; no source, graph or statistical calls.

Expected answers are used only after AgentRunner returns. Each case has isolated
SQLite state; multi-turn cases close/reopen the runner between actual model turns.
"""

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import permutations
from pathlib import Path

from app.agent import AgentRunner
from app.config import PROJECT_ROOT, Settings
from app.models import AnalysisPlan, AnalysisRequest, AnalysisResponse
from app.sources import content_hash, utc_now
from app.storage import StateStore


class PlanningOnlyService:
    def __init__(self, settings):
        self.store = StateStore(settings.data_directory)

    def execute_plan(self, plan, run_id, session_id, trace, deadline):
        return AnalysisResponse(
            status="no_data",
            run_id=run_id,
            session_id=session_id,
            meta={"plan": plan.model_dump(), "evaluation_only": True},
        )


def term(value):
    value = re.sub(r"[^\w]+", " ", value.casefold()).strip()
    aliases = {
        "type 2 diabetes mellitus": "type 2 diabetes",
        "diabetes mellitus type 2": "type 2 diabetes",
        "parkinson s disease": "parkinson disease",
        "crohn s disease": "crohn disease",
        "chronic obstructive pulmonary disease": "copd",
        "hepatitis b virus infection": "hepatitis b",
        "rheumatoid arthritis": "rheumatoid arthritis",
        "lung carcinoma": "lung cancer",
        "breast neoplasms": "breast cancer",
        "breast carcinoma": "breast cancer",
    }
    return aliases.get(value, value)


def equivalent_filters(actual, expected):
    for key, value in expected.items():
        proposed = actual.get(key)
        if key == "intervention_match" and len(expected.get("interventions", [])) < 2:
            continue  # ANY and ALL of one intervention have identical populations.
        if isinstance(value, str):
            if not isinstance(proposed, str) or term(proposed) != term(value):
                return False
        elif isinstance(value, list):
            if sorted(term(v) for v in proposed) != sorted(term(v) for v in value):
                return False
        elif proposed != value:
            return False
    return True


def grade(response, expected):
    action = (
        "execute"
        if response.status in ("ok", "no_data")
        else "clarify"
        if response.status == "needs_clarification"
        else "unsupported"
        if response.status == "unsupported"
        else "failed"
    )
    errors = []
    if action != expected["action"]:
        errors.append(f"action: expected {expected['action']}, observed {action}")
    if action == expected["action"] == "execute":
        actual = response.meta["plan"]
        keys = ["operation", *(["group_by"] if expected["operation"] == "category" else [])]
        keys += [key for key in ("top_n", "histogram_bins", "time_field", "relationship") if key in expected]
        for key in keys:
            if actual[key] != expected[key]:
                errors.append(f"{key}: expected {expected[key]}, observed {actual[key]}")
        expected_groups, actual_groups = expected["cohorts"], [c["filters"] for c in actual["cohorts"]]
        if len(actual_groups) != len(expected_groups) or not any(
            all(equivalent_filters(a, e) for a, e in zip(order, expected_groups, strict=True))
            for order in permutations(actual_groups)
        ):
            errors.append("cohort filter populations differ")
    correct = not errors
    return {
        "observed_action": action,
        "correct": correct,
        "errors": errors,
        "incorrect_execution": action == "execute" and not correct,
        "correct_clarification": expected["action"] == action == "clarify",
        "overclarification": expected["action"] == "execute" and action == "clarify",
    }


def evaluate_case(case, settings, output):
    local = settings.model_copy(update={"app_data_dir": output / "state" / case["id"]})
    service = PlanningOnlyService(local)
    sid, version, state = service.store.session(None)
    if case.get("seed_plan"):
        state["last_successful_plan"] = AnalysisPlan.model_validate(case["seed_plan"]).model_dump()
    if case.get("seed_pending"):
        state["pending_clarification"] = case["seed_pending"]
    if state:
        state["last_run_id"] = "evaluation_seed"
        service.store.commit_session(sid, version, state)
    turns = []
    for index, turn in enumerate(case["turns"]):
        request = AnalysisRequest.model_validate({**turn["request"], "session_id": sid})
        runner = AgentRunner(local, service)
        started = time.monotonic()
        try:
            response = runner.analyze(request)
            trace = service.store.run(response.run_id)["trace"]
            turns.append(
                {
                    "turn": index + 1,
                    "request": turn["request"],
                    "expected": turn["expected"],
                    **grade(response, turn["expected"]),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "response": response.model_dump(mode="json"),
                    "trace": trace,
                }
            )
        finally:
            runner.close()
    result = {"id": case["id"], "split": case["split"], "turns": turns}
    (output / f"{case['id']}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def summarize(results):
    turns = [turn for case in results for turn in case["turns"]]
    execute = [t for t in turns if t["expected"]["action"] == "execute"]
    clarify = [t for t in turns if t["expected"]["action"] == "clarify"]
    metadata = [
        event["metadata"] for t in turns for event in t["trace"] if event["stage"] == "planner_response"
    ]

    def rate(numerator, denominator):
        return {
            "numerator": numerator,
            "denominator": denominator,
            "rate": round(numerator / denominator, 4) if denominator else None,
        }

    return {
        "cases": len(results),
        "turns": len(turns),
        "semantic_plan_accuracy": rate(sum(t["correct"] for t in turns), len(turns)),
        "execute_plan_accuracy": rate(sum(t["correct"] for t in execute), len(execute)),
        "incorrect_execution_rate": rate(sum(t["incorrect_execution"] for t in turns), len(turns)),
        "correct_clarification_rate": rate(sum(t["correct_clarification"] for t in clarify), len(clarify)),
        "overclarification_rate": rate(sum(t["overclarification"] for t in execute), len(execute)),
        "model_calls": sum(t["response"].get("meta", {}).get("model_calls", 0) for t in turns),
        "recorded_model_responses": len(metadata),
        "input_tokens": sum((m.get("usage") or {}).get("input_tokens", 0) for m in metadata),
        "output_tokens": sum((m.get("usage") or {}).get("output_tokens", 0) for m in metadata),
        "sum_turn_elapsed_seconds": round(sum(t["elapsed_seconds"] for t in turns), 3),
        "failed_cases": [r["id"] for r in results if any(not t["correct"] for t in r["turns"])],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly enable paid main-model calls")
    parser.add_argument("--split", choices=["all", "development", "heldout"], default="all")
    parser.add_argument("--workers", type=int, choices=[1, 2], default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    path = PROJECT_ROOT / "docs" / "evals" / "semantic_cases.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    cases = [c for c in document["cases"] if args.split == "all" or c["split"] == args.split]
    for case in cases:
        for turn in case["turns"]:
            AnalysisRequest.model_validate(turn["request"])
            assert turn["expected"]["action"] in ("execute", "clarify", "unsupported")
    if not args.live:
        print(
            json.dumps(
                {
                    "validated_cases": len(cases),
                    "model_calls": 0,
                    "note": "Use --live for paid main-model evaluation.",
                }
            )
        )
        return
    settings = Settings()
    output = args.output or settings.data_directory / ("semantic_" + str(time.time_ns()))
    output.mkdir(parents=True, exist_ok=False)
    results = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(evaluate_case, case, settings, output): case["id"] for case in cases}
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "case": result["id"],
                        "correct": all(t["correct"] for t in result["turns"]),
                        "completed": len(results),
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
    results.sort(key=lambda c: next(i for i, case in enumerate(cases) if case["id"] == c["id"]))
    report = {
        "at": utc_now(),
        "kind": "real_main_model_query_to_plan_only",
        "model": settings.openai_model,
        "reasoning_effort": settings.openai_reasoning_effort,
        "cases_hash": content_hash(document),
        "wall_seconds": round(time.monotonic() - started, 3),
        "overall": summarize(results),
        "splits": {
            split: summarize([r for r in results if r["split"] == split])
            for split in ("development", "heldout")
        },
        "limitations": [
            "Engineer-authored cases; no independent annotation or confidence interval.",
            "Seeded contexts are fixtures; repeated_clarification uses three actual model turns across runner restarts.",
            "Only query-to-plan semantics measured; no CTgov/Neo4j/result correctness measured here.",
            "Actual provider cost unavailable; tokens and elapsed times retained.",
            "One pass, no variance estimate. Two experiments remain disabled.",
        ],
        "results": results,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output / "report.json"), **report["overall"]}, indent=2))


if __name__ == "__main__":
    main()
