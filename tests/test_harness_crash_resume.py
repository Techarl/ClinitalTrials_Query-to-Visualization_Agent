import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts.resume_harness_v3 import cached_task, resumed_retry


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class Budget:
    def __init__(self, usd):
        self.events = [{"usd": usd, "status": "attempted_usage_unknown"}]

    @contextmanager
    def scope(self, *args):
        yield

    def scope_events(self, *args):
        return self.events


def setup(tmp_path, count=3):
    baseline, root = tmp_path / "h0", tmp_path / "retries"
    write(baseline / "summary.json", {"model_calls": 4})
    for i in range(count):
        write(root / f"attempt-{i}/summary.json", {"model_calls": 3, "marker": i})
        write(root / f"attempt-{i}/turn-1.json", {})
    calls = []

    def run_episode(episode, settings, path, budget, frozen):
        calls.append(path.name)
        if not (path / "summary.json").exists():
            raise AssertionError("A new call must not be admitted beyond budget")
        return json.loads((path / "summary.json").read_text())

    api = SimpleNamespace(
        freeze=lambda *args: None,
        run_episode=run_episode,
        write=write,
        metrics=lambda events: {"usd": sum(e["usd"] for e in events)},
        public_episode_probe=lambda path: int(path.name.split("-")[-1]) if "attempt" in path.name else -1,
        quality=lambda public: public,
    )
    return baseline, root, api, calls


def test_full_retry_cache_is_returned_without_calls_or_selection(tmp_path):
    baseline, root, api, calls = setup(tmp_path)
    original = {"opaque": "completed result"}
    write(root / "result.json", original)
    api.public_episode_probe = lambda path: pytest.fail("Do not reselect completed retries")
    assert resumed_retry(api, None, None, root, Budget(1), None, baseline, 1) == original
    assert calls == []


def test_resume_reconstructs_all_saved_candidates_at_exhausted_budget(tmp_path):
    baseline, root, api, calls = setup(tmp_path)
    budget = Budget(1)
    result = resumed_retry(api, None, None, root, budget, None, baseline, 1)
    assert calls == ["attempt-0", "attempt-1", "attempt-2"]
    assert result["selected_trace"].endswith("attempt-2")
    assert len(result["attempts"]) == 3
    assert budget.events == [{"usd": 1, "status": "attempted_usage_unknown"}]


def test_full_task_cache_is_opaque_and_never_reexecutes(tmp_path):
    row = {"episode_id": "episode", "incoming_bank_sha256": "bank", "hidden_grade": "unused"}
    write(tmp_path / "result.json", row)
    api = SimpleNamespace(content_hash=lambda bank: "bank", infrastructure_failures=lambda path: [])
    result = cached_task(
        lambda *a: pytest.fail("No model calls"),
        api,
        SimpleNamespace(id="episode"),
        None,
        tmp_path,
        None,
        None,
        {},
    )
    assert result["hidden_grade"] == "unused"


def test_retry_holes_and_more_than_twenty_attempts_rejected(tmp_path):
    baseline, root, api, calls = setup(tmp_path, 0)
    (root / "attempt-2").mkdir(parents=True)
    with pytest.raises(ValueError, match="missing intermediate"):
        resumed_retry(api, None, None, root, Budget(1), None, baseline, 1)
    assert not calls


def test_pending_original_run_can_close_without_releasing_unknown_reservation(tmp_path):
    baseline, root, api, calls = setup(tmp_path, 1)
    pending = root / "attempt-1"
    write(pending / "turn-1-run.json", {"run_id": "original"})
    budget = Budget(1)
    cached = api.run_episode

    def execute(episode, settings, path, received_budget, frozen):
        if path == pending:
            summary = {"model_calls": 0, "status": "limited"}
            write(path / "summary.json", summary)
            write(path / "turn-1.json", {})
            return summary
        return cached(episode, settings, path, received_budget, frozen)

    api.run_episode = execute
    result = resumed_retry(api, None, None, root, budget, None, baseline, 1)
    assert len(result["attempts"]) == 2
    assert result["attempts"][-1]["summary"]["status"] == "limited"
    assert budget.events[0]["status"] == "attempted_usage_unknown"


def test_twenty_cached_attempts_never_admit_a_twenty_first(tmp_path):
    baseline, root, api, calls = setup(tmp_path, 20)
    result = resumed_retry(api, None, None, root, Budget(0.1), None, baseline, 1)
    assert len(calls) == 20
    assert len(result["attempts"]) == 20
    assert result["selected_trace"].endswith("attempt-19")
