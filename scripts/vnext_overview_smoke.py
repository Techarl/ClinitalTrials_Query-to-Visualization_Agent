"""Live source-count acceptance. No LLM; retain all count and sample receipts."""

import json
from pathlib import Path

from app.config import Settings
from app.service import AnalysisService
from app.v2.agent import V2Agent
from app.v2.contracts import RunRequest, TaskSpec
from app.v2.integrations import register_integrations
from app.v2.store import V2Store
from app.v2.tools import ToolRegistry


def main():
    output = Path("data/vnext-overview")
    settings = Settings(app_data_dir=output)
    service = AnalysisService(settings)
    store = V2Store(service.store)
    registry = ToolRegistry(settings, service, store)
    register_integrations(registry)
    agent = V2Agent(settings, registry)
    try:
        state = store.create(
            RunRequest(
                query="Official study-type overview for asthma omalizumab", source_mode="aggregate_overview"
            ),
            300,
        )
        definition = TaskSpec.model_validate(
            {
                "goal": "Official study-type overview",
                "source_mode": "aggregate_overview",
                "filters": {"condition": "asthma", "interventions": ["omalizumab"]},
                "goals": [{"id": "g", "description": "official study types", "operation": "category"}],
            }
        )
        agent.define_task(definition, state)
        store.save(state)
        result = registry.call(
            "official_count_overview",
            {"goal_id": "g", "dimension": "study_type", "filters": definition.filters.model_dump()},
            state,
        )
        if result["status"] != "ok":
            raise RuntimeError(result)
        state["result_ids"] = [result["result_id"]]
        agent.finish(
            state,
            "completed",
            "Live official count receipts checked independently; citations are samples only.",
        )
        (output / "response.json").write_text(
            agent.result(state["run_id"]).model_dump_json(indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "run_id": state["run_id"],
                    "status": agent.result(state["run_id"]).status,
                    "coverage": result["coverage"],
                }
            )
        )
    finally:
        agent.close()
        service.close()


if __name__ == "__main__":
    main()
