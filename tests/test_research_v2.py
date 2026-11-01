from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.storage import StateStore
from app.v2.context import SYSTEM, pack
from app.v2.contracts import RunRequest
from app.v2.costs import estimate
from app.v2.store import V2Store
from evaluation.runtime import PersistentBudget
from evaluation.scorers import grade
from experiments.decision_v2 import ATOMS, action_for
from experiments.harness_v2 import Component, ExperienceBank, safe_component


def usage():
    return {
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_tokens": 1100,
        "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
    }


def test_model_costs_and_unknown_reservations_survive_restart(tmp_path):
    assert estimate({"requested_model": "gpt-6.1-sol", "usage": usage()}) == pytest.approx(0.00267)
    assert estimate({"requested_model": "gpt-6-luna", "usage": usage()}) == pytest.approx(0.0001345)
    assert estimate({"requested_model": "unknown-model", "usage": usage()}) is None
    assert estimate({"provider": "openrouter_decisions", "usage": {"cost": 0.003}}) == 0.003
    for cost in (float("nan"), float("inf"), -1, True):
        assert estimate({"provider": "openrouter_decisions", "usage": {"cost": cost}}) is None
    path = tmp_path / "ledger.json"
    budget = PersistentBudget(path, max_usd=1)
    event = budget.before(1000)
    reservation = budget.estimated_usd
    budget.after(event, {"usage": None})
    reopened = PersistentBudget(path, max_usd=1)
    assert reopened.estimated_usd == reservation > 0
    assert reopened.events[0]["status"] == "completed_usage_unknown"
    reopened.after(reopened.events[0], {"requested_model": "gpt-6-luna", "usage": usage()})
    assert reopened.estimated_usd == pytest.approx(0.0001345)


def test_atomic_gate_distinguishes_system_repair_from_missing_user_information():
    correct = dict.fromkeys(ATOMS, 0.99)
    assert action_for(correct) == "execute"
    for field in ATOMS:
        bad = {**correct, field: 0.01}
        expected = (
            "unsupported"
            if field == "request_supported"
            else "clarify_user"
            if field == "request_clear"
            else "repair_plan"
        )
        assert action_for(bad) == expected


def test_semantic_scorer_rejects_unrequested_restrictions():
    from app.models import Filters
    from app.v2.contracts import Goal, RunResult, TaskSpec

    task = TaskSpec(
        goal="phase",
        filters=Filters(condition="asthma", countries=["United Kingdom"]),
        goals=[Goal(id="g", description="phase")],
    )
    result = RunResult(run_id="r", session_id="s", status="completed", task=task)
    verdict = grade(
        result, {"status": "completed", "filters": {"condition": "asthma"}}, SimpleNamespace(), {}
    )
    assert not verdict["correct"]
    assert "constraint:countries" in verdict["failures"]


def test_experience_requires_change_and_adoption_and_deduplicates():
    bank = ExperienceBank()
    before = {"model_calls": 6, "observations": [{"tool": "validate_result"}], "trace": "before.json"}
    after = {"model_calls": 5, "observations": [], "trace": "after.json"}
    assert bank.add("phase", before, after, Component(rationale="No change"), False) is None
    component = Component(skill="Reuse a completed verified analysis receipt.", rationale="One fewer call")
    bank.add("phase", before, after, component, False)
    assert bank.retrieve("phase", before) == []
    key = bank.add("phase", before, after, component, True)
    assert bank.add("year", before, after, component, True) == key
    assert len(bank.entries) == 2
    assert bank.retrieve("other", before)[0]["id"] == key
    assert bank.retrieve("other", {"observations": []}) == []
    with pytest.raises(ValueError, match="memorize"):
        safe_component(Component(skill="For asthma always answer 116", rationale="bad"))


def test_pending_original_request_survives_more_than_history_window(tmp_path):
    store = V2Store(StateStore(tmp_path))
    original = RunRequest(query="Melanoma studies in the United States, clarify drug identities")
    state = store.create(original, 60)
    for index in range(12):
        state["status"] = "needs_clarification"
        state["task"] = {"filters": {"condition": "melanoma", "countries": ["United States"]}}
        store.save(state, finish=True)
        state = store.create(RunRequest(query=f"short answer {index}", session_id=state["session_id"]), 60)
    assert original.query not in state["history"]
    assert state["pending_request"]["query"] == original.query
    assert state["prior"]["original_request"]["query"] == original.query
    assert deepcopy(state["prior"]["task"])["filters"]["condition"] == "melanoma"


def test_context_policy_changes_actual_packed_observations_at_fixed_budget():
    state = {
        "request": {"query": "preserve my scope"},
        "constraints_version": 1,
        "observations": [
            {"tool": "analyze", "status": "error", "message": "x" * 900},
            {"tool": "profile_dataset", "status": "ok", "summary": "y" * 900},
            {"tool": "search_trials", "status": "ok", "summary": "z" * 900},
        ],
    }
    relevant = pack(state, len(SYSTEM) + 1600, "relevant")
    recent = pack(state, len(SYSTEM) + 1600, "recent")
    assert relevant["packing"]["included_steps"] == [0]
    assert recent["packing"]["included_steps"] == [2]
    assert relevant["packing"]["omitted_steps"] and recent["packing"]["omitted_steps"]
    assert "preserve my scope" in relevant["payload"] and "preserve my scope" in recent["payload"]
