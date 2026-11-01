"""Independent review reproductions; expected failures identify frozen-source defects."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.models import Filters
from app.v2.agent import V2Agent
from app.v2.contracts import DateRange, Goal, ScopeCorrection, TaskSpec
from evaluation.scorers import grade
from scripts.audit_core300_selection import dependency_failures, revised_failures


def selection_fixture():
    def selection(anchor, prefix):
        return {
            "kind": "tool",
            "input": {
                "name": "select_entities",
                "arguments": {
                    "anchor_sponsor": anchor,
                    "top_n": 2,
                    "scope_id": "root",
                    "result_id": "network",
                },
            },
            "output": {
                "status": "ok",
                "root_scope_id": "root",
                "candidate_count": 4,
                "selected": [{"scope_id": prefix + str(i), "sponsor_id": f"sponsor{i}"} for i in range(2)],
            },
        }

    journal = [selection("Requested", "correct"), selection("Wrong", "wrong")]
    artifacts = [
        {
            "result_id": "network",
            "source_type": "clinicaltrials",
            "dependencies": [],
            "metadata": {
                "root_scopes": ["root"],
                "analysis_parameters": {"operation": "network", "relationship": "sponsor_shared_drug"},
            },
        },
        {
            "result_id": "trend",
            "source_type": "clinicaltrials",
            "dependencies": ["network"],
            "metadata": {
                "root_scopes": ["wrong0", "wrong1"],
                "analysis_parameters": {"operation": "time_series"},
                "lineage": [
                    {
                        "lineage": [
                            {
                                "selection": "top_shared_drug_sponsors",
                                "entity_id": f"sponsor{i}",
                                "anchor": "Wrong",
                                "parent_scope": "root",
                                "result_id": "network",
                            }
                        ]
                    }
                    for i in range(2)
                ],
            },
        },
    ]
    expected = {
        "status": "completed",
        "filters": {},
        "dependent_selection": True,
        "anchor_sponsor": "Requested",
        "selection_top_n": 2,
    }
    return expected, artifacts, journal


@pytest.mark.xfail(
    strict=True, reason="F10 residual: any correct selection can mask the wrong selection backing trend"
)
def test_frozen_scorer_rejects_correct_anchor_decoy_before_wrong_actual_trend():
    expected, artifacts, journal = selection_fixture()
    task = {"filters": Filters().model_dump(), "intervention_scope": "population"}
    result = SimpleNamespace(
        status="completed",
        task=SimpleNamespace(model_dump=lambda **_: task),
        artifacts=[SimpleNamespace(**a, model_dump=lambda _a=a, **_: _a) for a in artifacts],
        questions=[],
    )
    registry = SimpleNamespace(
        validate=lambda *_: {"validated": True}, store=SimpleNamespace(journal_entries=lambda *_: journal)
    )
    # Validators are stubbed only to isolate scorer linkage; no claim this is an end-to-end Neo4j run.
    state = {
        "run_id": "fixture",
        "observations": [{"tool": "select_entities", **e["output"]} for e in journal],
    }
    assert not grade(result, expected, registry, state)["correct"]


def test_offline_audit_rejects_decoy_and_accepts_actual_correct_selection():
    expected, artifacts, journal = selection_fixture()
    assert dependency_failures(expected, artifacts, journal) == [
        "dependent_selection_unbound_to_requested_anchor_and_n"
    ]
    artifacts[-1]["metadata"]["root_scopes"] = ["correct0", "correct1"]
    for scope in artifacts[-1]["metadata"]["lineage"]:
        scope["lineage"][0]["anchor"] = "Requested"
    # A later unused wrong selection does not invalidate an earlier correct selection actually used.
    assert dependency_failures(expected, artifacts, journal) == []
    assert revised_failures(["invented_or_unrelated_cohorts", "status"], expected, artifacts, journal) == [
        "status"
    ]


@pytest.mark.parametrize(
    "mutation",
    ["wrong_top_n", "wrong_lineage", "wrong_dependency", "duplicate_scope", "wrong_network_population"],
)
def test_offline_audit_binds_requested_selection_provenance(mutation):
    expected, artifacts, journal = selection_fixture()
    journal[0] = deepcopy(journal[1])
    journal[0]["input"]["arguments"]["anchor_sponsor"] = "Requested"
    for scope in artifacts[-1]["metadata"]["lineage"]:
        scope["lineage"][0]["anchor"] = "Requested"
    assert dependency_failures(expected, artifacts, journal) == []
    if mutation == "wrong_top_n":
        journal[0]["input"]["arguments"]["top_n"] = 3
    elif mutation == "wrong_lineage":
        artifacts[-1]["metadata"]["lineage"][0]["lineage"][0]["entity_id"] = "someone_else"
    elif mutation == "wrong_dependency":
        artifacts[-1]["dependencies"] = ["other"]
    elif mutation == "duplicate_scope":
        artifacts[-1]["metadata"]["root_scopes"] = ["wrong0", "wrong0"]
    else:
        artifacts[0]["metadata"]["root_scopes"] = ["other_population"]
    assert dependency_failures(expected, artifacts, journal)


@pytest.mark.xfail(
    strict=True, reason="F04 residual: date_range is a dict, rejected by scalar-only correction guard"
)
def test_date_range_can_repair_model_misread_from_explicit_request():
    agent = object.__new__(V2Agent)
    task = TaskSpec(
        goal="start trend",
        filters=Filters(condition="asthma"),
        goals=[Goal(id="trend", description="start trend")],
        date_range=DateRange(field="start", lower="2021-01-01", upper="2021-12-31"),
    )
    state = {
        "request": {"query": "asthma start dates 2020-01-01 through 2020-12-31"},
        "task": task.model_dump(mode="json"),
        "constraints_version": 1,
        "result_ids": [],
    }
    task.date_range = DateRange(field="start", lower="2020-01-01", upper="2020-12-31")
    task.corrections = [
        ScopeCorrection(
            field="date_range",
            request_quote="start dates 2020-01-01 through 2020-12-31",
            reason="The first plan used the wrong year",
        )
    ]
    agent.define_task(task, state)
    assert state["task"]["date_range"]["lower"] == "2020-01-01"


def repair_fixture(query, filters):
    task = TaskSpec(
        goal="phase",
        filters=Filters(condition="asthma", **filters),
        goals=[Goal(id="chart", description="phase")],
    )
    state = {
        "request": {"query": query},
        "task": task.model_dump(mode="json"),
        "constraints_version": 1,
        "result_ids": [],
    }
    return object.__new__(V2Agent), state, task


@pytest.mark.xfail(strict=True, reason="F04 residual: empty lists and enum spellings cannot be corrected")
@pytest.mark.parametrize(
    "field,before,after,query",
    [
        ("countries", ["United States"], [], "Show asthma in all countries"),
        ("phases", ["PHASE2"], ["PHASE3"], "Show asthma in Phase 3"),
        ("sponsor", "Wrong sponsor", None, "Show asthma for all sponsors"),
    ],
)
def test_explicit_list_enum_or_null_corrections(field, before, after, query):
    agent, state, task = repair_fixture(query, {field: before})
    setattr(task.filters, field, after)
    task.corrections = [
        ScopeCorrection(field="filters." + field, request_quote=query, reason="Correct inference")
    ]
    agent.define_task(task, state)
    assert state["task"]["filters"][field] == after


@pytest.mark.xfail(strict=True, reason="F04 residual: explicit date clear cannot replace an inferred range")
def test_explicit_original_request_can_clear_inferred_date_range():
    query = "Show asthma for all dates"
    agent, state, task = repair_fixture(query, {})
    state["task"]["date_range"] = {"field": "start", "lower": "2020-01-01", "upper": None}
    task.corrections = [
        ScopeCorrection(field="date_range", request_quote=query, reason="No date restriction was requested")
    ]
    agent.define_task(task, state)
    assert state["task"]["date_range"] is None


@pytest.mark.xfail(strict=True, reason="F04 residual: quoting A can silently drop user-mentioned B")
@pytest.mark.parametrize(
    "query,quote", [("Compare A and B for asthma", "A"), ("Show only A and B for asthma", "only A")]
)
def test_correcting_list_cannot_drop_requested_member_without_explicit_restriction(query, quote):
    agent, state, task = repair_fixture(query, {"interventions": ["A", "B"]})
    task.filters.interventions = ["A"]
    task.corrections = [ScopeCorrection(field="filters.interventions", request_quote=quote, reason="Drop B")]
    with pytest.raises(ValueError, match="verbatim"):
        agent.define_task(task, state)


@pytest.mark.xfail(strict=True, reason="F04 residual: an explicit structured null date range is ignored")
def test_structured_null_date_range_remains_binding():
    agent, state, task = repair_fixture("Show asthma", {})
    state["request"]["date_range"] = None
    state["task"] = None
    task.date_range = DateRange(field="start", lower="2020-01-01")
    with pytest.raises(ValueError, match="explicit"):
        agent.define_task(task, state)


def test_structured_filters_cannot_be_weakened_and_unquoted_clears_are_rejected():
    agent, state, task = repair_fixture("Show asthma in all countries", {"countries": ["Canada"]})
    state["request"]["filters"] = {"countries": ["Canada"]}
    task.filters.countries = []
    task.corrections = [
        ScopeCorrection(field="filters.countries", request_quote="all countries", reason="Clear")
    ]
    with pytest.raises(ValueError, match="explicit"):
        agent.define_task(task, state)
    del state["request"]["filters"]
    state["request"]["query"] = "Show asthma"
    task.corrections[0].request_quote = "asthma"
    with pytest.raises(ValueError, match="verbatim"):
        agent.define_task(task, state)


def test_invented_list_member_can_be_removed_with_all_remaining_values_quoted():
    query = "Show asthma with A"
    agent, state, task = repair_fixture(query, {"interventions": ["A", "inventedDrug"]})
    task.filters.interventions = ["A"]
    task.corrections = [
        ScopeCorrection(field="filters.interventions", request_quote=query, reason="Remove invented term")
    ]
    agent.define_task(task, state)
    assert state["task"]["filters"]["interventions"] == ["A"]


def test_explicitly_excluded_list_member_can_be_removed():
    query = "Show asthma with A; exclude B"
    agent, state, task = repair_fixture(query, {"interventions": ["A", "B"]})
    task.filters.interventions = ["A"]
    task.corrections = [
        ScopeCorrection(field="filters.interventions", request_quote=query, reason="Respect exclusion")
    ]
    agent.define_task(task, state)
    assert state["task"]["filters"]["interventions"] == ["A"]
