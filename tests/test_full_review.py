import json
from types import SimpleNamespace

import pytest

from app.models import Filters
from app.v2.charts import temporal_or_scatter
from app.v2.context import pack
from app.v2.contracts import AnalysisInput, ReadSourceInput, ScopeCorrection, TaskSpec
from app.v2.evidence_selection import eligibility_passages
from app.v2.skills import register_skills
from evaluation.protocol import freeze
from tests.test_v2_boundaries import setup_agent


@pytest.mark.parametrize(
    "identities,labels",
    [(["A"], ["A"]), (["A", "A"], ["A", "B"]), (["A", "B"], ["B", "A"]), (["A", "C"], ["A", "C"])],
)
def test_comparison_identity_completeness(tmp_path, identities, labels):
    agent, _, state = setup_agent(tmp_path)
    try:
        state["task"]["intervention_scope"] = "comparison"
        state["task"]["filters"]["interventions"] = ["A", "B"]
        scopes = [{"filters": {"interventions": [n]}, "nct_ids": [], "label": n} for n in identities]
        with pytest.raises(ValueError, match="Comparison"):
            agent.registry.check_comparison(scopes, labels, state)
        valid = [{"filters": {"interventions": [n]}, "nct_ids": [], "label": n} for n in ("A", "B")]
        agent.registry.check_comparison(valid, [], state)
        with pytest.raises(ValueError):
            agent.registry.check_comparison([{"filters": {"interventions": ["A", "B"]}}], [], state)
    finally:
        agent.close()


def test_source_read_enters_next_model_context_and_is_pageable(tmp_path):
    agent, store, state = setup_agent(tmp_path)
    try:
        text = "Excluded: prior therapy. " * 400
        handle = store.asset(state["session_id"], state["run_id"], "source_text", {"abstract": text})
        page = agent.registry.read_source(
            ReadSourceInput(source_handle=handle, pointer="/abstract", limit=2000), state
        )
        state["observations"] = [{"tool": "read_source", "status": "ok", **page}]
        context = json.loads(pack(state, 24000)["payload"])
        assert context["observations"][0]["text"] == text[:2000]
        rest = agent.registry.read_source(
            ReadSourceInput(source_handle=handle, pointer="/abstract", offset=2000), state
        )
        assert rest["text"] == text[2000:6000]
        alien = {**state, "session_id": "other"}
        with pytest.raises((ValueError, KeyError)):
            agent.registry.read_source(ReadSourceInput(source_handle=handle), alien)
    finally:
        agent.close()


def test_inferred_scope_can_be_corrected_only_from_request_evidence(tmp_path):
    agent, store, state = setup_agent(tmp_path)
    try:
        state["request"]["query"] = "Show diabetes phase counts"
        previous = state["constraints_version"]
        proposed = TaskSpec.model_validate(
            {**state["task"], "filters": Filters(condition="diabetes").model_dump()}
        )
        with pytest.raises(ValueError, match="verbatim"):
            agent.define_task(proposed, state)
        proposed.corrections = [
            ScopeCorrection(field="filters.condition", request_quote="diabetes", reason="Misread the disease")
        ]
        agent.define_task(proposed, state)
        assert state["task"]["filters"]["condition"] == "diabetes"
        assert state["constraints_version"] == previous + 1
        assert state["scope_revisions"][0]["previous_task"]["filters"]["condition"] == "asthma"
        state["request"]["filters"] = {"condition": "asthma"}
        with pytest.raises(ValueError, match="explicit"):
            agent.define_task(proposed, state)
    finally:
        agent.close()


