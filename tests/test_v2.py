import json
import time
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Filters
from app.service import AnalysisService
from app.sources import Snapshot, SnapshotStore, content_hash, source_query, utc_now
from app.storage import StateStore
from app.v2.agent import V2Agent, usage_report
from app.v2.charts import temporal_or_scatter
from app.v2.contracts import AnalysisInput, Artifact, Coverage, DateRange, Goal, RunRequest, TaskSpec
from app.v2.dates import in_range, numeric_value, source_date
from app.v2.entities import resolve
from app.v2.pubmed import PubMedClient
from app.v2.store import V2Store
from app.v2.tools import ToolRegistry
from app.v2.verify import verify_extended
from evaluation.replay import FrozenSource, ReplayCoverageMissing


def task(**values):
    return TaskSpec(
        goal="Synthetic analysis",
        filters=Filters(condition="Synthetic condition"),
        goals=[Goal(id="chart", description="Phase counts", operation="category")],
        **values,
    )


def snapshot_for(studies):
    query = source_query(Filters(condition="Synthetic condition"))
    return Snapshot(
        dataset_id="ds_" + uuid4().hex,
        query=query,
        query_hash=content_hash(query),
        started_at=utc_now(),
        finished_at=utc_now(),
        expected_count=len(studies),
        retrieved_unique_count=len(studies),
        complete=True,
        raw_hash=content_hash(studies),
    )


def test_date_precision_and_contained_range(studies):
    year = studies[1]
    assert source_date(year, "start")["precision"] == "year"
    assert not in_range(year, DateRange(lower=date(2022, 2, 1)))
    assert in_range(year, DateRange(lower=date(2022, 1, 1), upper=date(2022, 12, 31)))
    assert numeric_value(year, "trial_duration")[0] is None
    day = deepcopy(studies[0])
    day["protocolSection"]["statusModule"]["startDateStruct"] = {"date": "2020-02-28", "type": "ACTUAL"}
    day["protocolSection"]["statusModule"]["completionDateStruct"] = {"date": "2020-03-01", "type": "ACTUAL"}
    assert numeric_value(day, "trial_duration") == (2, "ACTUAL", None)


@pytest.mark.parametrize("operation", ["time_series", "scatter"])
def test_extended_oracle_rejects_dropped_points_and_wrong_coordinates(tmp_path, studies, operation):
    studies[0]["protocolSection"]["statusModule"]["startDateStruct"]["date"] = "2020-01-01"
    studies[0]["protocolSection"]["statusModule"]["completionDateStruct"] = {
        "date": "2020-04-01",
        "type": "ACTUAL",
    }
    snapshot = snapshot_for(studies)
    scopes = [({"label": "test"}, snapshot, studies)]
    store = StateStore(tmp_path)
    args = AnalysisInput(scope_ids=["scope"], goal_id="chart", operation=operation, time_grain="month")
    visual, meta, ids = temporal_or_scatter(args, scopes, store, "r")
    artifact = Artifact(
        artifact_id="a",
        result_id="r",
        goal_id="chart",
        source_type="clinicaltrials",
        title="t",
        metric="m",
        visualization=visual,
        metadata=meta,
        evidence_ids=ids,
        coverage=Coverage(
            statistics_basis="complete_records",
            count_exactness="exact",
            population_coverage="complete",
            evidence_coverage="complete_contributors",
            fetched_count=3,
        ),
    )
    evidence = {ev: {c["nct_id"] for c in store.evidence_page(ev, 0, 100)["citations"]} for ev in ids}
    verify_extended(artifact, scopes, evidence)
    assert meta["cohorts"][0]["excluded_count"] == 2
    bad = artifact.model_copy(deep=True)
    bad.visualization["data"] = []
    with pytest.raises(ValueError, match="Missing"):
        verify_extended(bad, scopes, evidence)
    bad = artifact.model_copy(deep=True)
    bad.visualization["data"][0]["x" if operation == "scatter" else "trial_count"] = 999
    with pytest.raises(ValueError):
        verify_extended(bad, scopes, evidence)


