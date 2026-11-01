"""500 deterministic mutations; these are not 500 model/agent questions.

Grid: 200 temporal (4 fields × 2 grains × 5 calendar windows × 5 corruptions),
180 histograms (10 distributions × 6 bin counts × 3 corruptions),
96 graphs (8 topologies × 3 presentation limits × 4 corruptions),
24 comparisons (3 cohort cardinalities × 8 semantic corruptions).
"""

import json
from copy import deepcopy
from itertools import combinations
from types import SimpleNamespace

import pytest

from app.storage import StateStore
from app.v2.charts import temporal_or_scatter
from app.v2.contracts import AnalysisInput
from app.v2.tools import ToolRegistry
from app.v2.verify import verify_extended
from app.verify_examples import verify
from evaluation.sql_oracle import identity, sponsor_edges, verify_sql_artifact
from tests.test_v2 import snapshot_for
from tests.test_verifier_mutations import build_export

FIELDS = {
    "start": "startDateStruct",
    "completion": "completionDateStruct",
    "primary_completion": "primaryCompletionDateStruct",
    "first_posted": "studyFirstPostDateStruct",
}
WINDOWS = [
    ("1998-01-01", "2001-12-31", ["1999-12-31", "2000-01-01"], 4),
    ("1999-01-01", "2001-12-31", ["2000-02-28", "2000-02-29"], 3),
    ("2018-01-01", "2021-12-31", ["2019-12-31", "2020-01-01"], 4),
    ("2019-01-01", "2021-12-31", ["2020-02-29", "2020-03-01"], 3),
    ("2023-01-01", "2025-12-31", ["2024-01-01", "2024-12-31"], 3),
]


@pytest.mark.parametrize("field", list(FIELDS))
@pytest.mark.parametrize("grain", ["year", "month"])
@pytest.mark.parametrize("calendar", WINDOWS)
@pytest.mark.parametrize("mutation", ["drop_zero", "count", "contributor", "duplicate", "period"])
def test_temporal_mutation_is_rejected(tmp_path, studies, field, grain, calendar, mutation):
    lower, upper, source_dates, expected_years = calendar
    selected = deepcopy(studies[:2])
    for index, raw in enumerate(selected):
        raw["protocolSection"]["statusModule"][FIELDS[field]] = {
            "date": source_dates[index],
            "type": "ACTUAL",
        }
    scope = {
        "label": "Cohort A",
        "date_range": {"field": field, "lower": lower, "upper": upper},
    }
    scopes = [(scope, snapshot_for(selected), selected)]
    store = StateStore(tmp_path)
    args = AnalysisInput(
        scope_ids=["s"], goal_id="g", operation="time_series", time_field=field, time_grain=grain
    )
    visual, meta, ids = temporal_or_scatter(args, scopes, store, "r")
    # These fixture assertions are independent of both production and verifier math.
    assert len(visual["data"]) == (expected_years if grain == "year" else 12 * expected_years)
    assert sum(row["trial_count"] for row in visual["data"]) == 2
    assert visual["data"][0]["trial_count"] == visual["data"][-1]["trial_count"] == 0
    evidence = {ev: {c["nct_id"] for c in store.evidence_page(ev, 0, 100)["citations"]} for ev in ids}
    artifact = SimpleNamespace(metadata=meta, visualization=visual)
    verify_extended(artifact, scopes, evidence)
    rows = visual["data"]
    nonempty = next(row for row in rows if row["trial_count"])
    if mutation == "drop_zero":
        rows.pop(0)
    elif mutation == "count":
        nonempty["trial_count"] += 1
    elif mutation == "contributor":
        evidence[nonempty["evidence_set_id"]] = {"NCT99999999", "NCT99999998"}
    elif mutation == "duplicate":
        rows.append(deepcopy(nonempty))
    else:
        rows[0]["category"] = "1899" if grain == "year" else "1899-01"
    with pytest.raises(ValueError):
        verify_extended(artifact, scopes, evidence)


DISTRIBUTIONS = [
    [0, 9, 9],
    [0, 1, 9],
    [9, 9, 9],
    [1, 2, 3],
    [0, 27, 81],
    [7, 20, 31],
    [999, 1000, 1001],
    [0, 100000, 100001],
    [3, 11, 19],
    [0, 0, 1],
]


@pytest.mark.parametrize("values", DISTRIBUTIONS)
@pytest.mark.parametrize("bins", [1, 2, 3, 7, 10, 50])
@pytest.mark.parametrize("mutation", ["drop_bin", "overlap_boundary", "wrong_contributor"])
def test_histogram_mutation_is_rejected(tmp_path, studies, values, bins, mutation):
    for raw, value in zip(studies, values, strict=True):
        raw["protocolSection"].setdefault("designModule", {})["enrollmentInfo"] = {
            "count": value,
            "type": "ACTUAL",
        }
    folder, response = build_export(tmp_path, studies, operation="histogram", bins=bins)
    assert verify(folder, tmp_path)["status"] == "verified"
    exported = response.model_dump(mode="json")
    rows = exported["visualization"]["data"]
    assert sum(row["trial_count"] for row in rows) == 3
    evidence_path = folder / "response.evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    nonempty = next(row for row in rows if row["trial_count"])
    if mutation == "drop_bin":
        rows.pop()
    elif mutation == "overlap_boundary":
        rows[0]["bin_end"] += 0.5
    else:
        ev = evidence[nonempty["evidence_set_id"]]
        # Remove an actual contributor while preserving a plausible unchanged chart count.
        ev["citations"].pop()
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    (folder / "response.json").write_text(json.dumps(exported), encoding="utf-8")
    with pytest.raises((AssertionError, ValueError, KeyError)):
        verify(folder, tmp_path)


