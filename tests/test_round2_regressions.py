import json
from bisect import bisect_right
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from openai import OpenAI
from pydantic import SecretStr

from app.analysis import AnalysisEngine
from app.cli import export_result
from app.config import Settings
from app.graphdb import GraphRepository
from app.models import AnalysisPlan, AnalysisRequest, Cohort, Filters, PlannerDecision
from app.providers import OpenAIGenerationClient
from app.sources import Snapshot, SnapshotStore, content_hash, source_query, utc_now
from app.storage import StateStore
from app.verify_examples import verify
from experiments.budget import Budget
from tests.test_agent import setup_runner
from tests.test_recovery_and_http import SimulatedProcessCrash
from tests.test_verifier_mutations import build_export


def enrollment_studies(studies, values):
    result = []
    for index, value in enumerate(values):
        raw = deepcopy(studies[0])
        p = raw["protocolSection"]
        p["identificationModule"]["nctId"] = f"NCT{index + 1:08}"
        if value is None:
            p["designModule"].pop("enrollmentInfo", None)
        else:
            p["designModule"]["enrollmentInfo"] = {"count": value, "type": "ACTUAL"}
        result.append(raw)
    return result


@pytest.mark.parametrize(
    "values,bins",
    [
        ([0, 9, 18], 14),
        ([0, 15], 11),
        ([0, 33, 44], 20),
        ([0, 0, 0], 5),
        ([None, None], 3),
        ([0, 1, 2], 14),
        ([0, 100, None, 100], 7),
        ([0, 7, 14], 7),
    ],
)
def test_histogram_declared_intervals_and_exactly_once(tmp_path, studies, values, bins):
    raw = enrollment_studies(studies, values)
    folder, response = build_export(tmp_path, raw, "histogram", bins=bins)
    expected = {
        r["protocolSection"]["identificationModule"]["nctId"]: v
        for r, v in zip(raw, values, strict=True)
        if v is not None
    }
    if expected:
        evidence = json.loads((folder / "response.evidence.json").read_text(encoding="utf-8"))
        seen = Counter()
        rows = response.visualization.data
        assert len(rows) == bins
        for index, row in enumerate(rows):
            if index:
                assert rows[index - 1].bin_end == row.bin_start
            for citation in evidence[row.evidence_set_id]["citations"]:
                value = expected[citation["nct_id"]]
                assert row.bin_start <= value, (value, row)
                assert value <= row.bin_end if index == bins - 1 else value < row.bin_end, (value, row)
                seen[citation["nct_id"]] += 1
        assert seen == Counter({key: 1 for key in expected})
        assert rows[-1].bin_end >= max(expected.values())
    else:
        assert response.status == "no_data"
    assert verify(folder, tmp_path)["status"] == "verified"


def test_histogram_invariant_survives_a_correlated_oracle_error(tmp_path, studies, monkeypatch):
    # Inject the original wrong boundary assignment in production and make the
    # expected-table oracle agree with it. Direct interval checks must still fail.
    monkeypatch.setattr(
        "app.analysis.bisect_right",
        lambda edges, value: 7 if value == 9 else bisect_right(edges, value),
        raising=False,
    )
    folder, response = build_export(tmp_path, enrollment_studies(studies, [0, 9, 18]), "histogram", bins=14)
    evidence = json.loads((folder / "response.evidence.json").read_text(encoding="utf-8"))
    expected = []
    for row in response.visualization.data:
        item = {
            key: value
            for key, value in row.model_dump().items()
            if key in ("id", "cohort", "category", "year", "bin_start", "bin_end")
        }
        item["ids"] = {c["nct_id"] for c in evidence[row.evidence_set_id]["citations"]}
        expected.append(item)
    monkeypatch.setattr(
        "app.verify_examples.expected_table", lambda *_: (expected, response.meta["presentation_clipping"], 0)
    )
    with pytest.raises(AssertionError, match="interval"):
        verify(folder, tmp_path)


def drug_plan(scope):
    groups = [["A"], ["B"]] if scope == "comparison" else [["A", "B"]]
    return AnalysisPlan(
        operation="category",
        cohorts=[
            Cohort(
                id=str(index),
                label=f"Group {index}",
                filters=Filters(condition="melanoma", interventions=drugs),
            )
            for index, drugs in enumerate(groups)
        ],
    )


@pytest.mark.parametrize("original,corrected", [("population", "comparison"), ("comparison", "population")])
def test_clarification_scope_correction_survives_another_question_restart_and_history(
    tmp_path, original, corrected
):
    clarify = PlannerDecision(action="clarify", mode="new", questions=["Pooled or separate?"])
    runner, service, _ = setup_runner(
        tmp_path,
        [
            clarify,
            PlannerDecision(action="clarify", mode="clarification", questions=["Confirm phase grouping?"]),
        ],
    )
    first = runner.analyze(
        AnalysisRequest(
            query="Resolve drug logic",
            filters=Filters(condition="melanoma", interventions=["A", "B"]),
            intervention_scope=original,
        )
    )
    corrected_run = runner.analyze(
        AnalysisRequest(
            query="Use this scope; please confirm grouping",
            session_id=first.session_id,
            intervention_scope=corrected,
        )
    )
    runner.close()
    runner, service, generator = setup_runner(
        tmp_path, [PlannerDecision(action="execute", mode="clarification", plan=drug_plan(corrected))]
    )
    response = runner.analyze(
        AnalysisRequest(query="Yes", session_id=first.session_id, parent_run_id=corrected_run.run_id)
    )
    assert response.status == "no_data"
    assert len(service.executed) == 1
    pending = generator.contexts[0]["pending_clarification"]
    assert pending["confirmed_intervention_scope"] == corrected
    assert "intervention_scope" not in generator.contexts[0]["request"]
    runner.close()


