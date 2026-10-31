"""Developer smoke run for the deterministic path, independent of the LLM planner."""

import json
import time

from app.config import Settings
from app.models import AnalysisPlan, Cohort, Filters
from app.service import AnalysisService


def main():
    settings = Settings()
    service = AnalysisService(settings)
    plan = AnalysisPlan(
        operation="category",
        group_by="phase",
        title="Melanoma / pembrolizumab studies by phase",
        cohorts=[
            Cohort(
                id="pembrolizumab",
                label="Pembrolizumab",
                filters=Filters(condition="melanoma", interventions=["pembrolizumab"]),
            )
        ],
    )
    session_id, _, _ = service.store.session(None)
    run_id = service.store.start_run(session_id, None, {"developer_handwritten_plan": plan.model_dump()})
    trace = []
    try:
        response = service.execute_plan(
            plan, run_id, session_id, trace, time.monotonic() + settings.max_run_seconds
        )
        service.store.finish_run(run_id, response.model_dump(mode="json"), trace)
        output = settings.data_directory / "handwritten_demo.json"
        output.write_text(response.model_dump_json(indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": response.status,
                    "run_id": run_id,
                    "output": str(output),
                    "counts": [
                        {"category": row.category, "trial_count": row.trial_count}
                        for row in response.visualization.data
                    ]
                    if response.visualization
                    else [],
                    "generation_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(0 if response.status == "ok" else 1)
    finally:
        service.close()


if __name__ == "__main__":
    main()