def test_entity_identity_fuzzy_rejection_and_combination():
    same = resolve(["Keytruda", "pembrolizumab"])
    assert same["same_entity_comparison"]
    assert same["resolutions"][0]["resolution_status"] == "alias"
    assert resolve(["pembrolizuma"])["resolutions"][0]["resolution_status"] == "unresolved"
    assert resolve(["pembrolizumab + chemotherapy"])["resolutions"][0]["resolution_status"] == "unresolved"
    rejected = same["resolutions"][0]["candidates"][0]["canonical_id"]
    assert resolve(["Keytruda"], rejected=[rejected])["resolutions"][0]["resolution_status"] == "unresolved"


def test_owned_assets_events_and_unknown_usage_reservations(tmp_path):
    store = V2Store(StateStore(tmp_path))
    state = store.create(RunRequest(query="test"), 60)
    handle = store.asset(state["session_id"], state["run_id"], "scope", {"answer": 42})
    with pytest.raises(ValueError, match="another session"):
        store.get(handle, "wrong", "scope")
    store.event(state["run_id"], "tool_started", {})
    assert [e["sequence"] for e in store.events(state["run_id"], 1)] == [2]
    store.begin(state["run_id"], "model:0", "model", {"reservation_usd": 0.2})
    store.complete(state["run_id"], "model:0", {"metadata": {"usage": None}})
    assert usage_report(store, state["run_id"])["estimated_or_reserved_usd"] == 0.2
    assert not usage_report(store, state["run_id"])["usage_complete"]
    state["status"] = "completed"
    store.save(state, finish=True)
    token = store.share(state["run_id"], {"readonly": True})
    assert store.shared(token)["readonly"]
    store.revoke(state["run_id"], token)
    with pytest.raises(KeyError):
        store.shared(token)


def test_replay_missing_is_not_empty(tmp_path, studies):
    snapshot = snapshot_for(studies)
    SnapshotStore(tmp_path).save(snapshot, studies)
    replay = FrozenSource(tmp_path)
    assert len(replay.fetch(snapshot.query, time.monotonic() + 1)[1]) == 3
    with pytest.raises(ReplayCoverageMissing, match="replay_coverage_missing"):
        replay.fetch({"query.cond": "different"}, time.monotonic() + 1)


def test_pubmed_real_parser_with_local_transport_and_correction(tmp_path):
    xml = '<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>A title</ArticleTitle><Abstract><AbstractText Label="RESULTS">NCT00000001 observed.</AbstractText></Abstract><PublicationTypeList><PublicationType>Clinical Trial</PublicationType></PublicationTypeList></Article><CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn"><PMID>456</PMID></CommentsCorrections></CommentsCorrectionsList></MedlineCitation></PubmedArticle></PubmedArticleSet>'
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return (
            httpx.Response(200, json={"esearchresult": {"idlist": ["123"], "count": "1"}})
            if "esearch" in request.url.path
            else httpx.Response(200, text=xml)
        )

    client = PubMedClient(
        Settings(_env_file=None), tmp_path, httpx.Client(transport=httpx.MockTransport(handler))
    )
    ids, count, _ = client.search("drug", 5, time.monotonic() + 10)
    articles, _ = client.articles(ids, time.monotonic() + 10)
    assert count == 1 and articles[0]["corrections"][0]["type"] == "RetractionIn"
    assert articles[0]["nct_mentions"] == ["NCT00000001"]
    client.search("drug", 5, time.monotonic() + 10)
    assert len(calls) == 2
    client.client.close()


class ScriptedPlanner:
    def __init__(self, actions):
        self.actions, self.calls = actions, 0

    def choose(self, context, definitions, deadline):
        payload = json.loads(context["payload"])
        action = self.actions[self.calls]
        self.calls += 1
        return action(payload) if callable(action) else action, {"usage": None}


def test_sse_resume_and_clarification_preserves_task(tmp_path):
    settings = Settings(_env_file=None, app_data_dir=tmp_path, v2_max_estimated_usd=5)
    application = create_app(settings)
    application.state.v2.service_factory = lambda: SimpleNamespace()
    planner = ScriptedPlanner(
        [
            {"name": "define_task", "arguments": task().model_dump()},
            {
                "name": "clarify_user",
                "arguments": {"questions": ["Which medicine?"], "missing_fields": ["interventions"]},
            },
        ]
    )
    application.state.v2.planner = planner
    with TestClient(application) as client:
        started = client.post("/v2/runs", json={"query": "Synthetic condition phase counts"}).json()
        rid = started["run_id"]
        application.state.v2.futures[rid].result(timeout=15)
        result = client.get("/v2/runs/" + rid).json()
        assert result["status"] == "needs_clarification"
        events = client.get(f"/v2/runs/{rid}/events").text
        assert "clarification_required" in events
        seq = application.state.v2.store.events(rid)[-1]["sequence"]
        reconnect = client.get(f"/v2/runs/{rid}/events", headers={"Last-Event-ID": str(seq)}).text
        assert "event: end" in reconnect and "event: update" not in reconnect
        assert client.post(f"/v2/runs/{rid}/resume").json()["run_id"] == rid
        assert planner.calls == 2


