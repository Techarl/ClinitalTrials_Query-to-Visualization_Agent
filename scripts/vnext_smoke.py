"""Explicitly paid native-tool smoke on frozen CTgov data and real Neo4j."""

import argparse
import json
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.service import AnalysisService
from app.v2.agent import V2Agent
from app.v2.contracts import RunRequest
from app.v2.integrations import register_integrations
from app.v2.store import V2Store
from app.v2.tools import ToolRegistry
from evaluation.replay import FrozenSource

QUERIES = {
    "dependent": "Among melanoma studies involving pembrolizumab starting from 2015 through 2025, find the top three sponsors sharing the most drugs with Merck Sharp & Dohme LLC. Show the sponsor shared-drug network and compare the selected sponsors by annual trial counts. Keep the same root population for both charts and show supporting evidence.",
    "scatter": "For psoriasis studies involving secukinumab, show a scatter plot of enrollment against trial duration in days. Use exact start and completion dates, explain exclusions and retain point-level sources.",
    "entity": "Compare Keytruda and pembrolizumab trials for melanoma by phase. First check whether these are different medicines.",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=QUERIES, default="dependent")
    parser.add_argument("--output", type=Path, default=Path("data/vnext-smoke"))
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    settings = Settings(app_data_dir=args.output / "runtime", v2_max_estimated_usd=2)
    service = AnalysisService(settings)
    store = V2Store(service.store)
    registry = ToolRegistry(settings, service, store, FrozenSource(PROJECT_ROOT / "examples"))
    register_integrations(registry)
    agent = V2Agent(settings, registry)
    state = store.create(RunRequest(query=QUERIES[args.scenario]), settings.v2_max_run_seconds)
    print(json.dumps({"run_id": state["run_id"], "scenario": args.scenario}), flush=True)
    try:
        result = agent.run(state["run_id"])
        output = args.output / (args.scenario + "-" + state["run_id"])
        output.mkdir(exist_ok=False)
        (output / "response.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        for name, value in [
            ("state", store.run(state["run_id"])),
            ("events", store.events(state["run_id"], limit=1000)),
            ("journal", store.journal_entries(state["run_id"])),
        ]:
            (output / (name + ".json")).write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "artifacts": len(result.artifacts),
                    "questions": result.questions,
                    "model_calls": result.usage["model_calls"],
                    "estimated_or_reserved_usd": result.usage["estimated_or_reserved_usd"],
                    "output": str(output),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        agent.close()
        service.close()


if __name__ == "__main__":
    main()