def test_paid_receipt_replays_with_no_model_budget_left(tmp_path):
    class Planner:
        def choose(self, *args):
            pytest.fail("Paid call must not repeat")

    agent, store, state = setup_agent(tmp_path, Planner(), v2_max_model_calls=1)
    try:
        store.begin(state["run_id"], "model:0", "model", {"reservation_usd": 1})
        store.complete(
            state["run_id"],
            "model:0",
            {"action": {"name": "clarify_user", "arguments": {"questions": ["Which?"]}}},
        )
        agent.choose({"run_id": state["run_id"]})
        assert store.run(state["run_id"])["current_action"]["name"] == "clarify_user"
    finally:
        agent.close()


def test_cancellation_during_planner_prevents_clarification(tmp_path):
    class Planner:
        def choose(self, *args):
            store.cancel(state["run_id"])
            return {"name": "clarify_user", "arguments": {"questions": ["Which?"]}}, {"usage": None}

    agent, store, state = setup_agent(tmp_path, Planner())
    try:
        agent.choose({"run_id": state["run_id"]})
        assert store.run(state["run_id"])["status"] == "cancelled"
        assert len(store.journal_entries(state["run_id"])) == 1
    finally:
        agent.close()


@pytest.mark.parametrize(
    "grain,periods", [("year", ["2020", "2021", "2022"]), ("month", ["2020-01", "2020-02", "2020-03"])]
)
def test_all_zero_explicit_time_domain(tmp_path, grain, periods):
    agent, store, state = setup_agent(tmp_path)
    try:
        bound = {
            "field": "start",
            "lower": "2020-01-01",
            "upper": "2022-12-31" if grain == "year" else "2020-03-31",
        }
        scopes = [({"label": "Population", "date_range": bound}, SimpleNamespace(dataset_id="none"), [])]
        args = AnalysisInput(scope_ids=["s"], goal_id="g", operation="time_series", time_grain=grain)
        visual, _, _ = temporal_or_scatter(args, scopes, store.base, state["run_id"])
        assert [r["category"] for r in visual["data"]] == periods
        assert all(r["trial_count"] == 0 for r in visual["data"])
        args.time_field = "completion"
        assert temporal_or_scatter(args, scopes, store.base, state["run_id"])[0]["data"] == []
    finally:
        agent.close()


@pytest.mark.parametrize(
    "text",
    [
        "Ordinary eligibility paragraph without bullets.",
        "Inclusion Criteria:\nMust be over 18.\nNo prior therapy.",
        "\\- Age 18\n\\- No prior therapy",
        "* Inclusion Criteria: Adults only\n* Exclusion Criteria: Pregnant",
    ],
)
def test_chunker_preserves_criterion_prose(text):
    raw = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "x"},
            "eligibilityModule": {"eligibilityCriteria": text},
        }
    }
    passages = eligibility_passages(raw, "local", "a" * 64)
    assert passages
    covered = set(i for p in passages for i in range(p.start, p.end))
    assert all(i in covered for i, c in enumerate(text) if not c.isspace())
    assert all(p.text == text[p.start : p.end] for p in passages)


def test_agent_reads_and_activates_versioned_skill(tmp_path):
    agent, store, state = setup_agent(tmp_path)
    try:
        register_skills(agent.registry)
        found = agent.registry.call("search_experiences", {"query": "comparison missing cohort"}, state)
        identity = found["matches"][0]["skill_id"]
        denied = agent.registry.call("apply_skill", {"skill_id": identity}, state)
        assert denied["status"] == "error"
        for tool in ("read_skill", "apply_skill"):
            state = store.run(state["run_id"])
            state["current_action"] = {"name": tool, "arguments": {"skill_id": identity}}
            store.save(state)
            agent.execute({"run_id": state["run_id"]})
        current = store.run(state["run_id"])
        assert current["active_skills"][0]["skill_id"] == identity
        assert identity in pack(current, 24000)["system"]
    finally:
        agent.close()


def test_frozen_identity_rejects_changes(tmp_path):
    path = tmp_path / "manifest.json"
    freeze(path, {"model": "one"})
    freeze(path, {"model": "one"})
    with pytest.raises(ValueError, match="identity changed"):
        freeze(path, {"model": "two"})
