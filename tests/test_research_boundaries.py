import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.models import AnalysisResponse
from experiments.budget import Budget
from experiments.decision import GateAssessment, apply_gate, call_jev, gate_state
from experiments.harness import AdditiveHarness, HarnessEdit, experience_entry, public_task
from experiments.retrieval import HybridIndex


def test_gate_never_promotes_missing_plan_and_rejects_untyped_probabilities():
    baseline = AnalysisResponse(status="needs_clarification", run_id="r", session_id="s", questions=["Drug?"])
    assessment = GateAssessment(
        action="execute",
        probabilities={"execute": 1, "clarify": 0, "unsupported": 0},
        plan_preserves_request=1,
    )
    assert apply_gate(baseline.model_dump(), assessment).status == "needs_clarification"
    with pytest.raises(ValueError):
        GateAssessment(
            action="execute",
            probabilities={"execute": 0.8, "clarify": 0.8, "unsupported": 0.1},
            plan_preserves_request=1,
        )


def test_jev_typed_adapter_uses_documented_endpoint_without_logging_credentials():
    settings = Settings(_env_file=None, openrouter_api_key="synthetic-key-never-a-real-secret")

    def respond(request):
        assert str(request.url) == str(settings.openrouter_decisions_url)
        body = json.loads(request.content)
        assert body["model"] == "typesafe/jev-1.14"
        assert set(body["questions"]) == {"action", "plan_preserves_request"}
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.14-test",
                "provider": "synthetic",
                "usage": {"input_tokens": 10, "cost": 0.000001},
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "clarify",
                        "probabilities": {"execute": 0.1, "clarify": 0.9, "unsupported": 0},
                    },
                    "plan_preserves_request": {"type": "noul", "noul": 0.5},
                },
            },
        )

    assessment, metadata = call_jev(
        settings, {"query": "this drug"}, client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    assert assessment.action == "clarify"
    assert "synthetic-key" not in json.dumps(metadata)


def test_gold_and_scores_do_not_enter_research_inputs():
    case = {
        "id": "sample",
        "turns": [
            {
                "request": {"query": "melanoma"},
                "expected": {"action": "execute", "private_answer_marker": "GOLD_SECRET"},
            }
        ],
    }
    assert "GOLD_SECRET" not in json.dumps(public_task(case))
    turn = {
        "expected": case["turns"][0]["expected"],
        "correct": True,
        "trace": [
            {
                "stage": "planner_request",
                "context": {"system": "fixed capabilities", "payload": '{"request":{"query":"melanoma"}}'},
            },
            {"stage": "planner_response", "decision": {"action": "clarify"}},
        ],
        "response": {"meta": {}},
    }
    assert "GOLD_SECRET" not in json.dumps(gate_state(turn))


def test_candidate_harness_cannot_replace_base_or_increase_hard_context_limit():
    from app.context import PLANNER_PROMPT, ContextBuilder
    from app.models import AnalysisRequest

    edit = HarnessEdit(addendum="Preserve filters", context_char_limit=40000, rationale="synthetic")
    harness = AdditiveHarness(edit).load("baseline-v1")
    assert harness.system_instructions.startswith(PLANNER_PROMPT)
    assert harness.version != "baseline-v1"
    package = ContextBuilder(Settings(_env_file=None, max_context_chars=8000)).build(
        "planner", AnalysisRequest(query="melanoma"), {}, harness, []
    )
    assert package["chars"] <= 8000


def test_hybrid_ranking_keeps_lexical_and_dense_evidence_separate():
    docs = [{"id": "a", "text": "alpha"}, {"id": "b", "text": "beta"}]
    encoder = SimpleNamespace(encode=lambda texts: [[1, 0] if t == "alpha" else [0, 1] for t in texts])
    index = HybridIndex(docs, encoder)
    assert index.search("alpha")["bm25"][0]["id"] == "a"
    assert index.search("alpha")["dense"][0]["id"] == "a"
    assert index.search("unrecognized")["bm25"] == []
    assert len(index.search("unrecognized")["dense"]) == 2  # Candidate presence is not entity confirmation.


def test_research_budget_retains_unknown_call_reservation():
    budget = Budget(max_usd=0.05, max_calls=1)
    event = budget.before(100, output_tokens=10)
    assert event["status"] == "attempted_usage_unknown"
    assert budget.estimated_usd > 0
    with pytest.raises(RuntimeError, match="budget"):
        budget.before(100)


def test_unchanged_outputs_are_negative_experience_not_an_improvement():
    run = {"turns": [{"response": {"status": "needs_clarification", "meta": {}}}]}
    result = {
        "final": run,
        "probes": [run, run],
        "composition": {
            "adopt_edit": True,
            "edit": {"addendum": "arbitrary"},
            "lesson": "Claimed improvement",
        },
    }
    entry = experience_entry({"id": "fixture"}, run, result, 0)
    assert entry["component"] is None
    assert entry["behavioral_evidence"]["changed"] is False
    assert "No accuracy improvement" in entry["text"]
