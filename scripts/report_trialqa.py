"""Independently report a finished TrialQA study; never load gold or partial grades."""

import argparse
import hashlib
import itertools
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile

from scripts.report_public_nli import digest, ledger_summary, percentile, read, require_unique

ARMS = ("given_sources", "frozen_retrieval")
SPLITS = ("development", "calibration", "heldout")
SEED = 20261101


def verify_archive(root, protocol):
    identity = protocol["identity"]
    if digest(identity) != protocol["sha256"]:
        raise ValueError("Frozen protocol digest mismatch")
    expected = identity["code"]
    with ZipFile(root / "protocol.source.zip") as archive:
        if set(archive.namelist()) != set(expected) or len(archive.namelist()) != len(expected):
            raise ValueError("Source archive member set mismatch")
        for name, checksum in expected.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != checksum:
                raise ValueError("Source archive hash mismatch: " + name)
    changed = [
        name
        for name, checksum in expected.items()
        if not Path(name).exists() or hashlib.sha256(Path(name).read_bytes()).hexdigest() != checksum
    ]
    return {
        "archive_matches_frozen_manifest": True,
        "files": len(expected),
        "current_workspace_differs": changed,
    }


def validated_rows(report, protocol, splits, *, expected_questions=120):
    if report["protocol_hash"] != protocol["sha256"] or digest(protocol["identity"]) != protocol["sha256"]:
        raise ValueError("Report protocol identity mismatch")
    parameters = protocol["identity"]["parameters"]
    planned = parameters["planned_ids"]
    require_unique(planned, "planned question IDs")
    if len(planned) != expected_questions or report["planned_questions"] != expected_questions:
        raise ValueError("Wrong complete-study denominator")
    if splits != parameters["split_manifest"] or set(planned) - set(splits):
        raise ValueError("Frozen split manifest mismatch")
    groups = defaultdict(set)
    for identity in planned:
        groups[splits[identity]["group"]].add(splits[identity]["split"])
    if any(len(values) != 1 or not values <= set(SPLITS) for values in groups.values()):
        raise ValueError("NCT group crosses splits")
    indexed = {}
    for case in report["cases"]:
        key = (case["id"], case["arm"])
        if key in indexed or case["id"] not in planned or case["arm"] not in ARMS:
            raise ValueError("Duplicate or unplanned case/arm")
        if case["status"] not in (
            "completed",
            "partial",
            "failed",
            "limited",
            "needs_clarification",
            "runner_failed",
        ):
            raise ValueError("Nonterminal runtime result")
        judgment = case.get("judgment")
        if judgment is not None and any(
            type(judgment.get(k)) is not bool for k in ("correct", "citations_support_answer")
        ):
            raise ValueError("Invalid judgment flags")
        correct = bool(judgment and judgment["correct"])
        indexed[key] = {
            "status": case["status"],
            "correct": correct,
            "cited_correct": bool(
                correct and case.get("quote_integrity") and judgment["citations_support_answer"]
            ),
            "quote_integrity": bool(case.get("quote_integrity")),
            "judge_missing": judgment is None,
            "judge_error": case.get("judge_error"),
            "elapsed_seconds": case.get("elapsed_seconds"),
            "source_reads": case.get("source_reads", 0),
        }
    if set(indexed) != {(identity, arm) for identity in planned for arm in ARMS}:
        raise ValueError("Incomplete planned case/arm coverage")
    rows = [
        {"id": identity, **splits[identity], "arms": {arm: indexed[identity, arm] for arm in ARMS}}
        for identity in planned
    ]
    for arm in ARMS:
        for field in ("correct", "cited_correct"):
            if report["summary"][arm][field] != sum(r["arms"][arm][field] for r in rows):
                raise ValueError("Runner summary disagrees with complete case rows")
    return rows


