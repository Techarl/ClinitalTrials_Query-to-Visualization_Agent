"""English publication of a reconciled, closed Harness report; no runtime writes."""

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from scripts.harness_recovery_reporting import audit_recovery
from scripts.report_harness_v3 import ARMS, report

LABELS = {
    "h0": "Strong H0",
    "ppc_no_memory": "PPC without memory",
    "ppc_memory": "PPC with frozen memory",
    "usd_retries": "USD-allowance retries",
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def historical_costs(archive_path, index_path, formal):
    """Validate all four closed pre-formal ledgers, without trusting rounded prose."""
    raw = archive_path.read_bytes()
    expected = read(index_path)["files"][archive_path.name]["sha256"]
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Prior ledger archive hash mismatch")
    expected_main = {r["sha256"]: r for r in formal["prior_main_receipts"]}
    seen, receipts, categories = set(), [], Counter()
    with ZipFile(archive_path) as archive:
        names = [i.filename for i in archive.infolist() if not i.is_dir()]
        if "SHA256-MANIFEST.json" in names:
            manifest = json.loads(archive.read("SHA256-MANIFEST.json"))
            names.remove("SHA256-MANIFEST.json")
            if set(manifest) != set(names) or any(
                hashlib.sha256(archive.read(name)).hexdigest() != manifest[name] for name in names
            ):
                raise ValueError("Prior ledger archive member checksum mismatch")
        if len(names) != 4 or len(set(names)) != 4:
            raise ValueError("Expected four nonoverlapping prior Harness ledger files")
        for name in names:
            payload = archive.read(name)
            sha = hashlib.sha256(payload).hexdigest()
            if sha in seen:
                raise ValueError("Duplicate prior Harness ledger receipt")
            seen.add(sha)
            ledger = json.loads(payload)
            events = ledger["events"]
            known = unknown = 0.0
            unknown_count = 0
            for event in events:
                completed = event["status"] == "completed"
                value = event["estimated_cost_usd"] if completed else event["reserved_upper_estimate_usd"]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid prior Harness ledger cost")
                if completed:
                    known += value
                else:
                    unknown += value
                    unknown_count += 1
            cost = known + unknown
            if len(events) != ledger["attempts"] or abs(cost - ledger["estimated_or_reserved_usd"]) > 1e-8:
                raise ValueError("Prior Harness receipt does not reconcile")
            if sha in expected_main:
                original = expected_main[sha]
                if original["attempts"] != len(events) or abs(original["usd"] - cost) > 1e-8:
                    raise ValueError("Prior main receipt differs from the frozen formal protocol")
                category = "prior_main"
                label = (
                    "Stopped main01 infrastructure run"
                    if "stopped" in name or "main01" in name
                    else "Completed development and online diagnostics"
                )
            elif "pilot" in name.casefold():
                category, label = "pilot", "Pilot and mechanism diagnostics"
            elif "smoke" in name.casefold() or "completed-ledger" in name:
                category, label = "smoke", "Infrastructure smoke"
            else:
                raise ValueError("Unrecognized prior Harness receipt")
            categories[category] += 1
            receipts.append(
                {
                    "label": label,
                    "category": category,
                    "sha256": sha,
                    "attempts": len(events),
                    "known_usd": known,
                    "unknown_reserved_usd": unknown,
                    "unknown_attempts": unknown_count,
                    "estimated_or_reserved_usd": cost,
                }
            )
    if categories != {"prior_main": 2, "pilot": 1, "smoke": 1} or not set(expected_main) <= seen:
        raise ValueError("Prior Harness stage coverage is incomplete")
    main = sum(r["estimated_or_reserved_usd"] for r in receipts if r["category"] == "prior_main")
    if abs(main - formal["prior_main_study_usd"]) > 1e-8:
        raise ValueError("Prior main cost would be omitted or double counted")
    total = (
        sum(r["estimated_or_reserved_usd"] for r in receipts)
        + formal["formal_costs"]["estimated_or_reserved_usd"]
    )
    return {
        "archive_sha256": expected,
        "receipts": receipts,
        "all_v3_actual_or_reserved_usd": total,
        "all_v3_attempts": sum(r["attempts"] for r in receipts) + formal["formal_costs"]["attempts"],
    }


def dollars(value):
    return f"${value:.6f}"


def render(formal, prior):
    if formal["planned_tasks"] != 40 or set(formal["arms"]) != set(ARMS):
        raise ValueError("Renderer requires the fixed forty-task/four-arm report")
    observed = formal["observed_complete_task_rows"]
    closed = "completed" if formal["complete"] else "closed incomplete"
    lines = [
        "# Harness v3: frozen-memory transfer results",
        "",
        f"The formal study is **{closed}**: **{observed}/40 complete four-arm task records**. "
        "All forty planned tasks remain in the primary denominator; failed, exceptional and unrun work is retained.",
        "",
        "## Scope and interpretation",
        "",
        "The protocol selected forty distinct heldout tasks from ten disease groups and twenty authored operation families, "
        "with one fixed admission order and four concurrent workers. Twenty development tasks produced the frozen bank. "
        "Each formal task receives an independent read-only copy; test results never update it. "
        "The same operation families appear in development and formal testing, so this measures disease transfer rather than unseen-family generalization.",
        "",
        "The separate two-task online diagnostic starts from an empty bank and compares proactive versus disabled access. "
        "It is not part of the forty-task transfer denominator and does not update the formal bank. "
        "Its outcomes and the development mechanism observations remain in the "
        "[online diagnostic](public-evaluation/harness-v3-online-results.json) and "
        "[development report](public-evaluation/harness-v3-development.json).",
        "",
        "## Correctness",
        "",
        "| Arm | Original checks / 40 | With dependency audit / 40 | Audit / observed complete records | Final execution calls | Selected-final seconds (sum) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm in ARMS:
        row = formal["arms"][arm]
        lines.append(
            f"| {LABELS[arm]} | {row['original_correct']}/40 | "
            f"{row['correct_over_planned']['successes']}/40 | "
            f"{row['correct_over_observed']['successes']}/{row['correct_over_observed']['denominator']} | "
            f"{row['final_execution_calls']} | {row['final_execution_seconds']:.3f} |"
        )
    lines += [
        "",
        "Seconds sum recorded elapsed time for the selected final executions, including their tool work. "
        "They exclude editor calls, candidate probes and unselected retries, and do not measure experiment wall time or pure model latency. "
        "Cached summaries can retain earlier timings; crash-resumed summaries can measure only a resumed segment. "
        "Treat these values as descriptive execution records, with shared provider/cache load and interruption confounds.",
        "",
        "The independent post-hoc audit rechecks dependency selection and linkage. Other frozen correctness checks are retained; "
        "this is not a wholly independent rescore of every task property. Neither audit labels nor hidden answers are used to "
        "select candidates or amend experience. Original grades remain visible.",
        "",
        f"Task-level exceptions: **{len(formal['task_exceptions'])}**. Unrun tasks: **{len(formal['unrun_ids'])}**.",
        "",
        "### Paired comparisons with H0",
        "",
        "| Arm | Paired tasks | Only arm correct | Only H0 correct | Difference, percentage points | 95% disease-cluster bootstrap interval |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for arm in ARMS[1:]:
        pairs = [(row["arms"][arm]["correct"], row["arms"]["h0"]["correct"]) for row in formal["rows"]]
        only_arm, only_h0 = sum(a and not b for a, b in pairs), sum(b and not a for a, b in pairs)
        pair = formal["paired"][arm]
        difference = "not estimable" if pair["difference"] is None else f"{100 * pair['difference']:+.2f}"
        interval = pair["cluster_bootstrap95"]
        text = (
            "not estimable" if interval is None else f"[{100 * interval[0]:+.2f}, {100 * interval[1]:+.2f}]"
        )
        lines.append(f"| {LABELS[arm]} | {len(pairs)} | {only_arm} | {only_h0} | {difference} | {text} |")
    lines += [
        "",
        "The paired analysis uses complete task records only. Forty authored tasks and ten disease clusters provide limited precision. "
        "Disease clustering does not prove source-trial disjointness, and an interval containing zero does not establish equivalence.",
        "",
        "## Costs and matched retry allowance",
        "",
        "| Arm | Selected final execution | Research cost including the common H0 |",
        "| --- | ---: | ---: |",
    ]
    for arm in ARMS:
        value = formal["arms"][arm]
        lines.append(
            f"| {LABELS[arm]} | {dollars(value['final_execution_usd'])} | "
            f"{dollars(value['arm_research_cost_including_shared_h0_usd'])} |"
        )
    costs, match = formal["formal_costs"], formal["budget_matching"]
    lines += [
        "",
        "Do not sum the research-cost column: each comparator includes the same H0, which is paid once in the global ledger. "
        "Research costs include every editor, candidate probe, discarded proposal and retry; selected-final costs alone are not end-to-end costs. "
        "Arm columns cover complete task rows; all partial or exceptional task costs remain in the global ledger.",
        "",
        f"The formal ledger records **{costs['attempts']} attempts**, {dollars(costs['known_cost_usd'])} known estimated charges "
        f"plus {dollars(costs['unknown_reserved_usd'])} reserved for **{costs['unknown_usage_attempts']} unknown-usage attempts**, "
        f"total **{dollars(costs['estimated_or_reserved_usd'])}**. Recorded usage: "
        f"{costs['recorded_input_tokens']:,} input and {costs['recorded_output_tokens']:,} output tokens; missing usage is not zero cost.",
        "",
        "| Closed stage | Attempts | Actual or conservatively reserved cost |",
        "| --- | ---: | ---: |",
    ]
    for receipt in prior["receipts"]:
        lines.append(
            f"| {receipt['label']} | {receipt['attempts']} | {dollars(receipt['estimated_or_reserved_usd'])} |"
        )
    lines += [
        f"| Formal transfer study | {costs['attempts']} | {dollars(costs['estimated_or_reserved_usd'])} |",
        f"| **All Harness v3 stages** | **{prior['all_v3_attempts']}** | **{dollars(prior['all_v3_actual_or_reserved_usd'])}** |",
        "",
        f"The main-stage subtotal is {dollars(formal['combined_main_study_usd'])}; it includes the stopped main01 run and cumulative "
        "development-plus-online receipt exactly once. Pilot and smoke costs are added separately in the all-v3 total. "
        "Historical pre-v3 experiments are separate studies and are not silently added to this total. These are estimates/reservations, not provider invoices.",
        "",
        f"Across complete task rows, retries received {dollars(match['retry_extra_allowance_usd'])} in additional allowance, "
        f"equal to PPC-without-memory's incremental research cost ({dollars(match['ppc_no_memory_incremental_usd'])}). "
        f"They actually spent or reserved {dollars(match['retry_actual_or_reserved_usd'])}; "
        f"the unused difference is {dollars(match['retry_extra_allowance_usd'] - match['retry_actual_or_reserved_usd'])}. "
        f"Each task permits at most {match['maximum_attempts_per_task']} retries. Pre-request reservations and that attempt cap "
        "can leave an unusable residual: equal allowance does not guarantee equal realized spending. "
        f"Costs outside complete task rows: {dollars(max(0, match['partial_or_exception_task_cost_usd']))}.",
        "",
        "## Experience mechanisms",
        "",
        "| Outer editor observation | PPC without memory | PPC with frozen memory |",
        "| --- | ---: | ---: |",
    ]
    labels = {
        "editor_retrieved_id_occurrences": "Retrieved experience ID occurrences",
        "editor_declared_reuse_id_occurrences": "Declared reuse ID occurrences",
        "publicly_adopted_tasks": "Publicly adopted tasks",
        "adopted_nonempty_procedure_tasks": "Adopted nonempty textual procedures",
        "adopted_empty_procedure_tasks": "Adopted empty procedures",
        "final_nonempty_injected_procedure_tasks": "Final nonempty injected text (including policy instructions)",
        "combined_probe_context_clipping_tasks": "Combined probes with context clipping",
    }
    editors = formal["mechanisms"]["editors"]
    for key, label in labels.items():
        lines.append(f"| {label} | {editors['ppc_no_memory'][key]} | {editors['ppc_memory'][key]} |")
    events = formal["mechanisms"]["memory_final_all_turn_tool_events"]
    lines += [
        "",
        "Memory-arm executor observations across **all final-execution turns**:",
        "",
        "| Tool event | Count |",
        "| --- | ---: |",
    ]
    for key, label in (
        ("search_calls", "Experience searches"),
        ("learned_read", "Learned skill reads"),
        ("learned_applied", "Learned skill activations"),
        ("engineering_builtin_read", "Engineering builtin reads"),
        ("engineering_builtin_applied", "Engineering builtin activations"),
        ("other_read", "Other skill reads"),
        ("other_applied", "Other skill activations"),
    ):
        lines.append(f"| {label} | {events.get(key, 0)} |")
    mechanisms = formal["mechanisms"]
    lines += [
        "",
        f"Tasks with any learned read: **{mechanisms['memory_final_tasks_with_learned_read']}**; "
        f"tasks with any learned activation: **{mechanisms['memory_final_tasks_with_learned_apply']}**. "
        "Counts are events or task occurrences, not unique reusable components. Outer editor retrieval, declared provenance and "
        "injected procedure text are distinct from the executor actively searching, reading and applying skills.",
        "",
        "An empty adopted procedure is not a learned textual component; another policy setting may still differ. "
        "Final injected text can also contain a fixed engineering policy instruction, which is not a learned component. "
        "Editors and adoption checks see the first/current turn, while final correctness is evaluated over the complete episode. "
        "Engineering builtins are already part of strong H0 and must not be presented as learned experience.",
        "",
        "These measurements do not by themselves demonstrate online learning, autonomous skill reuse benefits or recursive self-improvement. "
        "Any performance claim must be supported by the paired heldout comparison and its full research cost, not mechanism counts alone.",
        "",
        "## Interruption and reproducibility",
        "",
    ]
    recovery = formal.get("recovery_amendment")
    if recovery:
        lines.append(
            f"A desktop crash interrupted {len(recovery['interrupted_runs'])} pending recordings after "
            f"{recovery['completed_tasks']} complete task records. The external recovery wrapper preserved original "
            "deadlines, unknown reservations and already-recorded failures. It reconstructed cached retry candidates without "
            "changing the archived inference source. This is nevertheless a disclosed execution-layer amendment, not byte-identical "
            "execution of the original driver. A pending recording does not establish that its failure was caused by the crash. "
            "See the [recovery amendment](public-evaluation/harness-v3-recovery.json)."
        )
    else:
        lines.append("No desktop recovery amendment is attached to this closed report.")
    recovery_audit = formal.get("recovery_audit")
    if recovery_audit:
        lines += [
            "",
            f"The immutable pre-resume backup was verified against its registered hash. All "
            f"{recovery_audit['prior_attempts']} original ledger events remain an exact prefix of the closed ledger; "
            f"{recovery_audit['retained_unknown_attempts']} unknown-usage reservations totaling "
            f"{dollars(recovery_audit['retained_unknown_reserved_usd'])} are retained. "
            f"All {len(recovery_audit['unchanged_completed_tasks'])} previously complete task results are byte-identical. "
            "Snapshot costs are already included in the cumulative ledger, not added again.",
        ]
    lines += [
        "",
        "Arm order is fixed and provider/cache load is shared. Recorded elapsed time is not a randomized serving-efficiency estimate; "
        "crash-resumed executions further qualify timing comparisons. No hidden grades were used to repair or select candidates.",
        "",
        f"Frozen protocol identity: `{formal['protocol_sha256']}`. Closed ledger SHA256: `{formal['ledger_sha256']}`.",
        "",
        "[Validated machine-readable results](public-evaluation/harness-v3-formal-results.json) · "
        "[Archive index and exact source snapshots](public-evaluation/harness-v3-archives/index.json) · "
        "[Protocol](HARNESS_V3_PROTOCOL.md)",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument(
        "--archive-directory", type=Path, default=Path("docs/public-evaluation/harness-v3-archives")
    )
    parser.add_argument("--output", type=Path, default=Path("docs/HARNESS_V3_RESULTS.md"))
    parser.add_argument("--recovery-backup", type=Path)
    args = parser.parse_args()
    result = report(args.study, args.cases)  # The closed-only gate runs before other reads.
    if result.get("recovery_amendment"):
        if not args.recovery_backup:
            raise ValueError("Interrupted study requires the original recovery backup before publication")
        result["recovery_audit"] = audit_recovery(args.study, args.recovery_backup)
    prior = historical_costs(
        args.archive_directory / "closed-ledgers.zip", args.archive_directory / "index.json", result
    )
    args.output.write_text(render(result, prior), encoding="utf-8")
    print(
        json.dumps(
            {
                "planned_tasks": 40,
                "observed_tasks": result["observed_complete_task_rows"],
                "all_v3_actual_or_reserved_usd": prior["all_v3_actual_or_reserved_usd"],
            }
        )
    )


if __name__ == "__main__":
    main()
