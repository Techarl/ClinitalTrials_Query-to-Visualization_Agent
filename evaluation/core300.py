"""Build 300 grouped, frozen-source execution tasks with external raw-data oracles."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from app.config import Settings
from app.models import Filters
from app.sources import ClinicalTrialsClient, SnapshotStore, content_hash, source_query
from evaluation.contracts import Episode, Turn
from evaluation.runtime import write

GROUPS = [
    ("melanoma", "pembrolizumab", "development"),
    ("psoriasis", "secukinumab", "development"),
    ("asthma", "omalizumab", "development"),
    ("rheumatoid arthritis", "adalimumab", "calibration"),
    ("type 2 diabetes", "empagliflozin", "calibration"),
    ("non-small cell lung cancer", "osimertinib", "heldout"),
    ("breast cancer", "trastuzumab", "heldout"),
    ("ulcerative colitis", "vedolizumab", "heldout"),
    ("Crohn disease", "infliximab", "heldout"),
    ("multiple sclerosis", "ocrelizumab", "heldout"),
    ("migraine", "erenumab", "heldout"),
    ("prostate cancer", "enzalutamide", "heldout"),
    ("depression", "esketamine", "heldout"),
    ("ovarian cancer", "olaparib", "heldout"),
    ("obesity", "semaglutide", "heldout"),
]


def acquire(root):
    from evaluation.protocol import freeze

    freeze(
        root / "population-plan.json",
        {"groups": GROUPS, "selection": "Declared before source acquisition or model execution"},
    )
    store = SnapshotStore(root)
    settings = Settings(max_source_records=15000, ctgov_page_size=1000)
    for disease, drug, _ in GROUPS:
        query = source_query(Filters(condition=disease, interventions=[drug]))
        cached = store.cached(query, 10**10)
        if cached:
            continue
        client = ClinicalTrialsClient(settings)
        try:
            snapshot, rows = client.fetch(query, time.monotonic() + 600)
            if not snapshot.complete:
                raise RuntimeError(f"Incomplete {disease}: {snapshot.reason}")
            store.save(snapshot, rows)
            print(
                json.dumps({"disease": disease, "records": len(rows), "dataset_id": snapshot.dataset_id}),
                flush=True,
            )
        finally:
            client.close()


def build(root):
    store = SnapshotStore(root)
    episodes = []
    for group_index, (disease, drug, split) in enumerate(GROUPS):
        filters = {"condition": disease, "interventions": [drug]}
        snapshot, rows = store.cached(source_query(Filters(**filters)), 10**10)
        sponsors = Counter(
            r["protocolSection"].get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")
            for r in rows
        )
        anchor = sorted(sponsors, key=lambda n: (-sponsors[n], n))[0]
        prefix = f"For {disease} studies involving {drug}, "

        def expected(artifacts, **kw):
            return {
                "status": "completed",
                "filters": filters,
                "artifacts": artifacts,
                "sql_oracle": True,
                **kw,
            }

        def add(family, turns):
            identity = "ep_" + content_hash([disease, family, turns])[:16]
            episodes.append(
                Episode(
                    id=identity,
                    family=family,
                    entity_group=disease,
                    split=split,
                    turns=[Turn(**t) for t in turns],
                ).model_dump(mode="json")
            )

        for group in ("phase", "status", "country", "study_type"):
            add(
                "single_" + group,
                [
                    {
                        "query": prefix + f"show trial counts by {group} with complete evidence.",
                        "expected": expected([{"operation": "category", "group_by": group}]),
                    }
                ],
            )
        for field in ("start", "completion", "first_posted"):
            add(
                "single_time_" + field,
                [
                    {
                        "query": prefix
                        + f"show trial counts by {field.replace('_', ' ')} year, preserving zero periods and exclusions.",
                        "expected": expected(
                            [{"operation": "time_series", "time_field": field, "time_grain": "year"}]
                        ),
                    }
                ],
            )
        add(
            "single_histogram",
            [
                {
                    "query": prefix + "show enrollment in 7 equal-width histogram bins.",
                    "expected": expected([{"operation": "histogram", "histogram_bins": 7}]),
                }
            ],
        )
        combos = [("phase", "start"), ("status", "completion"), ("country", "first_posted")]
        if group_index < 5:
            combos.append(("study_type", "primary_completion"))
        for group, field in combos:
            add(
                f"compound_{group}_{field}",
                [
                    {
                        "query": prefix
                        + f"produce two charts: counts by {group}, and counts by {field.replace('_', ' ')} month. Preserve both results.",
                        "expected": expected(
                            [
                                {"operation": "category", "group_by": group},
                                {"operation": "time_series", "time_field": field, "time_grain": "month"},
                            ]
                        ),
                    }
                ],
            )
        for top in (2, 3):
            add(
                f"dependent_top{top}",
                [
                    {
                        "query": prefix
                        + f"show a shared-drug sponsor network, find the top {top} sponsors sharing drugs with {anchor}, then compare their yearly trial counts using the actual selected sponsor identities and all their studies in this population.",
                        "expected": expected(
                            [
                                {"operation": "network", "relationship": "sponsor_shared_drug"},
                                {"operation": "time_series", "time_field": "start", "time_grain": "year"},
                            ],
                            dependent_selection=True,
                            anchor_sponsor=anchor,
                            selection_top_n=top,
                        ),
                    }
                ],
            )
        for field in ("start", "completion", "first_posted", "primary_completion"):
            add(
                "dialogue_" + field,
                [
                    {
                        "query": prefix + "show phase counts.",
                        "expected": expected([{"operation": "category", "group_by": "phase"}]),
                    },
                    {
                        "query": f"Keep the same disease and medicine, now count by {field.replace('_', ' ')} month.",
                        "expected": expected(
                            [{"operation": "time_series", "time_field": field, "time_grain": "month"}]
                        ),
                    },
                    {
                        "query": "Switch to yearly buckets; keep that date field and population.",
                        "expected": expected(
                            [{"operation": "time_series", "time_field": field, "time_grain": "year"}]
                        ),
                    },
                ],
            )
        add(
            "boundary_empty_time",
            [
                {
                    "query": prefix
                    + "only include start dates fully within 1900-01-01 through 1901-12-31, and show annual start counts including zero years.",
                    "expected": expected(
                        [{"operation": "time_series", "time_field": "start", "time_grain": "year"}],
                        date_range={"field": "start", "lower": "1900-01-01", "upper": "1901-12-31"},
                    ),
                }
            ],
        )
        add(
            "boundary_unsupported_causality",
            [
                {
                    "query": prefix
                    + "infer the causal survival benefit versus placebo using registry metadata alone and choose the best treatment for an individual patient.",
                    "expected": {"status": "limited", "no_charts": True},
                }
            ],
        )
        if group_index >= 5:
            add(
                "boundary_precision",
                [
                    {
                        "query": prefix
                        + "plot duration in exact calendar days against enrollment, exclude missing or imprecise dates and report exclusions without imputation.",
                        "expected": expected(
                            [{"operation": "scatter", "x": "trial_duration", "y": "enrollment"}]
                        ),
                    }
                ],
            )
        assert sum(e["entity_group"] == disease for e in episodes) == 20
    assert len(episodes) == 300
    report = {
        "version": "core300-v1",
        "episodes": episodes,
        "split_audit": {
            "counts": dict(Counter(e["split"] for e in episodes)),
            "entity_groups": GROUPS,
            "policy": "Disease+medicine groups stay together. 10 heldout groups are new to the prior 72-episode suite. Templates recur; this is not 300 independently authored questions.",
            "categories": {"single": 120, "compound_or_dependent": 80, "multi_turn": 60, "boundary": 40},
            "recovery": "Crash/recovery and comparison mutations are separate deterministic tests, not inflated into this episode count.",
        },
    }
    write(root / "cases.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/core300-frozen"))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download:
        acquire(args.root)
    print(json.dumps(build(args.root)["split_audit"], indent=2))
