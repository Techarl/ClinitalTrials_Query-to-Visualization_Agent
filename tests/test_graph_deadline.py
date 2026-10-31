from types import SimpleNamespace

import pytest

from app.graphdb import IMPORT_QUERY, GraphRepository
from app.sources import Snapshot, content_hash, utc_now


def test_import_stops_after_slow_batch_and_never_publishes_ready(monkeypatch, studies):
    clock = [0.0]
    calls = []
    timeouts = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, query, parameters):
            calls.append(query.text)
            timeouts.append(query.timeout)
            if query.text == IMPORT_QUERY:
                clock[0] += 10
            return []

    monkeypatch.setattr("app.graphdb.time", SimpleNamespace(monotonic=lambda: clock[0]))
    repository = object.__new__(GraphRepository)
    repository.settings = SimpleNamespace(neo4j_database="neo4j")
    repository.driver = SimpleNamespace(session=lambda **kwargs: Session())
    raw = studies * 200  # Three batches; the first already consumes the remaining budget.
    snapshot = Snapshot(
        dataset_id="ds_deadline",
        query={},
        query_hash="fixture",
        started_at=utc_now(),
        finished_at=utc_now(),
        complete=True,
        raw_hash=content_hash(raw),
        retrieved_unique_count=len(raw),
    )
    with pytest.raises(TimeoutError):
        repository.import_snapshot(snapshot, raw, deadline=5)
    assert calls.count(IMPORT_QUERY) == 1
    assert any("ready=false" in query for query in calls)
    assert not any("ready=true," in query for query in calls)
    assert all(timeout <= 5 for timeout in timeouts)


def test_expired_graph_budget_issues_no_query():
    repository = object.__new__(GraphRepository)
    with repository.budget(0):
        with pytest.raises(TimeoutError):
            repository.query("RETURN 1")
