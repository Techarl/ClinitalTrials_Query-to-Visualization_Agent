import json
from collections import deque

from app.agent import AgentRunner
from app.config import Settings
from app.models import (
    AnalysisPlan,
    AnalysisRequest,
    AnalysisResponse,
    Cohort,
    FilterPatch,
    Filters,
    PlannerDecision,
    PlanPatch,
)
from app.providers import GenerationFailure
from app.storage import StateConflict, StateStore


class ScriptedGenerator:
    def __init__(self, decisions):
        self.decisions = deque(decisions)
        self.contexts = []

    def generate(self, package, deadline):
        self.contexts.append(json.loads(package["payload"]))
        decision = self.decisions.popleft()
        if isinstance(decision, Exception):
            raise decision
        return decision, {"provider": "scripted-test", "usage": None}


class TestService:
    __test__ = False

    def __init__(self, settings):
        self.settings = settings
        self.store = StateStore(settings.data_directory)
        self.executed = []

    def execute_plan(self, plan, run_id, session_id, trace, deadline):
        self.executed.append(plan)
        return AnalysisResponse(
            status="no_data", run_id=run_id, session_id=session_id, meta={"plan": plan.model_dump()}
        )


def plan(condition="melanoma", **filters):
    return AnalysisPlan(
        operation="category",
        cohorts=[Cohort(id="a", label="A", filters=Filters(condition=condition, **filters))],
    )


def setup_runner(tmp_path, decisions):
    settings = Settings(_env_file=None, app_data_dir=tmp_path)
    service = TestService(settings)
    generator = ScriptedGenerator(decisions)
    return AgentRunner(settings, service, generator), service, generator


def test_multiturn_patch_new_topic_and_restart_persistence(tmp_path):
    decisions = [
        PlannerDecision(action="execute", mode="new", plan=plan(interventions=["pembrolizumab"])),
        PlannerDecision(
            action="execute", mode="refine", patch=PlanPatch(set_filters=FilterPatch(phases=["PHASE3"]))
        ),
        PlannerDecision(action="execute", mode="new", plan=plan("asthma")),
    ]
    runner, service, _ = setup_runner(tmp_path, decisions)
    first = runner.analyze(
        AnalysisRequest(
            query="Phase distribution for melanoma pembrolizumab", filters=Filters(condition="melanoma")
        )
    )
    second = runner.analyze(
        AnalysisRequest(query="Only phase 3", session_id=first.session_id, parent_run_id=first.run_id)
    )
    assert service.executed[-1].cohorts[0].filters.interventions == ["pembrolizumab"]
    assert service.executed[-1].cohorts[0].filters.phases == ["PHASE3"]
    runner.close()
    replacement, service2, _ = setup_runner(tmp_path, [decisions[2]])
    third = replacement.analyze(
        AnalysisRequest(query="New topic: asthma", session_id=first.session_id, parent_run_id=second.run_id)
    )
    assert third.status == "no_data"
    assert service2.executed[-1].cohorts[0].filters.phases == []
    assert service2.executed[-1].cohorts[0].filters.interventions == []
    assert replacement.resume(first.run_id).run_id == first.run_id
    replacement.close()


def test_clarification_keeps_last_successful_plan_and_pending_explicit_filters(tmp_path):
    decisions = [
        PlannerDecision(action="execute", mode="new", plan=plan()),
        PlannerDecision(action="clarify", mode="new", questions=["Which drug?"]),
        PlannerDecision(action="execute", mode="clarification", plan=plan(interventions=["pembrolizumab"])),
    ]
    runner, service, _ = setup_runner(tmp_path, decisions)
    first = runner.analyze(AnalysisRequest(query="melanoma phases"))
    pending = runner.analyze(
        AnalysisRequest(
            query="Compare this drug", filters=Filters(condition="melanoma"), session_id=first.session_id
        )
    )
    _, _, state = service.store.session(first.session_id)
    assert state["last_successful_plan"]["cohorts"][0]["filters"]["condition"] == "melanoma"
    assert state["pending_clarification"]
    result = runner.analyze(
        AnalysisRequest(query="pembrolizumab", session_id=first.session_id, parent_run_id=pending.run_id)
    )
    assert result.status == "no_data"
    assert service.executed[-1].cohorts[0].filters.condition == "melanoma"
    runner.close()


def test_bounded_repair_and_failure_does_not_erase_success(tmp_path):
    runner, service, generator = setup_runner(
        tmp_path,
        [
            PlannerDecision(action="execute", mode="new", plan=plan()),
            GenerationFailure("invalid_structured_plan", True, ["invalid operation"]),
            GenerationFailure("invalid_structured_plan", True, ["still invalid"]),
        ],
    )
    first = runner.analyze(AnalysisRequest(query="melanoma phases"))
    failed = runner.analyze(AnalysisRequest(query="new query", session_id=first.session_id))
    assert failed.status == "failed"
    assert failed.meta["model_calls"] == 2
    assert len(generator.contexts) == 3
    _, _, state = service.store.session(first.session_id)
    assert state["last_result_id"] == first.run_id
    runner.close()


def test_explicit_conflict_and_cross_session_parent_are_blocked(tmp_path):
    runner, service, _ = setup_runner(
        tmp_path, [PlannerDecision(action="execute", mode="new", plan=plan("asthma"))]
    )
    response = runner.analyze(AnalysisRequest(query="asthma", filters=Filters(condition="melanoma")))
    assert response.status == "needs_clarification"
    assert not service.executed
    try:
        runner.analyze(
            AnalysisRequest(query="follow up", session_id="different_session", parent_run_id=response.run_id)
        )
        raise AssertionError("Cross-session parent should be rejected")
    except StateConflict:
        pass
    runner.close()


def test_replaceable_policy_can_request_clarification_without_executing(tmp_path):
    class ClarifyPolicy:
        def evaluate(self, decision, context):
            assert context["request"]["query"] == "melanoma phases"
            return PlannerDecision(action="clarify", mode="new", questions=["Which population?"])

    runner, service, _ = setup_runner(tmp_path, [PlannerDecision(action="execute", mode="new", plan=plan())])
    runner.policy = ClarifyPolicy()
    result = runner.analyze(AnalysisRequest(query="melanoma phases"))
    assert result.status == "needs_clarification"
    assert service.executed == []
    runner.close()


def test_harness_context_override_cannot_expand_the_hard_budget(tmp_path):
    import pytest

    from app.context import ContextBuilder
    from app.extensions import HarnessConfig

    settings = Settings(_env_file=None, app_data_dir=tmp_path, max_context_chars=8000)
    builder = ContextBuilder(settings)
    package = builder.build(
        "planner",
        AnalysisRequest(query="melanoma phases"),
        {},
        HarnessConfig(system_instructions="Alternate test instructions"),
        [],
    )
    assert package["system"] == "Alternate test instructions"
    with pytest.raises(ValueError, match="budget"):
        builder.build(
            "planner",
            AnalysisRequest(query="melanoma phases"),
            {},
            HarnessConfig(system_instructions="x" * 9000, context_char_limit=100000),
            [],
        )
