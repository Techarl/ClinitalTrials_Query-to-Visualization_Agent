from copy import deepcopy
from uuid import uuid4

import pytest

from app.analysis import AnalysisEngine, resolve_pointer
from app.config import Settings
from app.graphdb import GraphRepository
from app.models import AnalysisPlan, Cohort, Filters
from app.sources import Snapshot, content_hash, utc_now
from app.storage import StateStore


@pytest.fixture
def graph_data(request, studies, tmp_path):
    if not request.config.getoption("--live-neo4j"):
        pytest.skip("Enable --live-neo4j for temporary graph fixtures")
    settings = Settings()
    repository = GraphRepository(settings)
    repository.setup()
    dataset_ids = []

    def add(raw):
        dataset_id = "ds_" + uuid4().hex
        dataset_ids.append(dataset_id)
        snapshot = Snapshot(
            dataset_id=dataset_id,
            query={"query.cond": "synthetic fixture"},
            query_hash="fixture",
            started_at=utc_now(),
            finished_at=utc_now(),
            expected_count=len(raw),
            retrieved_unique_count=len(raw),
            complete=True,
            raw_hash=content_hash(raw),
        )
        repository.import_snapshot(snapshot, raw)
        repository.import_snapshot(snapshot, raw)  # Same immutable import is idempotent.
        return snapshot, raw

    first = add(studies)
    alternate = deepcopy(studies)
    alternate[0]["protocolSection"]["designModule"]["phases"] = ["PHASE4"]
    add(alternate)
    store = StateStore(tmp_path)
    yield AnalysisEngine(settings, repository, store), store, first
    # Only delete fixture snapshots created by this test, never application datasets.
    repository.query("MATCH (r:TrialRecord) WHERE r.dataset_id IN $ids DETACH DELETE r", {"ids": dataset_ids})
    repository.query(
        "MATCH (d:DatasetSnapshot) WHERE d.dataset_id IN $ids DETACH DELETE d", {"ids": dataset_ids}
    )
    repository.close()


def execute(graph_data, **kwargs):
    engine, store, dataset = graph_data
    filters = kwargs.pop("filters", Filters(condition="synthetic fixture"))
    plan = AnalysisPlan(cohorts=[Cohort(id="fixture", label="Fixture", filters=filters)], **kwargs)
    return engine.run(plan, {"fixture": dataset}, "fixture_run"), store, dataset


def test_group_counts_and_exact_evidence_are_snapshot_scoped(graph_data):
    (visual, meta, provenance, empty), store, (_, raw) = execute(
        graph_data, operation="category", group_by="phase"
    )
    assert not empty
    assert {row.category: row.trial_count for row in visual.data} == {
        "Phase 1 / Phase 2": 1,
        "Phase 3": 1,
        "Not reported": 1,
    }
    assert meta["cohorts"][0]["selected_trial_count"] == 3
    raw_index = {s["protocolSection"]["identificationModule"]["nctId"]: s for s in raw}
    for preview_id in provenance["evidence"]:
        page = store.evidence_page(preview_id)
        for citation in page["citations"]:
            for field in citation["fields"]:
                assert resolve_pointer(raw_index[citation["nct_id"]], field["path"]) == field["value"]


def test_country_duplicates_do_not_inflate_counts(graph_data):
    (visual, _, _, _), _, _ = execute(graph_data, operation="category", group_by="country")
    assert {row.category: row.trial_count for row in visual.data} == {
        "United States": 2,
        "Canada": 1,
        "Not reported": 1,
    }


def test_zero_enrollment_is_not_missing(graph_data):
    (visual, meta, _, _), _, _ = execute(graph_data, operation="histogram", histogram_bins=2)
    assert [row.trial_count for row in visual.data] == [1, 1]
    assert meta["excluded_missing_values"] == 1


def test_country_filter_selects_cohort_and_country_chart_categories(graph_data):
    (visual, meta, _, _), _, _ = execute(
        graph_data,
        operation="category",
        group_by="country",
        filters=Filters(condition="synthetic fixture", countries=["Canada"]),
    )
    assert {row.category: row.trial_count for row in visual.data} == {"Canada": 1}
    assert meta["cohorts"][0]["selected_trial_count"] == 1


def test_time_series_has_zero_buckets_and_missing_dates_are_excluded(graph_data):
    (visual, meta, _, _), _, _ = execute(graph_data, operation="time_series")
    assert [(row.year, row.trial_count) for row in visual.data] == [(2020, 1), (2021, 0), (2022, 1)]
    assert meta["excluded_missing_values"] == 1


def test_network_preserves_biological_and_distinct_trials(graph_data):
    (visual, _, _, _), _, _ = execute(graph_data, operation="network", relationship="drug_cooccurrence")
    assert len(visual.data.edges) == 1
    assert visual.data.edges[0].weight == 1
    assert {node.label for node in visual.data.nodes} == {"Synthetic Pembro", "Synthetic Drug A"}


def test_multihop_uses_only_current_snapshot_and_distinguishes_edge_metric(graph_data):
    (visual, _, _, _), _, _ = execute(graph_data, operation="network", relationship="sponsor_shared_drug")
    assert len(visual.data.edges) == 1
    edge = visual.data.edges[0]
    assert edge.weight == 1
    assert edge.contributor_count == 2
    assert edge.weight_metric == "shared_intervention_count"
    assert edge.shared_interventions == ["Synthetic Pembro"]
    assert all(node.trial_count == 1 for node in visual.data.nodes)