def test_real_graph_result_dependent_cohorts(tmp_path, studies, request):
    if not request.config.getoption("--live-neo4j"):
        pytest.skip("requires live Neo4j")
    settings = Settings(app_data_dir=tmp_path, v2_max_estimated_usd=5)
    frozen = tmp_path / "frozen"
    snapshot = snapshot_for(studies)
    SnapshotStore(frozen).save(snapshot, studies)
    service = AnalysisService(settings)
    store = V2Store(service.store)
    registry = ToolRegistry(settings, service, store, FrozenSource(frozen))
    definition = TaskSpec(
        goal="Rank sponsors sharing drugs with A, then compare their annual trials",
        filters=Filters(condition="Synthetic condition"),
        goals=[
            Goal(id="net", description="Shared-drug network", operation="network"),
            Goal(
                id="trend", description="Ranked sponsor trends", operation="time_series", depends_on=["net"]
            ),
        ],
    )
    actions = [
        {"name": "define_task", "arguments": definition.model_dump()},
        {"name": "search_trials", "arguments": {"filters": {"condition": "Synthetic condition"}}},
        lambda p: {
            "name": "analyze",
            "arguments": {
                "scope_ids": [p["observations"][-1]["scope_id"]],
                "goal_id": "net",
                "operation": "network",
                "relationship": "sponsor_shared_drug",
            },
        },
        lambda p: {
            "name": "select_entities",
            "arguments": {
                "scope_id": p["observations"][1]["scope_id"],
                "result_id": p["observations"][-1]["result_id"],
                "anchor_sponsor": "Synthetic Fixture Sponsor A",
                "top_n": 3,
            },
        },
        lambda p: {
            "name": "analyze",
            "arguments": {
                "scope_ids": [s["scope_id"] for s in p["observations"][-1]["selected"]],
                "labels": [s["sponsor_name"] for s in p["observations"][-1]["selected"]],
                "goal_id": "trend",
                "operation": "time_series",
            },
        },
        lambda p: {"name": "finalize_analysis", "arguments": {"result_ids": p["result_ids"]}},
    ]
    planner = ScriptedPlanner(actions)
    agent = V2Agent(settings, registry, planner)
    state = store.create(RunRequest(query="Rank and compare"), 120)
    try:
        result = agent.run(state["run_id"])
        assert result.status == "completed", store.run(state["run_id"])["observations"]
        assert len(result.artifacts) == 2
        network, trend = result.artifacts
        assert network.visualization["data"]["edges"][0]["weight"] == 1
        assert trend.visualization["data"][0]["trial_count"] == 1
        assert trend.visualization["data"][0]["cohort"] == "Synthetic Fixture Sponsor B"
        assert trend.dependencies == [network.result_id]
        assert len(registry.source.calls) == 1
        assert agent.run(state["run_id"]).model_dump() == result.model_dump()
        assert planner.calls == 6
        changed = deepcopy(store.run(state["run_id"]))
        changed["task"]["filters"]["countries"] = ["United States"]
        with pytest.raises(ValueError, match="constraint"):
            registry.validate(SimpleNamespace(handle=network.result_id), changed)
        changed = deepcopy(store.run(state["run_id"]))
        changed["task"]["goals"][1]["parameters"] = {"time_grain": "month"}
        with pytest.raises(ValueError, match="parameters"):
            registry.validate(SimpleNamespace(handle=trend.result_id), changed)
    finally:
        agent.close()
        ids = [folder.name for folder in service.snapshots.root.glob("ds_*")]
        service.graph.query("MATCH (r:TrialRecord) WHERE r.dataset_id IN $ids DETACH DELETE r", {"ids": ids})
        service.graph.query(
            "MATCH (d:DatasetSnapshot) WHERE d.dataset_id IN $ids DETACH DELETE d", {"ids": ids}
        )
        service.close()
