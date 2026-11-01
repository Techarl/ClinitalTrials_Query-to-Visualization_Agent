"""Publish only a complete, protocol-matched NLI study; never read partial case files or gold."""

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from scripts.nli_recovery_reporting import final_recovery_audit, recovery_plan, tag_latency

ARMS = ["hybrid", "cross_encoder", "jev", "luna", "full"]
SEED = 20261101
INTERRUPTED_PROTOCOL = "476bb042ff5273e16975f4e638aef8ff0c85e62877ca08a6bea540c038ea86ed"
COMPARISONS = [(arm, baseline) for baseline in ("hybrid", "full") for arm in ARMS if arm != baseline]
COMPARISONS += [("jev", "luna"), ("jev", "cross_encoder")]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reconcile_final_ledger(reported, persisted, study, source_archive):
    """Accept only independently saved post-write elapsed telemetry; never alter costs."""
    receipt = {
        "reported_budget_sha256": digest(reported),
        "persisted_ledger_sha256": digest(persisted),
        "accounting_authority": "persisted ledger.json",
        "timing_only_discrepancies": [],
    }
    if reported == persisted:
        return receipt
    # Bind this exception to the known frozen implementation, not an arbitrary run.
    with ZipFile(source_archive) as archive:
        source = archive.read("experiments/passage_selection.py").decode("utf-8")
    if ('budget.after(event, metadata)' not in source
            or 'metadata["elapsed_seconds"] = time.monotonic() - started' not in source):
        raise ValueError("Frozen source does not establish post-write elapsed telemetry")
    if ({k: v for k, v in reported.items() if k != "events"}
            != {k: v for k, v in persisted.items() if k != "events"}
            or len(reported["events"]) != len(persisted["events"])):
        raise ValueError("Formal ledger changed beyond elapsed telemetry")
    for index, (memory, disk) in enumerate(zip(reported["events"], persisted["events"])):
        if memory == disk:
            continue
        metadata = memory.get("metadata") or {}
        disk_metadata = disk.get("metadata") or {}
        elapsed = metadata.get("elapsed_seconds")
        if (memory.get("status") != "completed" or memory.get("arm") not in ("jev", "luna")
                or memory.get("stage") is not None or "elapsed_seconds" in disk_metadata
                or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or elapsed < 0
                or {k: v for k, v in metadata.items() if k != "elapsed_seconds"} != disk_metadata
                or {k: v for k, v in memory.items() if k != "metadata"}
                != {k: v for k, v in disk.items() if k != "metadata"}):
            raise ValueError("Formal ledger changed beyond elapsed telemetry")
        case_id, passage_id = memory.get("case_id"), memory.get("passage_id")
        if (not isinstance(case_id, str) or not case_id
                or any(c not in "0123456789abcdef-" for c in case_id)
                or not isinstance(passage_id, str) or not passage_id):
            raise ValueError("Elapsed telemetry lacks a safe score identity")
        relative = Path(case_id) / "scores" / memory["arm"] / (digest(passage_id)[:24] + ".json")
        proof = Path(study) / relative
        if not proof.is_file() or read(proof).get("metadata") != metadata:
            raise ValueError("Elapsed telemetry lacks matching saved score proof")
        receipt["timing_only_discrepancies"].append({
            "event_index": index, "case_id": case_id, "arm": memory["arm"],
            "added_field": "metadata.elapsed_seconds", "value": elapsed,
            "score_file": relative.as_posix(),
            "score_file_sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
        })
    return receipt


def percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def wilson(successes, n):
    """Descriptive binomial interval; dependence across trials is not corrected here."""
    if not n:
        return {"successes": 0, "denominator": 0, "rate": None, "wilson95": None}
    if not 0 <= successes <= n:
        raise ValueError("Invalid proportion")
    z, p = 1.959963984540054, successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return {
        "successes": successes,
        "denominator": n,
        "rate": p,
        "wilson95": [max(0, centre - radius), min(1, centre + radius)],
    }


def paired_cluster_difference(rows, arm, baseline, metric, *, draws=4000, seed=SEED):
    """Resample shared-trial components, retaining paired outcomes and all their cases."""
    groups = defaultdict(lambda: [0, 0])
    for row in rows:
        if metric == "answer_correct" and not row["answer_planned"]:
            continue
        cell = groups[row["shared_trial_component"]]
        cell[0] += int(row["arms"][arm][metric]) - int(row["arms"][baseline][metric])
        cell[1] += 1
    cells = [groups[key] for key in sorted(groups)]
    if not cells:
        return {"denominator": 0, "clusters": 0, "difference": None, "cluster_bootstrap95": None}
    rng, estimates = random.Random(seed), []
    for _ in range(draws):
        sample = rng.choices(cells, k=len(cells))
        estimates.append(sum(cell[0] for cell in sample) / sum(cell[1] for cell in sample))
    n = sum(cell[1] for cell in cells)
    return {
        "denominator": n,
        "clusters": len(cells),
        "difference": sum(cell[0] for cell in cells) / n,
        "cluster_bootstrap95": [percentile(estimates, 0.025), percentile(estimates, 0.975)],
        "draws": draws,
        "seed": seed,
    }