def test_current_scope_correction_reaches_execution(tmp_path):
    runner, service, _ = setup_runner(
        tmp_path,
        [
            PlannerDecision(action="clarify", mode="new", questions=["Which scope?"]),
            PlannerDecision(action="execute", mode="clarification", plan=drug_plan("comparison")),
        ],
    )
    first = runner.analyze(
        AnalysisRequest(
            query="Compare A vs B", filters=Filters(condition="melanoma", interventions=["A", "B"])
        )
    )
    result = runner.analyze(
        AnalysisRequest(query="Separate groups", session_id=first.session_id, intervention_scope="comparison")
    )
    assert result.status == "no_data" and len(service.executed) == 1
    runner.close()


@pytest.mark.parametrize("failure", ["invalid_json", "bad_sdk_shape", "unexpected", "timeout"])
def test_real_sdk_and_unexpected_provider_errors_keep_durable_attempt(tmp_path, failure):
    runner, service, _ = setup_runner(tmp_path, [])
    attempts = []

    def respond(request):
        attempts.append(request)
        with service.store.connect() as conn:
            row = conn.execute("SELECT trace FROM runs").fetchone()
        assert any(e["stage"] == "planner_request" for e in json.loads(row[0]))
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE_ERROR_MARKER", request=request)
        if failure == "unexpected":
            raise RuntimeError("PRIVATE_ERROR_MARKER")
        return httpx.Response(
            200,
            content=b"{" if failure == "invalid_json" else b"[]",
            headers={"content-type": "application/json"},
        )

    provider = OpenAIGenerationClient(
        runner.settings.model_copy(update={"openai_api_key": SecretStr("test-only-not-a-real-key")})
    )
    provider.client.close()
    provider.client = OpenAI(
        api_key="test-only-not-a-real-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    runner.generator = provider
    response = runner.analyze(AnalysisRequest(query="Melanoma phase counts"))
    final = service.store.run(response.run_id)
    assert len(attempts) == 1
    assert response.status == "failed" and response.meta["model_calls"] == 1
    assert response.meta["usage_complete"] is False
    assert sum(e["stage"] == "planner_request" for e in final["trace"]) == 1
    assert "PRIVATE_ERROR_MARKER" not in json.dumps(final)
    assert runner.resume(response.run_id).model_dump() == response.model_dump()
    runner.close()


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 1000},
        {"input_tokens": 1000, "output_tokens": 1},
        {"input_tokens": -1, "output_tokens": 1, "total_tokens": 0},
        {"input_tokens": True, "output_tokens": 1, "total_tokens": 2},
        {
            "input_tokens": 1000,
            "output_tokens": 1,
            "total_tokens": 1001,
            "input_tokens_details": {"cached_tokens": 1002, "cache_write_tokens": 0},
        },
    ],
)
def test_unknown_or_invalid_usage_never_releases_budget(usage):
    budget = Budget(max_usd=0.045)
    event = budget.before(1000)
    reserved = budget.estimated_usd
    budget.after(event, {"usage": usage})
    assert budget.estimated_usd == reserved
    assert event["status"] == "completed_usage_unknown"
    with pytest.raises(RuntimeError, match="budget"):
        budget.before(1000)


def test_complete_usage_settles_once_and_unknown_can_be_reconciled():
    budget = Budget()
    event = budget.before(1000)
    usage = {
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_tokens": 1100,
        "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
        "output_tokens_details": {"reasoning_tokens": 20},
    }
    budget.after(event, {"usage": None})
    budget.after(event, {"usage": usage})
    amount = budget.estimated_usd
    assert amount == pytest.approx(0.00267)
    assert event["status"] == "completed"
    budget.after(event, {"usage": usage})
    assert budget.estimated_usd == amount


def test_journal_survives_outer_checkpoint_failure_and_trace_overwrite(tmp_path):
    runner, service, generator = setup_runner(
        tmp_path, [PlannerDecision(action="execute", mode="new", plan=drug_plan("population"))]
    )
    original = runner.graph.invoke

    def fail_after_planning(state, config):
        runner._plan(state)
        service.store.record_trace(state["run_id"], [])
        raise RuntimeError("PRIVATE_ERROR_MARKER")

    runner.graph.invoke = fail_after_planning
    response = runner.analyze(AnalysisRequest(query="Melanoma A or B phase counts"))
    assert response.status == "failed" and response.meta["model_calls"] == 1
    assert response.meta["usage_complete"] is False
    assert len(service.store.model_journal(response.run_id)) == 2
    assert any(e["stage"] == "planner_request" for e in service.store.run(response.run_id)["trace"])
    assert "PRIVATE_ERROR_MARKER" not in json.dumps(service.store.run(response.run_id))
    assert len(generator.contexts) == 1
    runner.graph.invoke = original
    runner.close()


