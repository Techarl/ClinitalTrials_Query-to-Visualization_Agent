"""Opt-in source-grounded alias candidate retrieval pilot; no automatic merging."""

import argparse
import json
import re
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.sources import content_hash, utc_now
from experiments.retrieval import Embeddings, HybridIndex

QUERIES = [
    ("Keytruda", "pembrolizumab"),
    ("Humira", "adalimumab"),
    ("Cosentyx", "secukinumab"),
    ("Dupixent", "dupilumab"),
    ("Xolair", "omalizumab"),
    ("Opdivo", "nivolumab"),
    ("pembrolizumab", "pembrolizumab"),
    ("adalimumab", "adalimumab"),
    ("帕博利珠单抗", "pembrolizumab"),
    ("阿达木单抗", "adalimumab"),
    ("司库奇尤单抗", "secukinumab"),
    ("奥马珠单抗", "omalizumab"),
]


def annotate_report(report):
    """Retain strict label hits and disclose a post-hoc source-mention proxy.

    Source names are not canonical ingredient identities. A label such as
    'Pembrolizumab (KEYTRUDA)' is a plausible candidate even when a strict
    'pembrolizumab' equality test fails. Neither metric proves equivalence.
    """
    for row in report["queries"]:
        row["exact_label_hit_at_5"] = row.pop("recall_at_5", row.get("exact_label_hit_at_5", {}))
        row["generic_mention_hit_at_5_posthoc"] = {}
        pattern = re.compile(r"\b" + re.escape(row["expected_label"]) + r"\b", re.IGNORECASE)
        for method, candidates in row["results"].items():
            row["generic_mention_hit_at_5_posthoc"][method] = any(
                pattern.search(item["document"]["text"])
                and not re.search(
                    r"placebo|biosimilar|\+|\bor\b", item["document"]["canonical_label"], re.IGNORECASE
                )
                for item in candidates
            )
    report["exact_label_hit_at_5"] = report.pop("recall_at_5", report.get("exact_label_hit_at_5", {}))
    report["generic_mention_hit_at_5_posthoc"] = {
        method: sum(row["generic_mention_hit_at_5_posthoc"][method] for row in report["queries"])
        / len(report["queries"])
        for method in ("bm25", "dense", "hybrid")
    }
    report["metric_note"] = (
        "Primary metric is exact source-label equality, not medical entity accuracy. The supplementary generic-name mention proxy was added after inspecting naming variants; it excludes obvious placebo/combination/biosimilar source labels, is not independently annotated, and cannot establish equivalence. canonical_label is the source's label, not a canonical ingredient dictionary."
    )
    return report


def corpus(root):
    documents = {}
    datasets = {
        m["dataset_id"]
        for response in root.glob("[0-9][0-9]_*/response.json")
        for m in json.loads(response.read_text(encoding="utf-8"))["provenance"]["datasets"]
    }
    for dataset in sorted(datasets):
        records = json.loads((root / "snapshots" / dataset / "studies.json").read_text(encoding="utf-8"))
        for raw in records:
            p = raw["protocolSection"]
            nct = p["identificationModule"]["nctId"]
            for index, item in enumerate(p.get("armsInterventionsModule", {}).get("interventions", [])):
                if item.get("type") not in ("DRUG", "BIOLOGICAL") or not item.get("name"):
                    continue
                normalized = " ".join(item["name"].split()).casefold()
                key = content_hash([item["type"], normalized])[:24]
                if key not in documents:
                    documents[key] = {
                        "id": key,
                        "canonical_label": item["name"],
                        "kind": item["type"],
                        "aliases": set(),
                        "sources": [],
                    }
                document = documents[key]
                document["aliases"].update(item.get("otherNames", []))
                if len(document["sources"]) < 3:
                    document["sources"].append(
                        {
                            "dataset_id": dataset,
                            "nct_id": nct,
                            "path": f"/protocolSection/armsInterventionsModule/interventions/{index}",
                            "source_value": item,
                        }
                    )
    result = []
    for item in documents.values():
        item["aliases"] = sorted(item["aliases"])
        item["text"] = item["canonical_label"] + " | " + " | ".join(item["aliases"])
        result.append(item)
    return sorted(result, key=lambda d: d["id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Enable paid OpenAI embedding calls")
    parser.add_argument("--output", type=Path, default=Path("data/entity_retrieval"))
    parser.add_argument("--annotate-existing", type=Path, help="Offline metric clarification, no API calls")
    args = parser.parse_args()
    if args.annotate_existing:
        report = annotate_report(json.loads(args.annotate_existing.read_text(encoding="utf-8")))
        args.annotate_existing.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "exact_label_hit_at_5": report["exact_label_hit_at_5"],
                    "generic_mention_hit_at_5_posthoc": report["generic_mention_hit_at_5_posthoc"],
                    "model_calls": 0,
                }
            )
        )
        return
    documents = corpus(PROJECT_ROOT / "examples")
    if not args.live:
        print(
            json.dumps(
                {
                    "documents": len(documents),
                    "model_calls": 0,
                    "note": "Use --live to compare BM25, embedding, and hybrid.",
                }
            )
        )
        return
    args.output.mkdir(parents=True, exist_ok=False)
    embedder = Embeddings(Settings(), args.output / "vectors.json")
    try:
        index = HybridIndex(documents, embedder)
        rows = []
        for query, expected in QUERIES:
            ranked = index.search(query)
            rows.append(
                {
                    "query": query,
                    "expected_label": expected,
                    "results": ranked,
                    "recall_at_5": {
                        method: any(
                            item["document"]["canonical_label"].casefold() == expected for item in values
                        )
                        for method, values in ranked.items()
                    },
                }
            )
        unknown = {query: index.search(query) for query in ["PD", "XYZUNKNOWN-742"]}
        report = {
            "at": utc_now(),
            "purpose": "entity_candidates_only_not_statistical_retrieval",
            "documents": len(documents),
            "corpus_hash": content_hash(documents),
            "embedding_model": embedder.model,
            "dimensions": embedder.dimensions,
            "embedding_calls": embedder.calls,
            "input_tokens": embedder.input_tokens,
            "rrf_k": 60,
            "queries": rows,
            "unknown_query_candidates": unknown,
            "recall_at_5": {
                method: sum(r["recall_at_5"][method] for r in rows) / len(rows)
                for method in ("bm25", "dense", "hybrid")
            },
            "limitations": [
                "Tiny hand-authored candidate benchmark, not end-to-end semantic or statistical accuracy.",
                "Aliases are trial submitter-provided otherNames, not an authoritative terminology dictionary.",
                "Top-k results never define a cohort; no candidate is automatically accepted or merged.",
                "Unknown/ambiguous queries also yield dense candidates; human/planner clarification remains necessary.",
                "The default service does not use this pilot.",
            ],
        }
        report = annotate_report(report)
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "documents": len(documents),
                    "exact_label_hit_at_5": report["exact_label_hit_at_5"],
                    "embedding_calls": embedder.calls,
                    "input_tokens": embedder.input_tokens,
                },
                indent=2,
            )
        )
    finally:
        embedder.close()


if __name__ == "__main__":
    main()
