"""Deliberately corrupt valid exports. Every mutation must fail the offline oracle."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.analysis import AnalysisEngine
from app.cli import export_result
from app.config import Settings
from app.graphdb import entity_key, normalize
from app.models import AnalysisPlan, AnalysisResponse, Cohort, Filters
from app.sources import Snapshot, SnapshotStore, content_hash, source_query, utc_now
from app.storage import StateStore
from app.verify_examples import verify


class SyntheticGraph:
    def __init__(self, studies, dataset_id):
        self.rows = [normalize(raw, dataset_id)["props"] for raw in studies]

    def records(self, dataset_id, filters):
        return [{"record": r} for r in self.rows]

    def groups(self, dataset_id, filters, dimension):
        groups = {}
        for row in self.rows:
            category = row["phase_label"] if dimension == "phase" else row[dimension]
            groups.setdefault(category, []).append(row["nct_id"])
        return [{"category": category, "contributors": ids} for category, ids in groups.items()]

    def network(self, dataset_id, filters, relationship):
        a = "Synthetic Fixture Sponsor A"
        b = "Synthetic Fixture Sponsor B"
        sides = sorted(
            [(entity_key("sponsor", a), a, ["NCT00000001"]), (entity_key("sponsor", b), b, ["NCT00000002"])]
        )
        return [
            {
                "source": sides[0][0],
                "source_label": sides[0][1],
                "source_type": "sponsor",
                "target": sides[1][0],
                "target_label": sides[1][1],
                "target_type": "sponsor",
                "shared_id": entity_key("intervention:BIOLOGICAL", "Synthetic Pembro"),
                "shared_name": "Synthetic Pembro",
                "left_contributors": sides[0][2],
                "right_contributors": sides[1][2],
            }
        ]


def build_export(tmp_path, studies, operation="category", bins=4):
    settings = Settings(_env_file=None, app_data_dir=tmp_path / "data")
    store = StateStore(settings.data_directory)
    source = SnapshotStore(tmp_path)
    filters = Filters(condition="synthetic fixture")
    query = source_query(filters)
    snapshot = Snapshot(
        dataset_id="ds_synthetic",
        query=query,
        query_hash=content_hash(query),
        started_at=utc_now(),
        finished_at=utc_now(),
        complete=True,
        expected_count=len(studies),
        retrieved_unique_count=len(studies),
        raw_hash=content_hash(studies),
    )
    source.save(snapshot, studies)
    plan = AnalysisPlan(
        operation=operation,
        cohorts=[Cohort(id="a", label="Fixture", filters=filters)],
        relationship="sponsor_shared_drug",
        histogram_bins=bins,
    )
    graph = SyntheticGraph(studies, snapshot.dataset_id)
    visual, meta, provenance, empty = AnalysisEngine(settings, graph, store).run(
        plan, {"a": (snapshot, studies)}, "test_run"
    )
    response = AnalysisResponse(
        status="no_data" if empty else "ok",
        run_id="test_run",
        session_id="test_session",
        visualization=None if empty else visual,
        meta={**meta, "plan": plan.model_dump()},
        provenance=provenance,
    )
    folder = tmp_path / "export"
    export_result(response, SimpleNamespace(store=store), folder / "response.json")
    return folder, response


@pytest.mark.parametrize("operation", ["category", "time_series", "histogram", "network"])
def test_valid_synthetic_exports_pass_full_oracle(tmp_path, studies, operation):
    folder, _ = build_export(tmp_path, studies, operation)
    assert verify(folder, tmp_path)["complete_item_sets_checked"]


@pytest.mark.parametrize(
    "mutation",
    [
        "all_rows",
        "one_row",
        "extra_row",
        "duplicate_row",
        "order",
        "count",
        "evidence_ref",
        "no_citation_fields",
        "source_field",
        "cohort_filters",
        "encoding_field",
        "network_encoding",
        "completeness_meta",
        "missing_meta",
        "status",
        "missing_zero_year",
        "bin_boundary",
        "node_fake",
        "node_count",
        "node_evidence",
        "node_missing",
        "edge_missing",
        "endpoint",
        "edge_metric",
        "role_contributors",
    ],
)
def test_schema_valid_corruption_is_rejected(tmp_path, studies, mutation):
    network = mutation.startswith(("node_", "edge_")) or mutation in (
        "endpoint",
        "role_contributors",
        "network_encoding",
    )
    operation = (
        "network"
        if network
        else "time_series"
        if mutation == "missing_zero_year"
        else "histogram"
        if mutation == "bin_boundary"
        else "category"
    )
    folder, response = build_export(tmp_path, studies, operation)
    assert verify(folder, tmp_path)["status"] == "verified"
    data = response.model_dump(mode="json")
    visual = data["visualization"]["data"]
    evidence_path = folder / "response.evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if mutation == "all_rows":
        visual.clear()
    elif mutation == "one_row":
        visual.pop()
    elif mutation in ("extra_row", "duplicate_row"):
        visual.append(deepcopy(visual[0]))
        if mutation == "extra_row":
            visual[-1]["category"] = "Fabricated category"
    elif mutation == "order":
        visual.reverse()
    elif mutation == "count":
        visual[0]["trial_count"] += 1
    elif mutation == "evidence_ref":
        visual[0]["evidence_set_id"] = "ev_nonexistent"
    elif mutation in ("no_citation_fields", "source_field"):
        citation = next(iter(evidence.values()))["citations"][0]
        if mutation == "no_citation_fields":
            citation["fields"] = []
        else:
            citation["fields"][0]["value"] = "fabricated"
    elif mutation == "cohort_filters":
        data["meta"]["cohorts"][0]["filters"]["phases"] = ["PHASE3"]
    elif mutation in ("encoding_field", "network_encoding"):
        data["visualization"]["encoding"]["weight" if network else "x"]["field"] = "fabricated_field"
    elif mutation == "completeness_meta":
        data["meta"]["completeness"] = "incomplete"
    elif mutation == "missing_meta":
        data["meta"]["cohorts"][0]["missing_start_year"] = 0
    elif mutation == "status":
        data["status"] = "no_data"
    elif mutation == "missing_zero_year":
        visual[:] = [row for row in visual if row["trial_count"]]
    elif mutation == "bin_boundary":
        visual[0]["bin_end"] += 1
    elif mutation == "node_fake":
        visual["nodes"].append(
            {**visual["nodes"][0], "id": "fake", "label": "Imaginary sponsor", "trial_count": 99999}
        )
    elif mutation == "node_count":
        visual["nodes"][0]["trial_count"] += 1
    elif mutation == "node_evidence":
        visual["nodes"][0]["evidence_set_id"] = "ev_missing"
    elif mutation == "node_missing":
        visual["nodes"].pop()
    elif mutation == "edge_missing":
        visual["edges"].clear()
    elif mutation == "endpoint":
        visual["edges"][0]["source"] = "nonexistent"
    elif mutation == "edge_metric":
        visual["edges"][0]["weight_metric"] = "distinct_trial_count"
    elif mutation == "role_contributors":
        node = visual["nodes"][0]
        node["evidence_set_id"] = visual["edges"][0]["evidence_set_id"]
    AnalysisResponse.model_validate(data)  # These are semantic faults, not invalid JSON/schema.
    (folder / "response.json").write_text(json.dumps(data), encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises((AssertionError, KeyError)):
        verify(folder, tmp_path)


def test_all_missing_enrollment_reports_exclusion_without_zero_imputation(tmp_path, studies):
    raw = deepcopy(studies)
    for study in raw:
        study["protocolSection"]["designModule"].pop("enrollmentInfo", None)
    folder, response = build_export(tmp_path, raw, "histogram")
    assert response.status == "no_data"
    assert response.meta["excluded_missing_values"] == 3
    assert response.meta["cohorts"][0]["missing_enrollment"] == 3
    assert verify(folder, tmp_path)["status"] == "verified"


def test_no_data_cannot_hide_unreferenced_evidence(tmp_path, studies):
    raw = deepcopy(studies)
    for study in raw:
        study["protocolSection"]["designModule"].pop("enrollmentInfo", None)
    folder, response = build_export(tmp_path, raw, "histogram")
    response.provenance["evidence"] = {
        "ev_fake": {
            "contributor_count": 0,
            "citations": [],
            "preview_complete": True,
            "url": "/evidence/ev_fake",
        }
    }
    (folder / "response.json").write_text(response.model_dump_json(), encoding="utf-8")
    (folder / "response.evidence.json").write_text(
        json.dumps({"ev_fake": {"rule": {}, "total_contributors": 0, "citations": []}}), encoding="utf-8"
    )
    with pytest.raises(AssertionError):
        verify(folder, tmp_path)
