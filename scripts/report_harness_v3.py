"""Report only closed Harness formal executions; retain original and linkage-audited grades."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from scripts.audit_core300_selection import revised_failures
from scripts.report_public_nli import digest, ledger_summary, paired_cluster_difference, read, wilson

ARMS = ("h0", "ppc_no_memory", "ppc_memory", "usd_retries")


def summaries(row):
    return {
        "h0": row["h0"],
        "ppc_no_memory": row["ppc_no_memory"]["final"],
        "ppc_memory": row["ppc_memory"]["final"],
        "usd_retries": row["usd_retries"]["selected_summary"],
    }


def retry_path(study, episode_id, recorded):
    """Resolve a historical absolute trace by its owned episode-relative suffix."""
    normalized = recorded.replace("\\", "/")
    marker = f"/{episode_id}/"
    if normalized.count(marker) != 1:
        raise ValueError("Retry selection has no unique owning episode")
    suffix = normalized.split(marker, 1)[1]
    path = study / episode_id / suffix
    if not path.resolve().is_relative_to((study / episode_id).resolve()):
        raise ValueError("Retry selection escapes its task")
    if suffix != "h0" and not suffix.startswith("usd-retries/attempt-"):
        raise ValueError("Retry selection is not an allowed candidate")
    return path


def validate_population(report, protocol, corpus):
    if digest(protocol["identity"]) != protocol["sha256"]:
        raise ValueError("Protocol digest mismatch")
    params = protocol["identity"]["parameters"]
    ids = params["order"]
    if len(ids) != 40 or len(set(ids)) != 40 or params["workers"] != 4:
        raise ValueError("Expected forty unique tasks and four workers")
    all_cases = {case["id"]: case for case in corpus["episodes"]}
    cases = [all_cases[i] for i in ids]
    if any(c["split"] != "heldout" for c in cases):
        raise ValueError("Formal population contains a development task")
    if sorted(Counter(c["entity_group"] for c in cases).values()) != [4] * 10:
        raise ValueError("Expected ten heldout disease groups with four tasks each")
    if sorted(Counter(c["family"] for c in cases).values()) != [2] * 20:
        raise ValueError("Expected twenty task families with two tasks each")
    rows = report["results"]
    reported = [r["episode_id"] for r in rows]
    errors = [r["episode_id"] for r in report["task_exceptions"]]
    if len(set(reported + errors)) != len(reported + errors) or not set(reported + errors) <= set(ids):
        raise ValueError("Duplicate or unplanned formal task result")
    if set(report["pending_or_unrun_ids"]) != set(ids) - set(reported + errors):
        raise ValueError("Unrun denominator differs from the planned population")
    if report["planned_unique"] != 40 or report["evaluated_count"] != len(rows):
        raise ValueError("Reported denominator mismatch")
    for row in rows:
        expected = all_cases[row["episode_id"]]
        if row["entity_group"] != expected["entity_group"] or row["family"] != expected["family"]:
            raise ValueError("Reported task category mismatch")
        for summary in summaries(row).values():
            if summary["episode_id"] != row["episode_id"]:
                raise ValueError("Arm summary belongs to another task")
    return cases


def audit_arm(directory, episode):
    failures = []
    for number, spec in enumerate(episode["turns"], 1):
        path = directory / f"turn-{number}.json"
        if not path.exists():
            failures.append(["missing_recorded_turn"])
            continue
        turn = read(path)
        failures.append(
            revised_failures(
                turn["grade"]["failures"], spec["expected"], turn["response"]["artifacts"], turn["journal"]
            )
        )
    return {"correct": not any(failures), "turn_failures": failures}


def verify_arm_accounting(row, events):
    for scope, claimed in row["costs"].items():
        suffix = f"/{row['episode_id']}/{scope}"
        selected = [e for e in events if e.get("study_scope", "").replace("\\", "/").endswith(suffix)]
        cost = sum(
            e["estimated_cost_usd"] if e["status"] == "completed" else e["reserved_upper_estimate_usd"]
            for e in selected
        )
        if len(selected) != claimed["calls"] or abs(cost - claimed["usd"]) > 1e-8:
            raise ValueError("Per-arm accounting differs from the complete global ledger")
    retry = row["usd_retries"]
    if abs(retry["extra_allocation_usd"] - row["costs"]["no-memory"]["usd"]) > 1e-8:
        raise ValueError("Retry allowance is not matched to its comparator")
    if retry["actual"]["usd"] > retry["extra_allocation_usd"] + 1e-8:
        raise ValueError("Retry arm exceeded its USD allowance")


def aggregate(rows, planned=40):
    result = {}
    for arm in ARMS:
        observed = [r["arms"][arm] for r in rows]
        successes = sum(r["correct"] for r in observed)
        result[arm] = {
            "correct_over_planned": wilson(successes, planned),
            "observed_tasks": len(observed),
            "unobserved_tasks": planned - len(observed),
            "correct_over_observed": wilson(successes, len(observed)),
            "original_correct": sum(r["original_correct"] for r in observed),
            "final_execution_calls": sum(r["model_calls"] for r in observed),
            "final_execution_usd": sum(r["final_execution_usd"] for r in observed),
            "final_execution_seconds": sum(r["elapsed_seconds"] for r in observed),
            "arm_research_cost_including_shared_h0_usd": sum(r["research_usd"] for r in observed),
        }
    return result


def report(study, corpus_path):
    receipt = study / "completed-or-stopped-ledger.json"
    if not receipt.exists():
        raise ValueError("Formal process has not closed; no partial grades or active ledger inspected")
    raw, protocol, corpus = read(study / "report.json"), read(study / "protocol.json"), read(corpus_path)
    cases = validate_population(raw, protocol, corpus)
    source_hashes = protocol["identity"]["parameters"]["source_files"]
    if hashlib.sha256(corpus_path.read_bytes()).hexdigest() not in source_hashes.values():
        raise ValueError("Case corpus is not a frozen source file")
    with ZipFile(study / "protocol.source.zip") as archive:
        code = protocol["identity"]["code"]
        if set(archive.namelist()) != set(code):
            raise ValueError("Frozen source archive membership mismatch")
        for name, expected in code.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Frozen source archive hash mismatch")
    ledger = read(receipt)
    costs = ledger_summary(ledger)
    if abs(costs["estimated_or_reserved_usd"] - raw["budget"]["usd"]) > 1e-8:
        raise ValueError("Closed ledger and report cost differ")
    by_id, rows = {r["episode_id"]: r for r in raw["results"]}, []
    for case in cases:
        if case["id"] not in by_id:
            continue
        row = by_id[case["id"]]
        verify_arm_accounting(row, ledger["events"])
        paths = {
            "h0": study / case["id"] / "h0",
            "ppc_no_memory": study / case["id"] / "no-memory/final",
            "ppc_memory": study / case["id"] / "memory/final",
            "usd_retries": retry_path(study, case["id"], row["usd_retries"]["selected_trace"]),
        }
        arms = {}
        for arm, summary in summaries(row).items():
            scope = {
                "h0": "h0",
                "ppc_no_memory": "no-memory",
                "ppc_memory": "memory",
                "usd_retries": "usd-retries",
            }[arm]
            research_usd = row["costs"][scope]["usd"] + (row["costs"]["h0"]["usd"] if arm != "h0" else 0)
            arms[arm] = {
                **audit_arm(paths[arm], case),
                "original_correct": summary["correct"],
                "model_calls": summary["model_calls"],
                "final_execution_usd": summary["estimated_or_reserved_usd"],
                "elapsed_seconds": summary["elapsed_seconds"],
                "research_usd": research_usd,
            }
        rows.append(
            {
                "episode_id": case["id"],
                "family": case["family"],
                "entity_group": case["entity_group"],
                "arms": arms,
                "mechanism": row["mechanism"],
            }
        )
    return {
        "version": "harness-v3-formal-closed-report-v1",
        "planned_tasks": 40,
        "observed_complete_task_rows": len(rows),
        "task_exceptions": raw["task_exceptions"],
        "unrun_ids": raw["pending_or_unrun_ids"],
        "complete": len(rows) == 40 and not raw["task_exceptions"],
        "arms": aggregate(rows),
        "paired": {
            arm: paired_cluster_difference(
                [{**row, "shared_trial_component": row["entity_group"]} for row in rows],
                arm,
                "h0",
                "correct",
            )
            for arm in ARMS[1:]
        },
        "formal_costs": costs,
        "prior_main_study_usd": raw["prior_usd"],
        "combined_main_study_usd": raw["combined_harness_usd"],
        "ledger_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
        "protocol_sha256": protocol["sha256"],
        "reporting_source_sha256": {
            name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
            for name in (
                "scripts/report_harness_v3.py",
                "scripts/audit_core300_selection.py",
                "scripts/report_public_nli.py",
            )
        },
        "rows": rows,
        "limitations": [
            "Frozen original grades and post-hoc selection-linkage audit are both retained; no candidate selection was repeated using audit results.",
            "Only forty distinct authored heldout tasks across ten disease groups; bootstrap resamples those groups and does not establish equivalence.",
            "Bootstrap clusters are disease groups; this does not assert that every source trial is disjoint across disease populations.",
            "Each arm's research cost includes the same H0 execution; do not sum those columns. The global ledger counts the shared H0 once, plus every editor, rejected probe and retry.",
            "If budget/infrastructure stopped the study, unobserved tasks remain in the planned denominator; observed-only and planned outcomes are separate.",
            "Skill read/apply and editor-declared reuse are mechanism evidence, not causal attribution of benefit.",
            "Arm order is fixed within each task and provider/cache load is shared; observed latency and cost are not a randomized serving-efficiency estimate.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=Path("data/harness-v3-formal05"))
    parser.add_argument("--cases", type=Path, default=Path("data/core300-frozen/cases.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("docs/public-evaluation/harness-v3-formal-results.json")
    )
    args = parser.parse_args()
    result = report(args.study, args.cases)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "planned_tasks",
                    "observed_complete_task_rows",
                    "complete",
                    "combined_main_study_usd",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
