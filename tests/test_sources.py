import time

import httpx
import pytest

from app.config import Settings
from app.models import AnalysisPlan, Cohort, FilterPatch, Filters, PlanPatch
from app.sources import ClinicalTrialsClient, SnapshotStore, source_query


def settings(**kwargs):
    return Settings(_env_file=None, source_retries=0, **kwargs)


def source_client(respond, **kwargs):
    return ClinicalTrialsClient(settings(**kwargs), httpx.Client(transport=httpx.MockTransport(respond)))


def test_pagination_continues_across_empty_pages_and_deduplicates(studies, tmp_path):
    seen = []

    def respond(request):
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"dataTimestamp": "stable"})
        seen.append(dict(request.url.params))
        token = request.url.params.get("pageToken")
        page = {
            None: {"totalCount": 2, "studies": [studies[0]], "nextPageToken": "a"},
            "a": {"studies": [], "nextPageToken": "b"},
            "b": {"studies": [studies[0], studies[1]]},
        }[token]
        return httpx.Response(200, json=page)

    query = source_query(Filters(condition="melanoma"))
    snapshot, raw = source_client(respond).fetch(query, time.monotonic() + 10)
    assert snapshot.complete
    assert snapshot.retrieved_unique_count == 2
    assert snapshot.duplicate_count == 1
    assert snapshot.pages == 3
    assert all(page["query.cond"] == query["query.cond"] for page in seen)
    store = SnapshotStore(tmp_path)
    store.save(snapshot, raw)
    assert store.load(snapshot.dataset_id)[1] == raw
    assert store.cached(query, 60)[0].dataset_id == snapshot.dataset_id
    with pytest.raises(FileExistsError):
        store.save(snapshot, raw)
    path = store.root / snapshot.dataset_id / "studies.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.load(snapshot.dataset_id)


@pytest.mark.parametrize(
    "mode,expected_reason",
    [
        ("drift", "source_version_changed_or_unavailable"),
        ("mismatch", "unique_count_mismatch"),
        ("budget", "record_budget_exceeded"),
    ],
)
def test_incomplete_datasets_are_explicit(studies, mode, expected_reason):
    version_calls = 0

    def respond(request):
        nonlocal version_calls
        if request.url.path.endswith("/version"):
            version_calls += 1
            return httpx.Response(
                200, json={"dataTimestamp": str(version_calls) if mode == "drift" else "stable"}
            )
        return httpx.Response(200, json={"totalCount": 3 if mode != "drift" else 1, "studies": [studies[0]]})

    snapshot, _ = source_client(respond, max_source_records=2 if mode == "budget" else 10).fetch(
        {"query.cond": "test"}, time.monotonic() + 10
    )
    assert not snapshot.complete
    assert expected_reason in snapshot.reason


def test_zero_results_are_a_complete_empty_snapshot():
    def respond(request):
        return httpx.Response(
            200,
            json={"dataTimestamp": "stable"}
            if request.url.path.endswith("/version")
            else {"totalCount": 0, "studies": []},
        )

    snapshot, raw = source_client(respond).fetch({"query.cond": "none"}, time.monotonic() + 10)
    assert snapshot.complete and raw == []


def test_filters_cannot_inject_source_search_expressions():
    with pytest.raises(ValueError):
        source_query(Filters(condition='melanoma" OR AREA[NCTId]NCT00000001'))
    assert source_query(Filters(condition="melanoma", phases=["PHASE3"])) == source_query(
        Filters(condition="melanoma")
    )


def test_plan_patch_preserves_clears_and_validates_conditions():
    plan = AnalysisPlan(
        operation="category",
        cohorts=[Cohort(id="a", label="A", filters=Filters(condition="melanoma", countries=["Canada"]))],
    )
    changed = PlanPatch(set_filters=FilterPatch(phases=["PHASE3"])).apply(plan)
    assert changed.cohorts[0].filters.condition == "melanoma"
    assert changed.cohorts[0].filters.countries == ["Canada"]
    cleared = PlanPatch(clear_fields=["countries"], operation="time_series").apply(changed)
    assert cleared.cohorts[0].filters.countries == []
    assert cleared.cohorts[0].filters.phases == ["PHASE3"]
    with pytest.raises(ValueError):
        PlanPatch(clear_fields=["phases"], set_filters=FilterPatch(phases=["PHASE2"])).apply(plan)
    with pytest.raises(ValueError):
        PlanPatch(clear_fields=["condition"]).apply(plan)
