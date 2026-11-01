"""Recompute frozen examples through current Neo4j/engine, without model/source calls."""

import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace

from app.analysis import AnalysisEngine
from app.cli import export_result
from app.config import PROJECT_ROOT, Settings
from app.graphdb import GraphRepository
from app.models import AnalysisPlan, AnalysisResponse
from app.sources import SnapshotStore, content_hash, utc_now
from app.storage import StateStore
from app.verify_examples import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source_root = PROJECT_ROOT / "app"
    code_hash = content_hash(
        {
            str(p.relative_to(source_root)): p.read_text(encoding="utf-8")
            for p in sorted(source_root.rglob("*.py"))
        }
    )
    settings = Settings()
    graph = GraphRepository(settings)
    source = SnapshotStore(PROJECT_ROOT / "examples")
    store = StateStore(args.output / "state")
    engine = AnalysisEngine(settings, graph, store)
    reports = []
    try:
        with graph.budget(time.monotonic() + 180):
            graph.setup()
            for folder in sorted((PROJECT_ROOT / "examples").glob("[0-9][0-9]_*")):
                old = AnalysisResponse.model_validate_json(
                    (folder / "response.json").read_text(encoding="utf-8")
                )
                plan = AnalysisPlan.model_validate(old.meta["plan"])
                cohorts = {c["id"]: c for c in old.meta["cohorts"]}
                datasets = {}
                for cohort in plan.cohorts:
                    snapshot, studies = source.load(cohorts[cohort.id]["dataset_id"])
                    graph.import_snapshot(snapshot, studies)
                    datasets[cohort.id] = (snapshot, studies)
                visual, meta, provenance, empty = engine.run(plan, datasets, old.run_id)
                assert not empty and old.visualization.model_dump() == visual.model_dump(), folder.name
                assert all(old.meta[key] == value for key, value in meta.items()), folder.name + " metadata"
                assert old.provenance == provenance, folder.name + " provenance"
                response = AnalysisResponse(
                    status="ok",
                    run_id=old.run_id,
                    session_id=old.session_id,
                    visualization=visual,
                    meta={**meta, "plan": plan.model_dump(), "code_hash": code_hash, "replay_model_calls": 0},
                    provenance=provenance,
                )
                output = args.output / folder.name
                export_result(response, SimpleNamespace(store=store), output / "response.json")
                assert json.loads(
                    (output / "response.evidence.json").read_text(encoding="utf-8")
                ) == json.loads((folder / "response.evidence.json").read_text(encoding="utf-8")), (
                    folder.name + " full evidence"
                )
                reports.append(
                    {
                        **verify(output, PROJECT_ROOT / "examples"),
                        "visualization_unchanged": True,
                        "metadata_unchanged": True,
                        "full_evidence_unchanged": True,
                    }
                )
    finally:
        graph.close()
    report = {
        "at": utc_now(),
        "status": "passed",
        "kind": "current_neo4j_engine_replay_of_frozen_sources",
        "code_hash": code_hash,
        "model_calls": 0,
        "source_http_calls": 0,
        "checks": reports,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