def test_durable_success_before_checkpoint_crash_is_reused_after_restart(tmp_path):
    runner, service, _ = setup_runner(
        tmp_path, [PlannerDecision(action="execute", mode="new", plan=drug_plan("population"))]
    )
    original = service.store.record_model_event

    def crash_after_outcome(run_id, event, trace=None):
        original(run_id, event, trace)
        if event["stage"] == "planner_response":
            raise SimulatedProcessCrash()

    service.store.record_model_event = crash_after_outcome
    with pytest.raises(SimulatedProcessCrash):
        runner.analyze(AnalysisRequest(query="Melanoma phase counts", session_id="outcome_crash"))
    with service.store.connect() as conn:
        run_id = conn.execute("SELECT id FROM runs").fetchone()[0]
    service.store.record_trace(run_id, [])
    runner.close()
    recovered, _, generator = setup_runner(tmp_path, [])
    response = recovered.resume(run_id)
    assert response.status == "no_data" and response.meta["model_calls"] == 1
    assert generator.contexts == []
    recovered.close()


def test_scope_correction_on_a_historical_clarification_branch(tmp_path):
    runner, service, _ = setup_runner(
        tmp_path,
        [
            PlannerDecision(action="clarify", mode="new", questions=["Scope?"]),
            PlannerDecision(action="execute", mode="new", plan=drug_plan("population")),
            PlannerDecision(action="execute", mode="clarification", plan=drug_plan("comparison")),
        ],
    )
    old = runner.analyze(
        AnalysisRequest(query="A vs B", filters=Filters(condition="melanoma", interventions=["A", "B"]))
    )
    runner.analyze(AnalysisRequest(query="New topic pooled drugs", session_id=old.session_id))
    response = runner.analyze(
        AnalysisRequest(
            query="Separate groups",
            session_id=old.session_id,
            parent_run_id=old.run_id,
            intervention_scope="comparison",
        )
    )
    assert response.status == "no_data" and len(service.executed) == 2
    recovered_scope = service.executed[-1]
    assert len(recovered_scope.cohorts) == 2
    runner.close()


def test_fractional_histogram_two_cohorts_on_real_neo4j(tmp_path, studies, request):
    if not request.config.getoption("--live-neo4j"):
        pytest.skip("Enable --live-neo4j for temporary graph fixtures")
    raw = enrollment_studies(studies, [0, 9, 18])
    for index, study in enumerate(raw):
        study["protocolSection"]["designModule"]["phases"] = ["PHASE2" if index < 2 else "PHASE3"]
    settings = Settings()
    repository = GraphRepository(settings)
    dataset_id = "ds_" + uuid4().hex
    filters = Filters(condition="synthetic fixture")
    query = source_query(filters)
    snapshot = Snapshot(
        dataset_id=dataset_id,
        query=query,
        query_hash=content_hash(query),
        started_at=utc_now(),
        finished_at=utc_now(),
        complete=True,
        expected_count=3,
        retrieved_unique_count=3,
        raw_hash=content_hash(raw),
    )
    store = StateStore(tmp_path / "state")
    try:
        repository.setup()
        repository.import_snapshot(snapshot, raw)
        SnapshotStore(tmp_path).save(snapshot, raw)
        plan = AnalysisPlan(
            operation="histogram",
            histogram_bins=14,
            cohorts=[
                Cohort(id=phase, label=phase, filters=Filters(condition="synthetic fixture", phases=[phase]))
                for phase in ("PHASE2", "PHASE3")
            ],
        )
        visual, meta, provenance, empty = AnalysisEngine(settings, repository, store).run(
            plan, {c.id: (snapshot, raw) for c in plan.cohorts}, "histogram_real_run"
        )
        from app.models import AnalysisResponse

        response = AnalysisResponse(
            status="no_data" if empty else "ok",
            run_id="histogram_real_run",
            session_id="histogram_real_session",
            visualization=visual,
            meta={**meta, "plan": plan.model_dump()},
            provenance=provenance,
        )
        folder = tmp_path / "export"
        export_result(response, SimpleNamespace(store=store), folder / "response.json")
        assert verify(folder, tmp_path)["status"] == "verified"
        assert [row.trial_count for row in visual.data if row.cohort == "PHASE2"][7] == 1
        assert [row.trial_count for row in visual.data if row.cohort == "PHASE3"][-1] == 1
    finally:
        repository.query("MATCH (r:TrialRecord {dataset_id:$id}) DETACH DELETE r", {"id": dataset_id})
        repository.query("MATCH (d:DatasetSnapshot {dataset_id:$id}) DETACH DELETE d", {"id": dataset_id})
        repository.close()
