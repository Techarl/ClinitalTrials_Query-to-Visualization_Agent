from types import SimpleNamespace

import pytest

from evaluation.runtime import PersistentBudget
from experiments import jev_latency_control as study


def pair():
    return {
        "pair_id": "pair",
        "state": {"query": "Find contradictory evidence", "passage": {"text": "No prior therapy"}},
    }


def test_fixed_pairs_ignore_labels_and_each_arm_receives_identical_state():
    tasks = [
        {
            "id": str(i),
            "statement": f"statement {i}",
            "passages": [{"id": f"p{i}", "text": "No prior therapy", "role": "Primary"}],
        }
        for i in range(10)
    ]
    selected = study.choose_pairs(tasks)
    assert len(selected) == 8
    assert len({row["case_id"] for row in selected}) == 8
    with_labels = [{**task, "label": "Entailment", "gold_evidence": ["secret"]} for task in reversed(tasks)]
    assert study.choose_pairs(with_labels) == selected
    assert all("label" not in row["state"] for row in selected)


def test_persistent_client_is_reused_and_new_client_is_closed(tmp_path, monkeypatch):
    created = []
    observed = []

    class Client:
        closed = False

        def close(self):
            self.closed = True

    def factory(*args):
        client = Client()
        created.append(client)
        return client

    def invoke(provider, client, settings, state, account):
        observed.append((provider, client, state))
        account({"provider": "openrouter_decisions", "usage": {"cost": 0.001}})
        return 0.8

    monkeypatch.setattr(study, "create_client", factory)
    monkeypatch.setattr(study, "invoke", invoke)
    budget = PersistentBudget(tmp_path / "ledger.json", max_usd=1)
    shared = Client()
    for provider in ("jev", "luna"):
        for _ in range(2):
            study.measured_call(provider, "persistent", 1, pair(), "measured", None, budget, shared)
            study.measured_call(provider, "new_per_request", 1, pair(), "measured", None, budget)
    assert len(created) == 4
    assert all(client.closed for client in created)
    assert not shared.closed
    assert sum(client is shared for _, client, _ in observed) == 4
    assert all(state == pair()["state"] for _, _, state in observed)
    assert len(budget.events) == 8
    assert budget.estimated_usd == pytest.approx(0.008)


def test_failed_response_retains_usage_reservation_and_warmup_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "create_client", lambda *args: SimpleNamespace(close=lambda: None))

    def fail(*args):
        raise TimeoutError("No response")

    monkeypatch.setattr(study, "invoke", fail)
    budget = PersistentBudget(tmp_path / "ledger.json", max_usd=1)
    row = study.measured_call("jev", "new_per_request", 4, pair(), "warmup", None, budget)
    assert row["status"] == "failed"
    assert budget.events[0]["status"] == "attempted_usage_unknown"
    assert row["estimated_or_reserved_usd"] > 0
    assert budget.estimated_usd == row["estimated_or_reserved_usd"]
    summary = study.summarize([row], 1)
    assert summary["warmup_attempts"] == 1
    assert summary["warmup_estimated_or_reserved_usd"] == row["estimated_or_reserved_usd"]


def test_usage_is_preserved_if_score_parsing_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "create_client", lambda *args: SimpleNamespace(close=lambda: None))

    def fail_after_receipt(provider, client, settings, state, account):
        account({"provider": "openrouter_decisions", "usage": {"cost": 0.002}})
        raise ValueError("Malformed score")

    monkeypatch.setattr(study, "invoke", fail_after_receipt)
    budget = PersistentBudget(tmp_path / "ledger.json", max_usd=1)
    row = study.measured_call("jev", "new_per_request", 1, pair(), "measured", None, budget)
    assert row["status"] == "failed"
    assert row["usage_status"] == "completed"
    assert budget.estimated_usd == pytest.approx(0.002)


def test_success_percentiles_and_failure_denominator_are_separate():
    rows = [
        {
            "stage": "measured",
            "status": status,
            "response_seconds": seconds,
            "input_bytes": 20,
            "estimated_or_reserved_usd": 0.01,
        }
        for status, seconds in [("completed", 1), ("completed", 3), ("failed", 75)]
    ]
    summary = study.summarize(rows, 76)
    assert summary["attempts"] == 3
    assert summary["successful"] == 2
    assert summary["failed"] == 1
    assert summary["p50_seconds"] == 2
    assert summary["p95_seconds"] == pytest.approx(2.9)
    assert summary["measured_estimated_or_reserved_usd"] == pytest.approx(0.03)


def test_probe_cannot_exceed_authorized_cap(tmp_path):
    with pytest.raises(ValueError, match="at most"):
        study.run(SimpleNamespace(max_usd=1.01))