def require_unique(values, description):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {description}")


def validate_and_sanitize(report, protocol, strata, *, expected_cases=500, expected_answers=200):
    """Reject incomplete/mixed runs instead of silently shrinking denominators."""
    identity = protocol["identity"]
    if digest(identity) != protocol["sha256"]:
        raise ValueError("Protocol identity digest mismatch")
    if strata["formal_protocol_sha256"] != protocol["sha256"]:
        raise ValueError("Strata belong to a different formal protocol")
    if strata.get("label_access") is not False or strata.get("outcomes_used") is not False:
        raise ValueError("Strata are not registered input-only")
    params = identity["parameters"]
    ids, answer_ids = params["ids"], params["answer_ids"]
    require_unique(ids, "planned case IDs")
    require_unique(answer_ids, "answer case IDs")
    if len(ids) != expected_cases or len(answer_ids) != expected_answers or not set(answer_ids) <= set(ids):
        raise ValueError("Wrong formal case or answer denominator")
    if params["arms"] != ARMS or params["context_tokens"] != 1024 or params["candidate_limit"] != 24:
        raise ValueError("Unexpected arms or selection budget")
    stratum_ids = [r["case_id"] for r in strata["cases"]]
    require_unique(stratum_ids, "stratum case IDs")
    if set(stratum_ids) != set(ids):
        raise ValueError("Stratum case set differs from protocol")
    if set(answer_ids) != set(ids[:expected_answers]):
        raise ValueError("Answer subset differs from frozen hash-ordered selection")
    if expected_cases == 500:
        registered = strata["cases"]
        counts = (
            sum(r["full_context_fits_1024"] for r in registered),
            sum(r["source_fits_24_candidate_limit"] for r in registered),
            len({r["shared_trial_component"] for r in registered}),
            Counter(r["type"] for r in registered),
            Counter(r["section"] for r in registered),
        )
        if counts != (
            237,
            326,
            113,
            {"Single": 229, "Comparison": 271},
            {"Intervention": 142, "Eligibility": 132, "Results": 106, "Adverse Events": 120},
        ):
            raise ValueError("Registered input strata counts changed")
    indexed = {}
    for source in report["cases"]:
        key = source["id"], source["arm"]
        if key in indexed:
            raise ValueError("Duplicate case/arm result")
        if key[0] not in ids or key[1] not in ARMS:
            raise ValueError("Unexpected case/arm result")
        if source["status"] not in {"completed", "failed"}:
            raise ValueError("Unfinished case/arm result")
        completed = source["status"] == "completed"
        for field in ("recall", "precision"):
            value = source.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Invalid evidence metric")
        if type(source.get("complete")) is not bool:
            raise ValueError("Missing complete-evidence metric")
        answer_planned = key[0] in answer_ids
        if completed:
            selected, ranking = source.get("selected_ids"), source.get("ranking")
            for items in (selected, ranking):
                if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
                    raise ValueError("Missing or invalid selected/ranked IDs")
                require_unique(items, "selected/ranked evidence IDs")
            if not set(selected) <= set(ranking):
                raise ValueError("Selected evidence outside the ranked pool")
            if key[1] != "full" and len(ranking) > params["candidate_limit"]:
                raise ValueError("Ranking exceeds frozen candidate limit")
            if key[1] == "full" and selected != ranking:
                raise ValueError("Full-text control did not retain its full ranked source")
        if completed and answer_planned:
            if type(source.get("answer_correct")) is not bool or not isinstance(source.get("answer"), dict):
                raise ValueError("Planned answer missing from completed result")
            if type(source["answer"].get("citation_ids_valid")) is not bool:
                raise ValueError("Missing citation validation")
            citations = source["answer"].get("evidence_ids")
            if not isinstance(citations, list) or any(not isinstance(item, str) for item in citations):
                raise ValueError("Missing answer citation IDs")
            if source["answer"]["citation_ids_valid"] != (set(citations) <= set(selected)):
                raise ValueError("Citation membership flag disagrees with selected evidence")
        if not answer_planned and (source.get("answer") or source.get("answer_correct") is not None):
            raise ValueError("Unexpected answer outside frozen subset")
        # Never export statement text, selected sentences, predictions, or official gold.
        indexed[key] = {
            "completed": completed,
            "complete_evidence": completed and source["complete"],
            "evidence_recall": source["recall"] if completed else 0,
            "evidence_precision": source["precision"] if completed else 0,
            "answer_correct": bool(completed and answer_planned and source.get("answer_correct")),
            "citation_ids_valid": bool(
                completed and answer_planned and source.get("answer", {}).get("citation_ids_valid")
            ),
            "nonempty_citations": bool(completed and answer_planned and source["answer"]["evidence_ids"]),
        }
        for field in ("context_tokens", "ranking_seconds"):
            value = source.get(field)
            if completed:
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid execution measurement")
                if field == "context_tokens" and (type(value) is not int or (key[1] != "full" and value > params["context_tokens"])):
                    raise ValueError("Context exceeds frozen selection budget")
                indexed[key][field] = value
    if len(indexed) != expected_cases * len(ARMS):
        raise ValueError("Incomplete formal report: every planned case/arm must be terminal")
    for case_id in ids:
        pools = [set(source["ranking"]) for source in report["cases"]
                 if source["id"] == case_id and source["status"] == "completed" and source["arm"] != "full"]
        if pools and any(pool != pools[0] for pool in pools[1:]):
            raise ValueError("Selectors used different candidate pools")
    rows = []
    safe_strata = (
        "case_id",
        "type",
        "section",
        "full_context_fits_1024",
        "source_fits_24_candidate_limit",
        "shared_trial_component",
    )
    for stratum in sorted(strata["cases"], key=lambda r: r["case_id"]):
        rows.append(
            {
                **{k: stratum[k] for k in safe_strata},
                "answer_planned": stratum["case_id"] in answer_ids,
                "arms": {arm: indexed[stratum["case_id"], arm] for arm in ARMS},
            }
        )
    for arm in ARMS:
        claimed = report["summary"][arm]
        computed = summarize(rows)[arm]
        checks = {
            "planned": expected_cases,
            "answer_planned": expected_answers,
            "completed": computed["completed"],
            "complete_evidence": computed["complete_evidence"]["successes"],
            "answer_correct": computed["answer_correct"]["successes"],
            "evidence_recall": computed["mean_evidence_recall"],
            "evidence_precision": computed["mean_evidence_precision"],
        }
        if any(not math.isclose(claimed[key], value, abs_tol=1e-10) for key, value in checks.items()):
            raise ValueError("Raw cases disagree with published runner summary")
    return rows


