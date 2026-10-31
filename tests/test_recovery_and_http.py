import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import AnalysisRequest, PlannerDecision
from tests.test_agent import plan, setup_runner


class SimulatedProcessCrash(BaseException):
    """Bypass normal Exception boundaries to exercise a saved incomplete checkpoint."""


def test_checkpoint_resume_does_not_replan_or_repeat_a_completed_run(tmp_path):
    runner, service, _ = setup_runner(tmp_path, [PlannerDecision(action="execute", mode="new", plan=plan())])

    def crash(*args):
        raise SimulatedProcessCrash()

    service.execute_plan = crash
    with pytest.raises(SimulatedProcessCrash):
        runner.analyze(AnalysisRequest(query="melanoma phases", session_id="resume_fixture"))
    with service.store.connect() as conn:
        run_id = conn.execute("SELECT id FROM runs").fetchone()["id"]
    runner.close()
    recovered, _, generator = setup_runner(tmp_path, [])
    result = recovered.resume(run_id)
    assert result.status == "no_data"
    assert result.meta["model_calls"] == 1
    assert generator.contexts == []
    assert recovered.resume(run_id).model_dump() == result.model_dump()
    recovered.close()


def test_recovery_never_resets_the_original_deadline(tmp_path):
    runner, service, _ = setup_runner(tmp_path, [PlannerDecision(action="execute", mode="new", plan=plan())])

    def crash(*args):
        raise SimulatedProcessCrash()

    service.execute_plan = crash
    with pytest.raises(SimulatedProcessCrash):
        runner.analyze(AnalysisRequest(query="melanoma phases", session_id="expired_fixture"))
    with service.store.connect() as conn:
        run_id = conn.execute("SELECT id FROM runs").fetchone()["id"]
    runner.graph.update_state(
        {"configurable": {"thread_id": run_id}}, {"deadline_epoch": time.time() - 10}, as_node="validate"
    )
    runner.close()
    recovered, service2, generator = setup_runner(tmp_path, [])
    result = recovered.resume(run_id)
    assert result.status == "failed"
    assert service2.executed == []
    assert generator.contexts == []
    recovered.close()


def test_crash_during_model_call_is_counted_against_resume_budget(tmp_path):
    class CrashingGenerator:
        def generate(self, *args):
            raise SimulatedProcessCrash()

    runner, service, _ = setup_runner(tmp_path, [])
    runner.generator = CrashingGenerator()
    with pytest.raises(SimulatedProcessCrash):
        runner.analyze(AnalysisRequest(query="melanoma phases", session_id="model_crash_fixture"))
    with service.store.connect() as conn:
        run_id = conn.execute("SELECT id FROM runs").fetchone()["id"]
    runner.close()
    recovered, _, generator = setup_runner(
        tmp_path, [PlannerDecision(action="execute", mode="new", plan=plan())]
    )
    result = recovered.resume(run_id)
    assert result.status == "no_data"
    assert result.meta["model_calls"] == 2
    assert result.meta["usage_complete"] is False
    assert len(generator.contexts) == 1
    recovered.close()


def test_http_contract_does_not_initialize_models_for_health_or_evidence(tmp_path):
    settings = Settings(_env_file=None, app_data_dir=tmp_path)
    application = create_app(settings)
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        assert application.state.runner is None
        assert client.get("/evidence/unknown").status_code == 404
        assert client.get("/evidence/unknown?limit=0").status_code == 422
        assert client.post("/analyze", json={"query": "   "}).status_code == 422
        assert client.get("/capabilities").status_code == 200
        assert client.get("/openapi.json").status_code == 200
