"""Offline role-level accounting for the retained harness pilot; zero API calls."""

import json
import math
import statistics
from pathlib import Path

from experiments.budget import estimate_cost

ROOT = Path(__file__).resolve().parents[1]


def accounting(result, variant):
    if variant == "H0_multisample":
        runs = result["samples"][1:]  # Shared frozen H0 was already billed.
        metadata = [result["selector_metadata"]]
    else:
        runs = [*result["probes"], result["final"]]
        metadata = list(result["editor_metadata"])
    elapsed = sum(m["elapsed_seconds"] for m in metadata)
    for run in runs:
        for turn in run["turns"]:
            metadata.extend(turn["response"]["meta"]["model_usage"])
            elapsed += turn["elapsed_seconds"]
    return metadata, elapsed


def main():
    path = ROOT / "docs/evals/harness_report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    all_calls = 0
    summaries = {}
    for variant, stream in report["streams"].items():
        selected_metadata, selected_latency = [], []
        for result in stream:
            metadata, elapsed = accounting(result, variant)
            all_calls += len(metadata)
            if result["task_id"] != "repeated_clarification":
                selected_metadata.extend(metadata)
                selected_latency.append(elapsed)
        usages = [m["usage"] for m in selected_metadata]
        times = sorted(selected_latency)
        summaries[variant] = {
            "valid_single_turn_tasks": len(times),
            "new_gpt_calls": len(usages),
            "shared_reference_calls_reused": len(times),
            "logical_calls_including_references": len(usages) + len(times),
            "input_tokens": sum(u["input_tokens"] for u in usages),
            "output_tokens": sum(u["output_tokens"] for u in usages),
            "estimated_new_gpt_cost_usd": sum(estimate_cost(u) for u in usages),
            "sum_component_latency_p50_seconds": statistics.median(times),
            "sum_component_latency_p95_seconds": times[math.ceil(len(times) * 0.95) - 1],
        }
    assert all_calls == report["ledger"]["attempts"]
    assert {value["new_gpt_calls"] for value in summaries.values()} == {66}
    report["compute_summary_single_turn"] = summaries
    report["component_latency_note"] = (
        "Sum of recorded turn/editor/selector timings, excluding some initialization, file, embedding and cache overhead; not an exact wall-clock end-to-end measurement. Shared reference costs are already part of the baseline. All roles' tokens retained."
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