def summarize(rows):
    output = {}
    n, answer_n = len(rows), sum(r["answer_planned"] for r in rows)
    for arm in ARMS:
        values = [r["arms"][arm] for r in rows]
        answers = [r["arms"][arm] for r in rows if r["answer_planned"]]
        raw_latency = [r["ranking_seconds"] for r in values if "ranking_seconds" in r]
        latency = [r["ranking_seconds"] for r in values
                   if "ranking_seconds" in r and r.get("ranking_latency_eligible", True)]
        context_lengths = [r["context_tokens"] for r in values if "context_tokens" in r]
        output[arm] = {
            "planned": n,
            "completed": sum(r["completed"] for r in values),
            "failures": sum(not r["completed"] for r in values),
            "answer_failures": sum(not r["completed"] for r in answers),
            "complete_evidence": wilson(sum(r["complete_evidence"] for r in values), n),
            "answer_correct": wilson(sum(r["answer_correct"] for r in answers), answer_n),
            "valid_citation_ids": wilson(sum(r["citation_ids_valid"] for r in answers), answer_n),
            "nonempty_citations": wilson(sum(r["nonempty_citations"] for r in answers), answer_n),
            "answer_correct_with_nonempty_valid_ids": wilson(
                sum(r["answer_correct"] and r["citation_ids_valid"] and r["nonempty_citations"] for r in answers), answer_n
            ),
            "mean_evidence_recall": sum(r["evidence_recall"] for r in values) / n if n else None,
            "mean_evidence_precision": sum(r["evidence_precision"] for r in values) / n if n else None,
            "implementation_ranking_latency": {
                "successful_samples": len(latency),
                "p50_seconds": percentile(latency, 0.5),
                "p95_seconds": percentile(latency, 0.95),
                "excluded_interruption_samples": len(raw_latency) - len(latency),
                "raw_saved_elapsed_diagnostic": {
                    "samples": len(raw_latency),
                    "p50_seconds": percentile(raw_latency, 0.5),
                    "p95_seconds": percentile(raw_latency, 0.95),
                    "interpretation": "May include cached partial retry work; never full task or fair model latency",
                },
            },
            "serialized_context_tokens": {
                "successful_samples": len(context_lengths),
                "mean": sum(context_lengths) / len(context_lengths) if context_lengths else None,
                "p50": percentile(context_lengths, 0.5), "p95": percentile(context_lengths, 0.95),
                "scope": "cl100k_base count of selected passage JSON only; excludes question, instructions and model output",
            },
        }
    return output