def paired_statistics(rows, *, draws=10000, seed=SEED):
    groups = defaultdict(lambda: [0, 0, 0])
    quadrants = Counter()
    for row in rows:
        a, b = (int(row["arms"][arm]["cited_correct"]) for arm in ARMS)
        group = groups[row["group"]]
        group[0] += a
        group[1] += b
        group[2] += 1
        quadrants[f"given_{a}_retrieval_{b}"] += 1
    cells = [groups[key] for key in sorted(groups)]
    if not cells:
        return {"questions": 0, "groups": 0, "difference": None, "cluster_bootstrap95": None}
    rng = random.Random(seed)
    differences, rates = [], {arm: [] for arm in ARMS}
    for _ in range(draws):
        sample = rng.choices(cells, k=len(cells))
        n = sum(c[2] for c in sample)
        counts = [sum(c[i] for c in sample) for i in (0, 1)]
        differences.append((counts[1] - counts[0]) / n)
        for i, arm in enumerate(ARMS):
            rates[arm].append(counts[i] / n)
    effects = [c[1] - c[0] for c in cells if c[1] != c[0]]
    observed = abs(sum(effects))
    if len(effects) <= 20:
        permutations = itertools.product((-1, 1), repeat=len(effects))
        extreme = sum(
            abs(sum(s * e for s, e in zip(signs, effects, strict=True))) >= observed for signs in permutations
        )
        p = extreme / 2 ** len(effects)
        method = "exact_groupwise_arm_swap"
        permutations_n = 2 ** len(effects)
    else:
        rng = random.Random(seed)
        extreme = sum(abs(sum(rng.choice((-1, 1)) * e for e in effects)) >= observed for _ in range(draws))
        p, method, permutations_n = (extreme + 1) / (draws + 1), "monte_carlo_groupwise_arm_swap", draws
    return {
        "questions": len(rows),
        "groups": len(cells),
        "contrast": "frozen_retrieval-minus-given_sources",
        "difference": sum(c[1] - c[0] for c in cells) / len(rows),
        "cluster_bootstrap95": [percentile(differences, 0.025), percentile(differences, 0.975)],
        "arm_cluster_bootstrap95": {
            arm: [percentile(values, 0.025), percentile(values, 0.975)] for arm, values in rates.items()
        },
        "draws": draws,
        "seed": seed,
        "paired_question_counts": dict(quadrants),
        "sign_flip": {
            "method": method,
            "nonzero_groups": len(effects),
            "assignments": permutations_n,
            "two_sided_p": p,
        },
    }


def summarize(rows, *, draws=10000):
    subsets = {"all": rows, **{split: [r for r in rows if r["split"] == split] for split in SPLITS}}
    result = {}
    for name, subset in subsets.items():
        arms = {}
        for arm in ARMS:
            values = [r["arms"][arm] for r in subset]
            latency = [r["elapsed_seconds"] for r in values if r["elapsed_seconds"] is not None]
            arms[arm] = {
                "denominator": len(values),
                "correct": sum(r["correct"] for r in values),
                "cited_correct": sum(r["cited_correct"] for r in values),
                "quote_integrity": sum(r["quote_integrity"] for r in values),
                "runtime_statuses": dict(Counter(r["status"] for r in values)),
                "judge_missing": sum(r["judge_missing"] for r in values),
                "source_reads": sum(r["source_reads"] for r in values),
                "latency_p50_seconds": percentile(latency, 0.5),
                "latency_p95_seconds": percentile(latency, 0.95),
            }
        result[name] = {"arms": arms, "paired_cited_correct": paired_statistics(subset, draws=draws)}
    return result


def verify_inputs(protocol, data_root):
    tasks = read(data_root / "trialqa-inputs.json")
    sources = read(data_root / "trialqa-records.json")
    parameters = protocol["identity"]["parameters"]
    if digest(tasks) != parameters["inputs_sha256"] or digest(sources) != parameters["records_sha256"]:
        raise ValueError("Frozen question/source snapshot mismatch")
    task_map = {task["id"]: task for task in tasks}
    if len(task_map) != len(tasks):
        raise ValueError("Duplicate input question IDs")
    nct_groups = defaultdict(set)
    for task in tasks:
        for nct in task["nct_ids"]:
            nct_groups[nct].add(parameters["split_manifest"][task["id"]]["group"])
    if any(len(groups) != 1 for groups in nct_groups.values()):
        raise ValueError("Shared NCT ID crosses frozen groups")
    return task_map, sources["records"]


