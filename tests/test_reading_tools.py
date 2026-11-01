import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.storage import StateConflict
from app.v2.contracts import Goal, RunRequest
from app.v2.reading import register_reading
from tests.test_v2_boundaries import setup_agent


def test_idempotent_submission_is_atomic_under_concurrent_retries(tmp_path):
    agent, store, _ = setup_agent(tmp_path)
    try:
        request = RunRequest(query="same logical question", submission_id="submission-1234567890")
        with ThreadPoolExecutor(max_workers=6) as executor:
            runs = list(executor.map(lambda _: store.create(request, 60), range(12)))
        assert len({r["run_id"] for r in runs}) == 1
        with pytest.raises(StateConflict):
            store.create(request.model_copy(update={"query": "different"}), 60)
        assert sum(e["type"] == "run_created" for e in store.events(runs[0]["run_id"])) == 1
    finally:
        agent.close()


def test_text_answer_requires_read_quotation_and_preserves_verification_limit(tmp_path):
    agent, store, state = setup_agent(tmp_path)
    try:
        state["task"]["goals"] = [Goal(id="answer", description="Eligibility", kind="answer").model_dump()]
        register_reading(agent.registry)
        agent.registry.reading_records = {
            "NCT00000001": {
                "protocolSection": {
                    "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Example"},
                    "eligibilityModule": {
                        "eligibilityCriteria": "Adults aged 18 or older. Exclude prior therapy."
                    },
                }
            }
        }
        match = agent.registry.call("lookup_trials", {"query": "NCT00000001"}, state)["matches"][0]
        pointer = "/protocolSection/eligibilityModule/eligibilityCriteria"
        finding = {
            "source_handle": match["source_handle"],
            "pointer": pointer,
            "quotation": "Adults aged 18 or older.",
        }
        args = {
            "goal_id": "answer",
            "answer": "Adults are eligible; prior therapy is excluded.",
            "findings": [finding],
        }
        assert agent.registry.call("publish_text_answer", args, state)["status"] == "error"
        read = agent.registry.call(
            "read_source", {"source_handle": match["source_handle"], "pointer": pointer}, state
        )
        state["observations"].append(read)
        # New valid state must not replay a previous failed publication receipt.
        result = agent.registry.call("publish_text_answer", args, state)
        assert result["status"] == "ok"
        artifact = agent.registry.artifact(result["result_id"], state)
        assert artifact.source_type == "answer"
        assert artifact.coverage.statistics_basis == "source_excerpts"
        assert "semantic" in json.dumps(artifact.metadata)
        changed = {**args, "findings": [{**finding, "quotation": "Children only"}]}
        assert agent.registry.call("publish_text_answer", changed, state)["status"] == "error"
    finally:
        agent.close()
