import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import Filters
from app.sources import Snapshot, content_hash, utc_now
from app.storage import StateStore
from app.v2.acquisition import fetch_partitioned
from app.v2.agent import V2Agent, usage_report
from app.v2.contracts import Goal, HandleInput, RunRequest, TaskSpec
from app.v2.store import V2Store
from app.v2.tools import ToolRegistry


def setup_agent(tmp_path, planner=None, **settings):
    config = Settings(_env_file=None, app_data_dir=tmp_path, **settings)
    store = V2Store(StateStore(tmp_path))
    registry = ToolRegistry(config, SimpleNamespace(), store)
    agent = V2Agent(config, registry, planner)
    state = store.create(RunRequest(query="phase counts"), 60)
    agent.define_task(
        TaskSpec(
            goal="phase counts",
            filters=Filters(condition="asthma"),
            goals=[Goal(id="g", description="phase")],
        ),
        state,
    )
    store.save(state)
    return agent, store, state


def test_current_explicit_scope_overrides_pending_and_clear_fields(tmp_path):
    agent, store, state = setup_agent(tmp_path)
    try:
        state["task"]["filters"].update(countries=["United States"], interventions=["A", "B"])
        state["status"] = "needs_clarification"
        store.save(state, finish=True)
        child = store.create(
            RunRequest(
                query="Compare separately; remove US",
                session_id=state["session_id"],
                intervention_scope="comparison",
            ),
            60,
        )
        agent.define_task(
            TaskSpec(
                goal="compare",
                mode="clarification",
                intervention_scope="comparison",
                clear_fields=["countries"],
                goals=[Goal(id="g", description="compare")],
            ),
            child,
        )
        assert child["task"]["intervention_scope"] == "comparison"
        assert child["task"]["filters"]["condition"] == "asthma"
        assert child["task"]["filters"]["countries"] == []
        assert child["task"]["filters"]["interventions"] == ["A", "B"]
    finally:
        agent.close()


def test_terminal_state_event_and_concurrent_branch_are_atomic(tmp_path):
    store = V2Store(StateStore(tmp_path))
    a = store.create(RunRequest(query="a"), 60)
    b = store.create(RunRequest(query="b", session_id=a["session_id"]), 60)
    for state in (a, b):
        state["status"] = "completed"
        store.save(state, finish=True)
        assert store.events(state["run_id"])[-1]["type"] == "run_completed"
    assert store.run(a["run_id"])["session_head_updated"]
    assert not store.run(b["run_id"])["session_head_updated"]
    count = len(store.events(a["run_id"]))
    store.save(a, finish=True)
    assert len(store.events(a["run_id"])) == count
    child = store.create(
        RunRequest(query="branch", session_id=a["session_id"], parent_run_id=b["run_id"]), 60
    )
    assert child["prior"]["request"]["query"] == "b"


def test_crash_after_tool_receipt_replays_without_duplicate_execution(tmp_path):
    agent, store, state = setup_agent(tmp_path)
    count = []

    def execute(args, state):
        count.append(1)
        return {"value": 42}

    agent.registry.register("probe", "test", HandleInput, execute)
    state["current_action"] = {"name": "probe", "arguments": {"handle": "x"}}
    store.save(state)
    original = store.complete

    def crash(*args):
        original(*args)
        raise KeyboardInterrupt("simulated power loss")

    store.complete = crash
    try:
        with pytest.raises(KeyboardInterrupt):
            agent.execute({"run_id": state["run_id"]})
        store.complete = original
        agent.execute({"run_id": state["run_id"]})
        assert len(count) == 1
        assert store.run(state["run_id"])["observations"][-1]["reused"]
        assert len(store.journal_entries(state["run_id"])) == 1
    finally:
        agent.close()


