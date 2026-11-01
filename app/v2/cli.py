"""Run the VNext observation loop directly, with the same tool/evidence contracts."""

import argparse
import json
from pathlib import Path

from app.config import Settings
from app.service import AnalysisService
from app.v2.api import RunManager
from app.v2.contracts import RunRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--session-id")
    parser.add_argument("--parent-run-id")
    parser.add_argument(
        "--source-mode", choices=["full_evidence", "aggregate_overview"], default="full_evidence"
    )
    parser.add_argument("--output", type=Path, default=Path("data/vnext-result.json"))
    args = parser.parse_args()
    settings = Settings()
    service = AnalysisService(settings)
    manager = RunManager(settings, lambda: service)
    try:
        created = manager.store.create(
            RunRequest(
                query=args.query,
                session_id=args.session_id,
                parent_run_id=args.parent_run_id,
                source_mode=args.source_mode,
            ),
            settings.v2_max_run_seconds,
        )
        result = manager.initialize().run(created["run_id"])
        args.output.parent.mkdir(exist_ok=True, parents=True)
        args.output.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        evidence = {
            ev: service.store.evidence_page(ev, 0, 1000000)
            for artifact in result.artifacts
            for ev in artifact.evidence_ids
        }
        args.output.with_suffix(".evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "session_id": result.session_id,
                    "status": result.status,
                    "output": str(args.output),
                    "usage": result.usage,
                },
                ensure_ascii=False,
            )
        )
    finally:
        manager.close()
        service.close()


if __name__ == "__main__":
    main()
