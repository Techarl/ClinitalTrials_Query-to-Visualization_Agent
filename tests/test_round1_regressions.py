import sqlite3

import pytest

from app.agent import AgentRunner
from app.models import AnalysisPlan, AnalysisRequest, Cohort, FilterPatch, Filters, PlannerDecision, PlanPatch
from app.sources import Snapshot, SnapshotStore, content_hash, utc_now
from tests.test_agent import plan, setup_runner
from tests.test_recovery_and_http import SimulatedProcessCrash


@pytest.mark.parametrize(
    "match,scope,allowed",
    [
        ("all", "population", False),
        ("any", "population", False),
        ("any", "comparison", True),
        ("all", "comparison", False),
    ],
)
def test_intervention_population_cannot_be_split(match, scope, allowed):
    request = AnalysisRequest(
        query="two drugs",
        filters=Filters(condition="melanoma", interventions=["A", "B"], intervention_match=match),
        intervention_scope=scope,
    )
    split = AnalysisPlan(
        operation="category",
        cohorts=[
            Cohort(
                id=n,
                label=n,
                filters=Filters(condition="melanoma", interventions=[n], intervention_match=match),
            )
            for n in ["A", "B"]
        ],
    )
    assert (not AgentRunner._explicit_conflicts(request, split)) is allowed
    whole = plan(interventions=["A", "B"], intervention_match=match)
    if scope == "population":
        assert AgentRunner._explicit_conflicts(request, whole) == []


def test_two_clarifications_survive_restart_and_block_wrong_condition(tmp_path):
    runner, service, _ = setup_runner(
        tmp_path,
        [
            PlannerDecision(action="clarify", mode="new", questions=["Which drug?"]),
            PlannerDecision(action="clarify", mode="clarification", questions=["Full name?"]),
        ],
    )
    first = runner.analyze(
        AnalysisRequest(
            query="Phase counts for melanoma and this drug", filters=Filters(condition="melanoma")
        )
    )
    second = runner.analyze(AnalysisRequest(query="pembro", session_id=first.session_id))
    runner.close()
    runner, service, generator = setup_runner(
        tmp_path,
        [
            PlannerDecision(
                action="execute", mode="clarification", plan=plan("asthma", interventions=["pembrolizumab"])
            )
        ],
    )
    result = runner.analyze(
        AnalysisRequest(query="pembrolizumab", session_id=first.session_id, parent_run_id=second.run_id)
    )
    pending = generator.contexts[0]["pending_clarification"]
    assert pending["request"]["filters"] == {"condition": "melanoma"}
    assert pending["replies"][0]["query"] == "pembro"
    assert pending["confirmed_filters"] == {"condition": "melanoma"}
    assert result.status == "needs_clarification"
    assert service.executed == []
    runner.close()


def test_historical_clarification_restores_its_branch(tmp_path):
    runner, service, generator = setup_runner(
        tmp_path,
        [
            PlannerDecision(action="clarify", mode="new", questions=["Drug?"]),
            PlannerDecision(action="execute", mode="new", plan=plan("asthma")),
            PlannerDecision(
                action="execute", mode="clarification", plan=plan(interventions=["pembrolizumab"])
            ),
        ],
    )
    old = runner.analyze(AnalysisRequest(query="melanoma this drug", filters=Filters(condition="melanoma")))
    runner.analyze(AnalysisRequest(query="New topic asthma", session_id=old.session_id))
    result = runner.analyze(
        AnalysisRequest(query="pembrolizumab", session_id=old.session_id, parent_run_id=old.run_id)
    )
    assert result.status == "no_data"
    assert generator.contexts[-1]["pending_clarification"]["request"]["filters"]["condition"] == "melanoma"
    assert generator.contexts[-1]["previous_plan"] is None
    runner.close()


def test_finalize_transaction_rolls_back_session_on_run_write_failure(tmp_path):
    runner, service, _ = setup_runner(tmp_path, [])
    sid, version, _ = service.store.session("atomic")
    rid = service.store.start_run(sid, None, {"query": "test"})
    with service.store.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_finalize BEFORE UPDATE OF response ON runs BEGIN SELECT RAISE(ABORT, 'injected crash'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.store.finalize_run(
            rid, version, {"last_run_id": rid}, {"status": "ok"}, [], advance_session=True
        )
    assert service.store.session(sid)[1:] == (0, {})
    assert service.store.run(rid)["response"] is None
    with service.store.connect() as conn:
        conn.execute("DROP TRIGGER fail_finalize")
    response = {"status": "ok"}
    assert (
        service.store.finalize_run(rid, version, {"last_run_id": rid}, response, [], advance_session=True)
        == response
    )
    assert (
        service.store.finalize_run(rid, version, {}, {"status": "failed"}, [], advance_session=True)
        == response
    )
    assert service.store.session(sid)[1] == 1
    runner.close()


@pytest.mark.parametrize("after_commit", [False, True])
def test_crash_at_finalize_boundary_recovers_without_replanning(tmp_path, after_commit):
    runner, service, _ = setup_runner(tmp_path, [PlannerDecision(action="execute", mode="new", plan=plan())])
    original = service.store.finalize_run

    def crash(*args, **kwargs):
        if after_commit:
            original(*args, **kwargs)
        raise SimulatedProcessCrash()

    service.store.finalize_run = crash
    with pytest.raises(SimulatedProcessCrash):
        runner.analyze(AnalysisRequest(query="melanoma phase", session_id="boundary"))
    with service.store.connect() as conn:
        rid = conn.execute("SELECT id FROM runs").fetchone()[0]
    runner.close()
    recovered, _, generator = setup_runner(tmp_path, [])
    result = recovered.resume(rid)
    assert result.status == "no_data"
    assert generator.contexts == []
    assert recovered.resume(rid).model_dump() == result.model_dump()
    recovered.close()


def test_corrupt_manifest_and_raw_do_not_poison_other_cache(tmp_path, caplog):
    store = SnapshotStore(tmp_path)
    broken = store.root / "ds_broken"
    broken.mkdir()
    (broken / "manifest.json").write_text('{"dataset_id":', encoding="utf-8")
    query = {"query.cond": '"asthma"'}
    snap = Snapshot(
        dataset_id="ds_good",
        query=query,
        query_hash=content_hash(query),
        started_at=utc_now(),
        finished_at=utc_now(),
        complete=True,
        expected_count=0,
        raw_hash=content_hash([]),
    )
    store.save(snap, [])
    assert store.cached(query, 60)[0].dataset_id == "ds_good"
    assert store.cached({"query.cond": "different"}, 60) is None
    (store.root / "ds_good" / "studies.json").write_text("broken", encoding="utf-8")
    assert store.cached(query, 60) is None
    assert "Ignoring corrupt snapshot" in caplog.text


def test_patch_invalidates_old_narrative_and_supports_presentation_changes():
    before = plan()
    before.assumptions = ["No date bounds; grouped by phase"]
    after = PlanPatch(
        operation="time_series",
        set_filters=FilterPatch(phases=["PHASE3"], start_year=2015, end_year=2025),
        top_n=5,
        histogram_bins=20,
    ).apply(before)
    assert after.assumptions == []
    assert after.top_n == 5 and after.histogram_bins == 20
    assert after.cohorts[0].filters.condition == "melanoma"