def quotation_integrity(findings, records):
    """Independent JSON-pointer traversal over the pinned raw source snapshots."""
    for finding in findings:
        value = records.get(finding.get("nct_id"))
        if value is None:
            return False
        try:
            for token in finding["pointer"].split("/")[1:]:
                token = token.replace("~1", "/").replace("~0", "~")
                value = value[int(token)] if isinstance(value, list) else value[token]
        except (KeyError, IndexError, ValueError, TypeError):
            return False
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        if not finding["quotation"] or finding["quotation"] not in text:
            return False
    return bool(findings)


def build_report(root, data_root=Path("data/external-benchmarks"), *, expected_questions=120):
    root = Path(root)
    if not (root / "report.json").exists():
        raise ValueError("Producer has not published a complete report; refusing partial scoring")
    report, protocol, splits = (read(root / name) for name in ("report.json", "protocol.json", "splits.json"))
    rows = validated_rows(report, protocol, splits, expected_questions=expected_questions)
    archive = verify_archive(root, protocol)
    task_map, records = verify_inputs(protocol, Path(data_root))
    ledger = read(root / "ledger.json")
    if ledger != report["budget"]:
        raise ValueError("Completed report and ledger disagree")
    accounting = ledger_summary(ledger)
    unknown = []
    failures = []
    for row in rows:
        for arm in ARMS:
            execution = read(root / row["id"] / arm / "execution.json")["identity"]
            if execution != {
                "protocol_hash": protocol["sha256"],
                "input_hash": digest(task_map[row["id"]]),
                "arm": arm,
            }:
                raise ValueError("Case execution identity mismatch")
            path = root / row["id"] / arm / "runtime.json"
            runtime = read(path) if path.exists() else None
            if runtime is not None:
                if runtime["status"] != row["arms"][arm]["status"]:
                    raise ValueError("Runtime receipt and report status disagree")
                if quotation_integrity(runtime["findings"], records) != row["arms"][arm]["quote_integrity"]:
                    raise ValueError("Independent raw-source quotation check disagrees")
                errors = [
                    entry.get("output", {}).get("metadata", {}).get("error_type")
                    for entry in runtime["journal"]
                    if entry["kind"] == "model"
                ]
                errors = [error for error in errors if error]
            else:
                errors = [read(root / row["id"] / arm / "failure.json")["error_type"]]
            if row["arms"][arm]["status"] != "completed":
                failures.append(
                    {"id": row["id"], "arm": arm, "status": row["arms"][arm]["status"], "error_types": errors}
                )
    for event in ledger["events"]:
        if event["status"] != "completed":
            unknown.append(
                {
                    k: event.get(k)
                    for k in ("case_id", "arm", "stage", "status", "reserved_upper_estimate_usd")
                }
            )
    return {
        "protocol_hash": report["protocol_hash"],
        "completed_report_sha256": digest(report),
        "source_archive": archive,
        "source_snapshots_match_frozen_manifest": True,
        "independent_raw_source_quotation_check": "all case findings rechecked against pinned trial records",
        "summary": summarize(rows),
        "accounting": accounting,
        "unknown_usage_attempts": unknown,
        "noncompleted_runtime": failures,
        "limits": report["limits"]
        + [
            "120 questions include 19 development, 22 calibration, 79 heldout; only the heldout subset was untouched in project development.",
            "Cited correctness uses a fallible automated Luna judge; exact quotations alone do not prove semantic entailment.",
            "Intervals resample whole NCT-connected groups. Split comparisons are exploratory, without multiplicity correction.",
            "Missing answers/judgments remain unsuccessful in the operational denominator; infrastructure failures are not silently retried.",
            "Current frozen CTgov records can differ from the benchmark source version; not an official LABBench2 score.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("data/trialqa-formal-v1"))
    parser.add_argument("--data", type=Path, default=Path("data/external-benchmarks"))
    parser.add_argument(
        "--output", type=Path, default=Path("docs/public-evaluation/trialqa-formal-statistics.json")
    )
    args = parser.parse_args()
    report = build_report(args.run, args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "accounting": report["accounting"]}, indent=2))


if __name__ == "__main__":
    main()
