"""Task repairs invalidate stale tool errors using real SQLite journals and analysis tools."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import Filters
from app.sources import Snapshot, SnapshotStore, content_hash, source_query, utc_now
from app.storage import StateStore
from app.v2.agent import V2Agent
from app.v2.contracts import Goal, GoalParameters, RunRequest, TaskSpec
from app.v2.store import V2Store
from app.v2.tools import ToolRegistry, task_execution_hash


@pytest.fixture
def setup(tmp_path):
    settings = Settings(_env_file=None, app_data_dir=tmp_path)
    store = V2Store(StateStore(tmp_path))
    state = store.create(RunRequest(query="Show the annual trial trend."), 60)
    records = [{"protocolSection": {
        "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Synthetic fixture"},
        "statusModule": {"startDateStruct": {"date": "2020-02-01", "type": "ACTUAL"}},
    }}]
    query = source_query(Filters(condition="Synthetic condition"))
    snapshot = Snapshot(dataset_id="ds_taskcachefixture", query=query, query_hash=content_hash(query),
                        started_at=utc_now(), finished_at=utc_now(), expected_count=1,
                        retrieved_unique_count=1, complete=True, raw_hash=content_hash(records))
    snapshots = SnapshotStore(tmp_path / "snapshots")
    snapshots.save(snapshot, records)
    imports = []
    # No Neo4j/API: the real temporal computation and source/evidence validator run
    # on a synthetic snapshot; its unused graph projection is a recording adapter.
    graph = SimpleNamespace(import_snapshot=lambda *args: imports.append(args))
    registry = ToolRegistry(settings, SimpleNamespace(snapshots=snapshots, graph=graph), store)
    agent = V2Agent.__new__(V2Agent)
    agent.store, agent.registry, agent.settings = store, registry, settings
    task = TaskSpec(goal="Annual trend", filters=Filters(condition="Synthetic condition"),
                    goals=[Goal(id="g", description="Annual trend", operation="category")])
    scope = store.asset(state["session_id"], state["run_id"], "scope", {
        "dataset_id": snapshot.dataset_id, "filters": task.filters.model_dump(), "date_range": None,
        "nct_ids": ["NCT00000001"], "lineage": [], "label": "Population",
    })
    args = {"goal_id": "g", "scope_ids": [scope], "operation": "time_series", "time_grain": "year"}
    return agent, registry, state, task, args, imports


def define(agent, state, task):
    agent.define_task(task, state)
    agent.store.save(state)


def test_goal_repair_reexecutes_cached_error_then_unchanged_task_reuses_success(setup):
    agent, registry, state, task, args, imports = setup
    define(agent, state, task)
    failure = registry.call("analyze", args, state)
    assert failure["status"] == "error" and "declared task goal" in failure["message"]
    assert registry.call("analyze", args, state)["reused"]
    assert not imports
    fixed = task.model_copy(deep=True)
    fixed.goals[0].operation = "time_series"
    define(agent, state, fixed)
    assert state["constraints_version"] == 2
    result = registry.call("analyze", args, state)
    assert result["status"] == "ok" and not result.get("reused"), result
    assert registry.artifact(result["result_id"], state).visualization["data"][0]["trial_count"] == 1
    assert registry.call("analyze", args, state)["reused"]
    entries = [e for e in agent.store.journal_entries(state["run_id"]) if e["kind"] == "tool"]
    assert len(entries) == 2 and sum(e["attempts"] for e in entries) == 2
    assert len(imports) == 1


def test_parameter_repair_invalidates_error_and_old_result_satisfaction(setup):
    agent, registry, state, task, args, _ = setup
    task.goals[0].operation = "time_series"
    task.goals[0].parameters = GoalParameters(time_grain="month")
    define(agent, state, task)
    assert registry.call("analyze", args, state)["status"] == "error"
    fixed = task.model_copy(deep=True)
    fixed.goals[0].parameters.time_grain = "year"
    define(agent, state, fixed)
    result = registry.call("analyze", args, state)
    assert result["status"] == "ok" and not result.get("reused"), result
    state["result_ids"] = [result["result_id"]]
    define(agent, state, task)
    assert state["result_ids"] == []
    assert state["scope_revisions"][-1]["superseded_result_ids"] == [result["result_id"]]
    # Even explicit reselection cannot use a year chart for the now-monthly goal.
    state["result_ids"] = [result["result_id"]]
    agent.finish(state, "completed", "Attempted finalization")
    assert state["status"] == "limited" and state["unmet_goals"] == ["g"]


def test_added_dependencies_change_identity_and_old_chart_cannot_complete_goal(setup):
    agent, registry, state, task, args, _ = setup
    task.goals.insert(0, Goal(id="prior", kind="entity", description="Required identity"))
    task.goals[1].operation = "time_series"
    define(agent, state, task)
    result = registry.call("analyze", args, state)
    assert result["status"] == "ok", result
    state["result_ids"] = [result["result_id"]]
    fixed = task.model_copy(deep=True)
    fixed.goals[1].depends_on = ["prior"]
    define(agent, state, fixed)
    assert state["result_ids"] == [] and state["constraints_version"] == 2
    retry = registry.call("analyze", args, state)
    assert not retry.get("reused")
    state["result_ids"] = [result["result_id"]]
    agent.finish(state, "completed", "Attempted finalization")
    assert state["status"] == "partial" and "g" in state["unmet_goals"]


def test_registry_signature_guards_task_change_even_without_version_increment(setup):
    agent, registry, state, task, args, _ = setup
    define(agent, state, task)
    assert registry.call("analyze", args, state)["status"] == "error"
    state["task"]["goals"][0]["operation"] = "time_series"
    # Direct registry integrations may supply a new contract but retain the counter.
    agent.store.save(state)
    result = registry.call("analyze", args, state)
    assert result["status"] == "ok" and not result.get("reused"), result


def test_presentation_changes_preserve_version_artifacts_and_journal_dedup(setup):
    agent, registry, state, task, args, imports = setup
    task.goals[0].operation = "time_series"
    define(agent, state, task)
    result = registry.call("analyze", args, state)
    assert result["status"] == "ok", result
    state["result_ids"] = [result["result_id"]]
    revised = task.model_copy(deep=True)
    revised.goal = "Readable title"
    revised.goals[0].description = "Rephrased description"
    define(agent, state, revised)
    assert state["constraints_version"] == 1 and state["result_ids"] == [result["result_id"]]
    assert registry.call("analyze", args, state)["reused"] and len(imports) == 1
    normalized = deepcopy(state["task"])
    normalized["goals"][0]["parameters"].pop("y")
    assert task_execution_hash(normalized) == task_execution_hash(revised)