def aggregate(rows, *, draws=4000):
    subsets = {"all": rows}
    for field in ("full_context_fits_1024", "source_fits_24_candidate_limit", "type", "section"):
        for value in sorted({r[field] for r in rows}, key=str):
            subsets[f"{field}={value}"] = [r for r in rows if r[field] == value]
    output = {}
    for name, subset in subsets.items():
        output[name] = {
            "arms": summarize(subset),
            "paired_differences": {
                f"{arm}-minus-{baseline}": {
                    metric: paired_cluster_difference(subset, arm, baseline, metric, draws=draws)
                    for metric in ("complete_evidence", "answer_correct")
                }
                for arm, baseline in COMPARISONS
            },
        }
    return output


def common_case_latency(rows):
    """Descriptive same-case timing control only; never another quality subset."""
    eligible = [row for row in rows if all(row["arms"][arm]["completed"]
                and row["arms"][arm].get("ranking_latency_eligible", True) for arm in ARMS)]
    return {"planned_cases": len(rows), "retained_cases": len(eligible),
            "excluded_cases": len(rows) - len(eligible),
            "arms": {arm: values["implementation_ranking_latency"]
                     for arm, values in summarize(eligible).items()},
            "scope": "Identical cases with all five arms completed and no interruption flag; timing only. Asymmetric clients and execution periods remain confounded."}


def ledger_summary(ledger):
    events = ledger["events"]
    if ledger["attempts"] != len(events):
        raise ValueError("Ledger attempt count mismatch")
    known = unknown = embeddings = 0.0
    unknown_count = embedding_count = 0
    models, stages = Counter(), defaultdict(lambda: {"attempts": 0, "estimated_or_reserved_usd": 0.0})
    arm_costs = defaultdict(lambda: {"attempts": 0, "known_cost_usd": 0.0,
                                   "unknown_usage_attempts": 0, "unknown_reserved_usd": 0.0,
                                   "estimated_or_reserved_usd": 0.0})
    for event in events:
        metadata = event.get("metadata") or {}
        model = metadata.get("reported_model") or metadata.get("requested_model") or "unknown"
        models[model] += 1
        if event["status"] == "completed":
            cost = event["estimated_cost_usd"]
        else:
            cost = event["reserved_upper_estimate_usd"]
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise ValueError("Invalid ledger cost")
        if event["status"] == "completed":
            known += cost
        else:
            unknown += cost
            unknown_count += 1
        arm = event.get("arm") if event.get("arm") in ARMS else "shared_or_unattributed"
        arm_costs[arm]["attempts"] += 1
        arm_costs[arm]["estimated_or_reserved_usd"] += cost
        if event["status"] == "completed":
            arm_costs[arm]["known_cost_usd"] += cost
        else:
            arm_costs[arm]["unknown_usage_attempts"] += 1
            arm_costs[arm]["unknown_reserved_usd"] += cost
        embedding = "embedding" in model
        if embedding:
            embeddings += cost
            embedding_count += 1
        stage = "embeddings" if embedding else event.get("stage", "ranking_or_untagged")
        stages[stage]["attempts"] += 1
        stages[stage]["estimated_or_reserved_usd"] += cost
    if not math.isclose(known + unknown, ledger["estimated_or_reserved_usd"], abs_tol=1e-8):
        raise ValueError("Ledger event costs do not reconcile")
    return {
        "attempts": len(events),
        "known_cost_usd": known,
        "unknown_usage_attempts": unknown_count,
        "unknown_reserved_usd": unknown,
        "estimated_or_reserved_usd": known + unknown,
        "embedding_attempts": embedding_count,
        "embedding_cost_usd": embeddings,
        "model_attempts": dict(models),
        "stages": dict(stages),
        "nli_arm_costs": dict(arm_costs),
        "attribution_note": "Only explicit NLI arm tags are assigned. Embedding and untagged/unknown-model attempts stay shared or unattributed; do not interpret identified embedding counts as complete when usage is missing.",
    }