def test_crash_after_model_receipt_preserves_accounting_and_action(tmp_path):
    class Planner:
        calls = 0

        def choose(self, *args):
            self.calls += 1
            return {"name": "clarify_user", "arguments": {"questions": ["Which medicine?"]}}, {"usage": None}

    planner = Planner()
    agent, store, state = setup_agent(tmp_path, planner)
    original = store.complete

    def crash(*args):
        original(*args)
        raise KeyboardInterrupt()

    store.complete = crash
    try:
        with pytest.raises(KeyboardInterrupt):
            agent.choose({"run_id": state["run_id"]})
        store.complete = original
        agent.choose({"run_id": state["run_id"]})
        assert planner.calls == 1
        assert store.run(state["run_id"])["current_action"]["name"] == "clarify_user"
        assert usage_report(store, state["run_id"])["unknown_attempts"] == 1
    finally:
        agent.close()


@pytest.mark.parametrize("reason", ["cancel", "time", "money"])
def test_limits_stop_before_paid_call(tmp_path, reason):
    class Planner:
        def choose(self, *args):
            pytest.fail("Limit must be checked before paid call")

    agent, store, state = setup_agent(tmp_path, Planner())
    if reason == "cancel":
        store.cancel(state["run_id"])
    elif reason == "time":
        state["deadline_epoch"] = time.time() - 1
        store.save(state)
    else:
        store.begin(state["run_id"], "old", "model", {"reservation_usd": 2.0})
    try:
        result = agent.run(state["run_id"])
        assert result.status == ("cancelled" if reason == "cancel" else "limited")
    finally:
        agent.close()


class PartitionClient:
    settings = SimpleNamespace(max_source_records=10)

    def __init__(self, studies, failure=None):
        self.studies, self.failure, self.calls = studies, failure, 0

    def get(self, path, params, deadline):
        return (
            {"dataTimestamp": "v1"}
            if path == "/version"
            else {"totalCount": len(self.studies), "studies": []}
        )

    def fetch(self, query, deadline):
        i = self.calls
        self.calls += 1
        records = [self.studies[i]] if i < len(self.studies) else []
        if self.failure == "overlap" and i == 1:
            records = [self.studies[0]]
        snapshot = Snapshot(
            dataset_id="ds_test",
            query=query,
            query_hash=content_hash(query),
            started_at=utc_now(),
            finished_at=utc_now(),
            source_version_before={"dataTimestamp": "v1"},
            source_version_after={"dataTimestamp": "v2" if self.failure == "version" else "v1"},
            expected_count=len(records),
            retrieved_unique_count=len(records),
            complete=self.failure != "partial",
            raw_hash=content_hash(records),
        )
        return snapshot, deepcopy(records)


def test_partition_union_includes_complement_and_preserves_query(studies):
    client = PartitionClient(studies)
    snapshot, records, audit = fetch_partitioned(
        client, {"query.cond": "x"}, time.monotonic() + 60, threshold=1
    )
    assert snapshot.complete and len(records) == 3
    assert audit["partitions"][-1]["predicate"].startswith("NOT (")
    assert all(p["query"]["query.cond"] == "x" for p in audit["partitions"])


@pytest.mark.parametrize("failure", ["overlap", "version", "partial"])
def test_partition_inconsistency_cannot_publish(studies, failure):
    with pytest.raises(ValueError):
        fetch_partitioned(
            PartitionClient(studies, failure), {"query.cond": "x"}, time.monotonic() + 60, threshold=1
        )


def test_task_dependency_cannot_be_satisfied_by_independent_chart(tmp_path):
    agent, store, state = setup_agent(tmp_path)
    state["task"]["goals"] = [{"id": "g", "depends_on": []}, {"id": "later", "depends_on": ["g"]}]
    artifacts = {
        "a": SimpleNamespace(goal_id="g", dependencies=[]),
        "b": SimpleNamespace(goal_id="later", dependencies=[]),
    }
    agent.registry.validate = lambda *args: {}
    agent.registry.artifact = lambda handle, state: artifacts[handle]
    state["result_ids"] = ["a", "b"]
    try:
        agent.finish(state, "completed", "done")
        final = store.run(state["run_id"])
        assert final["status"] == "partial" and final["unmet_goals"] == ["later"]
    finally:
        agent.close()