def ring_studies(size):
    records = []
    for sponsor in range(size):
        drugs = ["Common", f"Ring {sponsor}", f"Ring {(sponsor - 1) % size}"]
        for repeat in range(2):
            records.append(
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": f"NCT{sponsor * 2 + repeat + 1:08d}"},
                        "sponsorCollaboratorsModule": {"leadSponsor": {"name": f"Sponsor {sponsor}"}},
                        "armsInterventionsModule": {
                            "interventions": [{"name": name, "type": "DRUG"} for name in drugs + ["Common"]]
                            + [{"name": f"Private {sponsor}", "type": "BIOLOGICAL"}]
                        },
                    }
                }
            )
    return records


@pytest.mark.parametrize("size", range(3, 11))
@pytest.mark.parametrize("limit", [1, 4, 100])
@pytest.mark.parametrize("mutation", ["weight", "drop_edge", "endpoint", "duplicate_edge"])
def test_sql_graph_mutation_is_rejected(size, limit, mutation):
    records = ring_studies(size)
    expected = {}
    for left, right in combinations(range(size), 2):
        pair = tuple(
            sorted([identity("sponsor", f"Sponsor {left}"), identity("sponsor", f"Sponsor {right}")])
        )
        expected[pair] = 2 if right - left == 1 or (left == 0 and right == size - 1) else 1
    # The graph family has a known closed form: a common drug plus one shared ring edge.
    assert sponsor_edges(records) == expected
    order = sorted(expected, key=lambda p: (-expected[p], *p))[:limit]
    rows = [{"source": a, "target": b, "weight": expected[a, b]} for a, b in order]
    artifact = SimpleNamespace(
        metadata={
            "analysis_parameters": {
                "operation": "network",
                "relationship": "sponsor_shared_drug",
                "top_n": limit,
            },
            "root_scopes": ["scope"],
        },
        visualization={"data": {"edges": rows}},
    )
    scope = {
        "filters": {},
        "dataset_id": "frozen",
        "nct_ids": [r["protocolSection"]["identificationModule"]["nctId"] for r in records],
    }
    registry = SimpleNamespace(
        scope=lambda *args: (scope, None, records),
        settings=SimpleNamespace(network_max_edges=100),
        service=SimpleNamespace(snapshots=SimpleNamespace(load=lambda *_: (None, records))),
    )
    assert verify_sql_artifact(artifact, registry, {})["checked"]
    if mutation == "weight":
        rows[0]["weight"] += 1
    elif mutation == "drop_edge":
        rows.pop()
    elif mutation == "endpoint":
        rows[0]["source"] = "sponsor:invented"
    else:
        rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError, match="SQL"):
        verify_sql_artifact(artifact, registry, {})


@pytest.mark.parametrize("count", [2, 3, 4])
@pytest.mark.parametrize(
    "mutation",
    ["omit", "duplicate", "mixed", "swap_labels", "unrequested", "extra", "combined", "omit_empty"],
)
def test_comparison_semantic_mutation_is_rejected(count, mutation):
    names = [f"Medicine {i}" for i in range(count)]
    state = {"task": {"intervention_scope": "comparison", "filters": {"interventions": names}}}
    scopes = [
        {
            "filters": {"interventions": [name]},
            "label": name,
            "nct_ids": [] if index == count - 1 else ["NCT00000001"],
        }
        for index, name in enumerate(names)
    ]
    labels = names[:]
    registry = ToolRegistry.__new__(ToolRegistry)
    registry.check_comparison(scopes, labels, state)
    if mutation in ("omit", "omit_empty"):
        scopes.pop(0 if mutation == "omit" else -1)
        labels.pop(0 if mutation == "omit" else -1)
    elif mutation == "duplicate":
        scopes[-1] = deepcopy(scopes[0])
    elif mutation == "mixed":
        scopes[0]["filters"]["interventions"] = names[:2]
    elif mutation == "swap_labels":
        labels[0], labels[1] = labels[1], labels[0]
    elif mutation == "unrequested":
        scopes[-1]["filters"]["interventions"] = ["Unrequested medicine"]
    elif mutation == "extra":
        scopes.append(deepcopy(scopes[0]))
        labels.append(labels[0])
    else:
        scopes = [{"filters": {"interventions": names}, "label": "Combined"}]
        labels = ["Combined"]
    with pytest.raises(ValueError, match="Comparison"):
        registry.check_comparison(scopes, labels, state)