def accounting(paths):
    """Deduplicate the same physical file, not independent equal-content ledgers."""
    studies, seen_paths, seen_content, skipped, identical = {}, [], {}, [], []
    for name, path in paths.items():
        path = Path(path).resolve(strict=True)
        if any(path.samefile(previous) for previous in seen_paths):
            skipped.append(name)
            continue
        seen_paths.append(path)
        ledger = read(path)
        fingerprint = digest(ledger)
        if fingerprint in seen_content:
            identical.append({"ledger": name, "same_content_as": seen_content[fingerprint]})
        seen_content[fingerprint] = name
        studies[name] = {"ledger_sha256": fingerprint, **ledger_summary(ledger)}
    totals = {
        k: sum(study[k] for study in studies.values())
        for k in (
            "attempts",
            "known_cost_usd",
            "unknown_usage_attempts",
            "unknown_reserved_usd",
            "estimated_or_reserved_usd",
            "embedding_attempts",
            "embedding_cost_usd",
        )
    }
    return {
        "studies": studies,
        "totals": totals,
        "duplicate_ledger_inputs_skipped": skipped,
        "identical_content_ledgers_retained": identical,
        "note": "Provider-reported cost or versioned estimates, not invoices. Unknown usage retains reservations. "
        "Embedding costs are included in totals, not added again. All attempts include failures. "
        "Only the same physical input file is deduplicated. Equal-content independent files remain counted and flagged; "
        "their identity cannot be resolved from content alone. No per-response-ID deduplication: repeated attempts may be separately billable.",
    }


