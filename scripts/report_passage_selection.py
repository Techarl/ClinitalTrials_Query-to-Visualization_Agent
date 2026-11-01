"""Recompute experiment metrics, export complete public traces and cost summaries."""

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import dotenv_values

from app.v2.costs import estimate
from app.v2.evidence_selection import Passage
from evaluation.runtime import write
from experiments.passage_selection import metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/passage-selection-v1"))
    parser.add_argument("--output", type=Path, default=Path("docs/passage-selection"))
    args = parser.parse_args()
    corpus = json.loads(Path("evaluation/passage_selection.json").read_text(encoding="utf-8"))
    budget = json.loads((args.input / "budget.json").read_text(encoding="utf-8"))
    summary = {
        "corpus_version": corpus["version"],
        "protocol": corpus["protocol"],
        "splits": {},
        "initial_retrieval": {},
        "attempts": budget["attempts"],
        "estimated_or_reserved_usd": budget["estimated_or_reserved_usd"],
        "unknown_attempts": sum(e["status"] != "completed" for e in budget["events"]),
        "cost_note": "Shared indexing/query embeddings plus all ranking and answer calls; CPU compute has no API fee but is not free. Estimates are not invoices.",
    }
    embedding_events = [
        e
        for e in budget["events"]
        if e.get("metadata", {}).get("requested_model") == "text-embedding-3-small"
    ]
    summary["shared_embedding_cost_usd"] = sum(
        e.get("estimated_cost_usd", e.get("reserved_upper_estimate_usd", 0)) for e in embedding_events
    )
    summary["embedding_attempts"] = len(embedding_events)
    details = []
    for split in ["development", "heldout"]:
        if not (args.input / f"{split}-complete.json").exists():
            raise ValueError(f"Split not completed: {split}")
        cases = [c for c in corpus["cases"] if c["split"] == split]
        if not (args.input / "scope-control-complete.json").exists():
            raise ValueError("Secondary scope control is incomplete")
        all_rows = {a: [] for a in [*corpus["protocol"]["arms"], "scoped_hybrid"]}
        retrieval = {method: [] for method in ["bm25", "dense", "hybrid"]}
        for case in cases:
            directory = args.input / case["id"]
            pool_data = json.loads((directory / "pool.json").read_text(encoding="utf-8"))
            pool = [Passage.model_validate(p) for p in pool_data["passages"]]
            for method, ranked in pool_data["initial_rankings"].items():
                retrieval[method].append(
                    sum(bool(set(g["passage_ids"]) & set(ranked)) for g in case["requirements"])
                    / len(case["requirements"])
                )
            for arm in all_rows:
                result = json.loads((directory / f"{arm}.json").read_text(encoding="utf-8"))
                recomputed = metrics(case, pool, result["ranking"], result["packing"], result)
                if recomputed != result["metrics"]:
                    raise ValueError(f"Saved metric mismatch: {case['id']} {arm}")
                receipts = [
                    json.loads(p.read_text(encoding="utf-8")) for p in directory.glob(f"{arm}-p_*.json")
                ]
                model_metadata = [result["metadata"], *[r["metadata"] for r in receipts]]
                costs = [estimate(m) for m in model_metadata]
                if any(c is None for c in costs):
                    raise ValueError(
                        "Unknown per-arm cost; retain reservation and resolve before cost comparison"
                    )
                row = {
                    "case": case["id"],
                    "arm": arm,
                    "split": split,
                    **recomputed,
                    "all_required_quotes_supported": recomputed["answer_requirement_coverage"] == 1
                    and recomputed["citations_exact"],
                    "model_calls": len(model_metadata),
                    "estimated_cost_usd": sum(costs),
                    "rank_seconds": result["rank_seconds"],
                    "answer_seconds": result["metadata"]["elapsed_seconds"],
                    "retrieval_seconds": pool_data["retrieval_seconds"],
                    "emitted_quotations": len(result["answer"]["findings"]),
                }
                all_rows[arm].append(row)
                details.append(row)
        summary["initial_retrieval"][split] = {
            method: {
                "mean_required_group_coverage_at_12": statistics.mean(values),
                "all_required_groups_present": sum(v == 1 for v in values),
                "queries": len(values),
            }
            for method, values in retrieval.items()
        }
        summary["splits"][split] = {}
        for arm, rows in all_rows.items():
            numeric = [
                "selected_requirement_coverage",
                "available_requirement_retention",
                "ndcg_at_5_within_pool",
                "counterevidence_retention",
                "answer_requirement_coverage",
                "context_tokens",
                "rank_seconds",
                "answer_seconds",
                "retrieval_seconds",
            ]
            means = {
                key: statistics.mean(r[key] for r in rows if r[key] is not None)
                if any(r[key] is not None for r in rows)
                else None
                for key in numeric
            }
            baseline = all_rows["hybrid"]
            summary["splits"][split][arm] = {
                "queries": len(rows),
                "means": means,
                "all_required_quotes_supported": sum(r["all_required_quotes_supported"] for r in rows),
                "strict_no_extra_passages": sum(r["answer_complete_and_relevant"] for r in rows),
                "all_citations_exact": sum(r["citations_exact"] for r in rows),
                "emitted_quotations": sum(r["emitted_quotations"] for r in rows),
                "model_calls": sum(r["model_calls"] for r in rows),
                "estimated_cost_usd": sum(r["estimated_cost_usd"] for r in rows),
                "paired_context_coverage_vs_hybrid": {
                    "wins": sum(
                        r["selected_requirement_coverage"] > b["selected_requirement_coverage"]
                        for r, b in zip(rows, baseline, strict=True)
                    ),
                    "ties": sum(
                        r["selected_requirement_coverage"] == b["selected_requirement_coverage"]
                        for r, b in zip(rows, baseline, strict=True)
                    ),
                    "losses": sum(
                        r["selected_requirement_coverage"] < b["selected_requirement_coverage"]
                        for r, b in zip(rows, baseline, strict=True)
                    ),
                },
            }
    args.output.mkdir(parents=True, exist_ok=True)
    write(args.output / "summary.json", summary)
    write(args.output / "cases.json", details)
    secrets = [
        v.encode()
        for k, v in dotenv_values(".env").items()
        if v and k in {"OPENAI_API_KEY", "OPENROUTER_API_KEY", "NEO4J_PASSWORD"}
    ]
    payloads = {
        p.relative_to(args.input).as_posix(): p.read_bytes()
        for p in args.input.rglob("*")
        if p.is_file() and p.suffix in {".json", ".txt"}
    }
    for name in [
        "experiments/passage_selection.py",
        "experiments/passage_corpus.py",
        "experiments/passage_scope_control.py",
        "experiments/retrieval.py",
        "app/v2/evidence_selection.py",
        "evaluation/passage_selection.json",
    ]:
        payloads["source/" + name] = Path(name).read_bytes()
    if any(secret in contents for contents in payloads.values() for secret in secrets):
        raise ValueError("Credential detected in trace export")
    manifest = {name: hashlib.sha256(contents).hexdigest() for name, contents in payloads.items()}
    archive = args.output / "traces.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for name, contents in payloads.items():
            bundle.writestr(name, contents)
        bundle.writestr("SHA256-MANIFEST.json", json.dumps(manifest, indent=2))
    write(
        args.output / "archive.json",
        {
            "path": archive.name,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "files": len(payloads),
            "credential_scan": "passed",
        },
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
