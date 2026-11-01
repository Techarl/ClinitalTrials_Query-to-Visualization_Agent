"""Export complete public execution traces and compact research tables, with credential scanning."""

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import dotenv_values

from evaluation.runtime import write

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "docs/vnext/results"
STUDIES = {
    "evaluation-initial": "vnext-eval-v1",
    "evaluation-logic": "vnext-eval-v2-logic",
    "evaluation-final": "vnext-eval-v2-final",
    "decision": "decision-v2",
    "repair": "cascade-v2",
    "harness": "harness-v2",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def aggregate(rows):
    return {
        "episodes": len(rows),
        "correct": sum(r["correct"] for r in rows),
        "model_calls": sum(r["model_calls"] for r in rows),
        "estimated_or_reserved_usd": sum(r["estimated_or_reserved_usd"] for r in rows),
        "mean_seconds": statistics.mean(r["elapsed_seconds"] for r in rows) if rows else None,
        "unknown_attempts": sum(r["unknown_attempts"] for r in rows),
    }


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    credentials = dotenv_values(ROOT / ".env")
    secrets = [
        credentials[k].encode()
        for k in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "NEO4J_PASSWORD")
        if credentials.get(k)
    ]
    index, summaries = {}, {}
    for name, folder in STUDIES.items():
        source = ROOT / "data" / folder
        report = read(source / "report.json")
        ledger = read(source / "ledger.json")
        destination = DESTINATION / (name + ".zip")
        hashes = {}
        with ZipFile(destination, "w", ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(source.rglob("*.json")):
                relative = path.relative_to(source)
                if "runtime" in relative.parts:
                    continue
                contents = path.read_bytes()
                if any(secret in contents for secret in secrets):
                    raise ValueError("Credential in research export: " + str(relative))
                archive.writestr(relative.as_posix(), contents)
                hashes[relative.as_posix()] = hashlib.sha256(contents).hexdigest()
            archive.writestr(
                "TRACE-MANIFEST.json",
                json.dumps(
                    {
                        "file_sha256": hashes,
                        "contents": "All exported per-turn responses, state, tool/model journals, public events, proposals, probes, assessments and cost ledgers; original and corrected grades retained.",
                        "source_records": "Frozen source JSON and PubMed receipts are in examples/snapshots and evaluation/frozen in the repository.",
                        "excluded": "Mutable SQLite/checkpoint files and duplicate runtime snapshot copies; no credentials or private user inputs are part of these generated research tasks.",
                    },
                    indent=2,
                ),
            )
        index[name] = {
            "file": destination.name,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "bytes": destination.stat().st_size,
            "trace_files": len(hashes),
        }
        summaries[name] = {
            "calls_including_editors_and_judges": len(ledger["events"]),
            "estimated_or_reserved_usd": ledger["estimated_or_reserved_usd"],
            "unknown_attempts": sum(e["status"] != "completed" for e in ledger["events"]),
        }
        if name.startswith("evaluation"):
            corrected = read(source / "rescored.json")
            summaries[name].update(
                {
                    "scorer": corrected["scorer_version"],
                    "total": aggregate(corrected["rows"]),
                    "splits": {
                        s: aggregate([r for r in corrected["rows"] if r["split"] == s])
                        for s in ("development", "calibration", "heldout")
                    },
                    "failures": [
                        {"id": r["episode_id"], "failures": r["failures"]}
                        for r in corrected["rows"]
                        if not r["correct"]
                    ],
                }
            )
        elif name == "decision":
            summaries[name].update(
                {k: report[k] for k in ("cases", "results", "origins", "thresholds", "fixed_only")}
            )
        elif name == "repair":
            corrected = read(source / "rescored.json")
            groups = defaultdict(list)
            for row in corrected["rows"]:
                groups[row["trace"].split("/")[-1]].append(row)
            summaries[name].update(
                {
                    "arms": {k: aggregate(v) for k, v in groups.items()},
                    "cascade_calibration_eligible": report["cascade_calibration_eligible"],
                }
            )
        else:
            corrected = {r["trace"]: r for r in read(source / "rescored.json")["rows"]}
            groups = defaultdict(list)
            uptake = Counter()
            for result in report["results"]:
                prefix = f"stream-{result['stream']}/task-{result['order']}"
                for name_, suffix in [
                    ("h0", "h0"),
                    ("ppc_no_memory", "ppc-no-memory/final"),
                    ("ppc_memory", "ppc-memory/final"),
                    ("manual", "manual"),
                ]:
                    groups[name_].append(corrected[prefix + "/" + suffix])
                selected = result["same_budget_retries"]["selected"]
                if selected is not None:
                    groups["same_budget_selected"].append(corrected[prefix + f"/retry-{selected}"])
                for key in ("retrieved", "applied"):
                    uptake[key] += len(result["ppc_memory"][key])
                uptake["adopted"] += int(result["ppc_memory"]["adopted"])
                uptake["context_cap_activated"] += int(result["ppc_memory"]["context_policy_effective"])
            summaries[name].update(
                {
                    "arms_final_execution_only": {k: aggregate(v) for k, v in groups.items()},
                    "memory": dict(uptake),
                    "streams": len({r["stream"] for r in report["results"]}),
                    "tasks": len(report["results"]),
                    "cost_note": "Arm totals show final executions only; total study ledger above includes editors, all probes, retries and failures.",
                }
            )
    write(DESTINATION / "index.json", index)
    write(DESTINATION / "summary.json", summaries)
    print(
        json.dumps(
            {
                "exports": index,
                "total_estimated_or_reserved_usd": sum(
                    v["estimated_or_reserved_usd"] for v in summaries.values()
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