def markdown(summary, costs):
    lines = [
        "# NLI4CT official test split: evidence selection and fixed-model answers",
        "",
        "The primary analysis retains all **500 questions**. All five arms answer the same frozen subset of "
        "**200 questions**. Failures remain in the denominators and count as unsuccessful.",
        "Complete evidence coverage measures whether selected context retains every annotated evidence item. "
        "Answer correctness measures agreement with the official binary label. These are distinct outcomes.",
        "",
        "| Method | Complete evidence / 500 | Correct answers / 200 | Failures / answer-subset failures |",
        "|---|---:|---:|---:|",
    ]
    for arm, row in summary["strata"]["all"]["arms"].items():
        lines.append(
            f"| {arm} | {row['complete_evidence']['successes']} | {row['answer_correct']['successes']} | "
            f"{row['failures']} / {row['answer_failures']} |"
        )
    lines += [
        "",
        "Paired differences in percentage points, with 95% bootstrap intervals over shared-trial connected components:",
        "",
        "| Contrast | Complete-evidence difference | Answer-correctness difference |",
        "|---|---:|---:|",
    ]
    for comparison, values in summary["strata"]["all"]["paired_differences"].items():
        cells = []
        for metric in ("complete_evidence", "answer_correct"):
            result = values[metric]
            lo, hi = result["cluster_bootstrap95"]
            cells.append(f"{100 * result['difference']:+.1f} [{100 * lo:+.1f}, {100 * hi:+.1f}]")
        lines.append(f"| {comparison} | {' | '.join(cells)} |")
    lines += [
        "",
        "Input strata include the 237 questions whose full context fits 1024 tokens and their complement, "
        "the 326 questions with at most 24 source sentences and their complement, Single/Comparison tasks, "
        "and four section types. All stratum results and paired intervals remain in the JSON outputs; "
        "favorable subsets must not replace the primary results.",
        "Strata were registered after execution began but before reviewers accessed outcomes. "
        "This is not a claim of preregistration before the first execution.",
        "",
        "Intervals use seed 20261101 and 4000 resamples of shared-trial connected components; "
        "the primary population contains 113 components. Each draw is paired across methods, "
        "with differences weighted by question counts. Answer-subset intervals resample only components "
        "containing answer questions. Absolute proportions also have descriptive Wilson intervals: "
        "their independent-sample assumption does not fit shared trials and may produce narrow intervals. "
        "Stratification and multiple comparisons are exploratory, without multiplicity correction; "
        "these intervals do not establish a definitive winner.",
        "",
        "Engineering interpretation: evidence retention and final answer quality must be considered "
        "separately. A reranker's coverage gain need not produce a reliably measured answer gain; "
        "the direct paired intervals above quantify that distinction. The budget-exempt full-text "
        "control tests whether compression is worthwhile when reading all supplied material is feasible. "
        "Its complete evidence coverage follows by construction from supplying every source sentence. "
        "These results evaluate the frozen configurations, not universal model superiority.",
        "",
        "The full-text arm is exempt from the 1024-token selection cap. The other four arms share "
        "up to 24 candidates from hybrid retrieval. This tests evidence selection within supplied trial "
        "materials, not whole-corpus retrieval or chart correctness. Valid citation IDs establish membership "
        "in selected context, not support for the conclusion. If an answer call fails before its arm result "
        "is saved, evidence metrics also receive zero: this is the frozen runner's end-to-end scoring convention.",
        "The 1024-token budget counts selected passage JSON with cl100k_base, including IDs and metadata. "
        "It excludes questions, instructions, reranking requests and outputs, so complete pipeline inputs "
        "are not equalized. Full text is an additional-information control. Budgeted arms use the same "
        "sequential greedy packer: skip candidates that do not fit and consider later ones, without "
        "truncation or a relevance threshold.",
        "All five arms share the answer model and reasoning setting pinned in the protocol. Rerankers "
        "receive the original statement and complete candidate metadata. Jev/Luna additionally share "
        "a relevance rubric; MiniLM uses its fixed query-candidate encoding. This compares predefined "
        "system configurations, without claiming equally extensive model-specific tuning.",
        "Direct Jev-minus-Luna and Jev-minus-cross_encoder paired intervals were added before formal "
        "outcomes were accessed. Citation-ID membership accepts an empty list, so the JSON also reports "
        "nonempty citation rates and the joint outcome of a correct label with nonempty valid IDs. "
        "That joint outcome still does not establish semantic citation support or complete necessary evidence.",
        "",
        "The formal Jev implementation creates a new HTTP client per passage, while Luna reuses its client. "
        "Observed latency therefore includes asymmetric client overhead and cannot rank pure model speed. "
        "The [independent latency control](JEV_LATENCY_CONTROL.md) tested equal persistent connections "
        "and concurrency; its small development-set results remain separate from this test-set quality "
        "comparison. The original eight-question study remains in its [report](PASSAGE_SELECTION_RESULTS.md).",
        "Ranking timers exclude shared embedding warmup and initial retrieval, and may include reads "
        "of cached passage scores after recovery. They do not measure end-to-end task duration or fair "
        "model-serving latency.",
    ]
    common_latency = summary.get("recovery_common_case_latency")
    if common_latency:
        lines += [
            "", f"Descriptive ranking latency on the same {common_latency['retained_cases']} questions "
            f"({common_latency['excluded_cases']} questions excluded only from this timing table):", "",
            "| Arm | Same-case samples | Median seconds | p95 seconds |", "|---|---:|---:|---:|",
        ]
        for arm, timing in common_latency["arms"].items():
            lines.append(f"| {arm} | {timing['successful_samples']} | "
                         f"{timing['p50_seconds']:.3f} | {timing['p95_seconds']:.3f} |")
        lines += ["", "Client and execution-period asymmetries remain. Full text performs no ranking, "
                  "so its zero does not mean zero answer latency. The JSON retains all original timers "
                  "and the separately filtered per-arm summaries; quality denominators remain unchanged."]
    lines += [
        "",
        "Costs include failed attempts, reservations and shared embeddings. Values are estimates or "
        "provider-reported charges, not invoices:",
        "",
        "| Stage | Attempts | Known cost USD | Unknown-usage reservation USD |",
        "|---|---:|---:|---:|",
    ]
    for name, row in costs["studies"].items():
        lines.append(
            f"| {name} | {row['attempts']} | {row['known_cost_usd']:.6f} | {row['unknown_reserved_usd']:.6f} |"
        )
    formal = costs["studies"].get("formal_v2", {})
    if formal.get("nli_arm_costs"):
        lines += ["", "Formal costs attributed by explicit arm tags, including ranking and answer attempts:", "",
                  "| Arm | Attempts | Known cost USD | Unknown-usage reservation USD |", "|---|---:|---:|---:|"]
        for name, row in formal["nli_arm_costs"].items():
            lines.append(f"| {name} | {row['attempts']} | {row['known_cost_usd']:.6f} | {row['unknown_reserved_usd']:.6f} |")
        lines += ["", "Shared embeddings and unattributed attempts are listed separately, without equal "
                  "allocation or duplicate assignment to arms. Local MiniLM CPU time is not converted "
                  "to USD; API charges do not represent total deployment cost. Unknown attempts without "
                  "metadata cannot reliably be classified as embeddings but remain in total costs."]
    total = costs["totals"]
    recovery = summary.get("recovery")
    if recovery:
        previous = recovery["prior_ledger"]
        lines += [
            "", "## Interruption and recovery", "",
            "After the desktop-process interruption, execution resumed from frozen source matching the "
            "original protocol. The 500 questions, common 200-question answer subset, five arms and "
            "original budget ceiling were unchanged. Saved arm results and passage-score caches were reused.",
            f"The pre-resume snapshot contains {recovery['snapshot_files']} files. Filenames establish that "
            f"all five arm results were saved for {recovery['cases_all_five_arm_results_saved']} questions; "
            "this is execution progress, not accuracy.",
            f"Pre-resume filenames conservatively flag {recovery['latency_excluded_arm_count']} case/arm "
            "timings: the case had saved activity without a saved arm result, or had a failure marker. "
            "This can include later arms that had not started. Exclusions apply only to latency quantiles; "
            "all quality denominators and paired analyses remain unchanged.",
            "Original ranking_seconds remain in the per-case JSON and diagnostic summaries. A resumed "
            "timer can cover only remaining work and cannot represent a complete ranking or task. "
            "Retained times still describe this implementation and do not remove connection or execution-period differences.",
            f"The pre-resume ledger was verified as an exact prefix of the final ledger, preserving "
            f"{previous['unknown_usage_attempts']} prior unknown-usage attempts and "
            f"${previous['unknown_reserved_usd']:.7f} in reservations. Recovery added "
            f"{recovery['new_attempts_after_restart']} attempts. Snapshot costs are already included in "
            "formal_v2 and are not added again.",
            "The [recovery appendix and latency exclusions](public-evaluation/nli4ct-recovery.json) retain "
            "snapshot, protocol and completion-receipt hashes. Unsanitized intermediate materials are not published.",
            "A common-case latency summary additionally retains only questions for which all five arms "
            "completed without interruption flags. It supports descriptive same-case comparison only, "
            "without replacing the primary quality analysis or removing client and execution-period differences.",
        ]
    reconciliation = summary.get("final_ledger_reconciliation", {})
    discrepancies = reconciliation.get("timing_only_discrepancies", [])
    if discrepancies:
        lines += [
            "", "The final in-memory report adds elapsed-time telemetry to "
            f"{len(discrepancies)} ledger event(s) after the ledger's last disk write. "
            "Each addition was independently matched to the saved score metadata; all other event "
            "fields, usage, costs, statuses and ordering are identical. Accounting uses the original "
            "persisted ledger. Neither original was modified. Both digests and the exact timing-only "
            "discrepancy receipt are retained in summary.final_ledger_reconciliation.",
        ]
    lines += [
        "",
        f"Total: **{total['attempts']} attempts, ${total['estimated_or_reserved_usd']:.6f}**. "
        f"Usage is unknown for {total['unknown_usage_attempts']} attempts, retaining "
        f"${total['unknown_reserved_usd']:.6f} in reservations. "
        f"Embedding cost of ${total['embedding_cost_usd']:.6f} is already included.",
        "",
        "Outputs: [summary](public-evaluation/nli4ct-summary.json) · "
        "[sanitized per-case metrics](public-evaluation/nli4ct-cases.json) · "
        "[cost reconciliation](public-evaluation/nli4ct-accounting.json). "
        "Question text, raw materials and gold answers are not published.",
        "The [independent official-source audit](public-evaluation/nli4ct-independent-audit.json) "
        "reconstructs source sections, annotated evidence and answer-label scores without importing "
        "product scoring functions. Public sanitized case metrics support aggregate and paired-statistic "
        "reproduction; source-level reproduction also needs the local official dataset and saved study "
        "outputs. It does not independently replay models, tokenization or billing.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=Path("data/nli4ct-official-test-v2"))
    parser.add_argument(
        "--inputs", type=Path, default=Path("data/external-benchmarks/nli4ct-test-inputs.json")
    )
    parser.add_argument("--strata", type=Path, default=Path("docs/public-evaluation/nli-input-strata.json"))
    parser.add_argument("--pilot", type=Path, default=Path("data/public-pilot-nli4ct-v1/ledger.json"))
    parser.add_argument("--aborted", type=Path, default=Path("data/nli4ct-official-test-v1/ledger.json"))
    parser.add_argument("--recovery-manifest", type=Path, help="Required for the interrupted formal run")
    parser.add_argument("--recovery-completion", type=Path, help="Successful CURRENT producer exit receipt")
    parser.add_argument("--recovery-plan", type=Path,
                        default=Path("docs/public-evaluation/nli-recovery-latency-plan.json"))
    args = parser.parse_args()
    report_path = args.study / "report.json"
    if not report_path.exists():
        parser.exit(2, "Formal report is not complete; no partial scores read and no outputs written.\n")
    # The runner creates report.json only after all planned executions and final scoring.
    protocol, strata = read(args.study / "protocol.json"), read(args.strata)
    if bool(args.recovery_manifest) != bool(args.recovery_completion):
        raise ValueError("Supply both recovery manifest and successful completion receipt")
    if protocol["sha256"] == INTERRUPTED_PROTOCOL and not args.recovery_manifest:
        raise ValueError("This interrupted formal protocol requires recovery disclosure")
    if args.recovery_completion and read(args.recovery_completion).get("status") != "completed":
        raise ValueError("Recovery producer has not closed successfully; final scores not read")
    report = read(report_path)
    rows = validate_and_sanitize(report, protocol, strata)
    if hashlib.sha256(args.inputs.read_bytes()).hexdigest() != strata["input_sha256"]:
        raise ValueError("Registered input file changed")
    tasks = read(args.inputs)
    tasks.sort(key=lambda t: digest(["nli4ct-evidence-v2", t["id"]]))
    if digest(tasks) != protocol["identity"]["parameters"]["dataset"]:
        raise ValueError("Input dataset does not match frozen execution")
    if [t["id"] for t in tasks] != protocol["identity"]["parameters"]["ids"]:
        raise ValueError("Frozen case order differs from input hash ordering")
    archive_path = args.study / "protocol.source.zip"
    with ZipFile(archive_path) as archive:
        for name, expected in protocol["identity"]["code"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Frozen source archive mismatch")
    final_ledger = read(args.study / "ledger.json")
    reconciliation = reconcile_final_ledger(report["budget"], final_ledger, args.study, archive_path)
    recovery = None
    if args.recovery_manifest:
        recovery = final_recovery_audit(args.recovery_manifest, protocol, final_ledger,
                                        args.recovery_completion, ledger_summary)
        registered_plan = read(args.recovery_plan)
        expected_plan = recovery_plan(read(args.recovery_manifest), protocol)
        if registered_plan != expected_plan or any(recovery.get(key) != value for key, value in registered_plan.items()):
            raise ValueError("Recovery latency rule differs from registered outcome-blind plan")
        recovery["registered_plan_sha256"] = hashlib.sha256(args.recovery_plan.read_bytes()).hexdigest()
        tag_latency(rows, recovery)
    costs = accounting(
        {"pilot": args.pilot, "aborted_v1": args.aborted, "formal_v2": args.study / "ledger.json"}
    )
    summary = {
        "version": "nli4ct-final-v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": protocol["sha256"],
        "source_zip_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "strata_sha256": digest(strata),
        "report_sha256": digest(report),
        "final_ledger_reconciliation": reconciliation,
        "main_denominator": 500,
        "answer_denominator": 200,
        "execution_contract": {
            "answer_model_requested": protocol["identity"]["settings"]["openai_model"],
            "answer_reasoning": protocol["identity"]["settings"]["openai_reasoning_effort"],
            "decision_model_requested": protocol["identity"]["settings"]["openrouter_decision_model"],
            **{key: protocol["identity"]["parameters"][key] for key in (
                "generic_model", "generic_reasoning", "cross_encoder", "context_tokens", "candidate_limit",
                "tokenizer", "case_workers", "rank_workers",
            )},
        },
        "supplementary_comparisons_registered_before_outcome_access": ["jev-minus-luna", "jev-minus-cross_encoder"],
        "strata": aggregate(rows),
        "recovery": recovery,
        "recovery_common_case_latency": common_case_latency(rows) if recovery else None,
        "limitations": [
            "Shared-trial cluster bootstrap; exploratory comparisons without multiplicity correction",
            "Wilson intervals assume independent cases and may understate uncertainty",
            "Full context is exempt from selection budget",
            "Ranking latency includes asymmetric client implementations",
            "Evidence completeness, answer correctness, and citation entailment are distinct",
        ],
    }
    # Publish only after every validation and aggregation succeeds.
    destination = Path("docs/public-evaluation")
    destination.mkdir(parents=True, exist_ok=True)
    outputs = [("summary", summary), ("cases", {"cases": rows}), ("accounting", costs)]
    if recovery:
        outputs.append(("recovery", recovery))
    for name, value in outputs:
        target = destination / f"nli4ct-{name}.json"
        temporary = target.with_suffix(".pending")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    Path("docs/NLI4CT_RESULTS.md").write_text(markdown(summary, costs), encoding="utf-8")
    print(json.dumps({"cases": 500, "answers": 200, "accounting": costs["totals"]}))


if __name__ == "__main__":
    main()
