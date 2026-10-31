"""Local natural-language client and portable evidence export."""

import argparse
import json
import sys
from pathlib import Path

from app.agent import AgentRunner
from app.config import Settings
from app.models import AnalysisRequest
from app.service import AnalysisService


def export_result(response, service: AnalysisService, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(response.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if response.provenance.get("evidence"):
        complete_evidence = {}
        for evidence_id in response.provenance["evidence"]:
            first = service.store.evidence_page(evidence_id, 0, 500)
            citations = list(first["citations"])
            offset = first["next_offset"]
            while offset is not None:
                page = service.store.evidence_page(evidence_id, offset, 500)
                citations.extend(page["citations"])
                offset = page["next_offset"]
            complete_evidence[evidence_id] = {
                "rule": first["rule"],
                "total_contributors": first["total_contributors"],
                "citations": citations,
            }
        destination.with_suffix(".evidence.json").write_text(
            json.dumps(complete_evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--language", choices=["en", "zh"], default="en")
    parser.add_argument("--session-id")
    parser.add_argument("--parent-run-id")
    parser.add_argument("--filters", help="Optional JSON object containing explicit shared filters")
    parser.add_argument("--output", type=Path, default=Path("data/latest_response.json"))
    args = parser.parse_args()
    request_data = {"query": args.query, "language": args.language}
    if args.session_id:
        request_data["session_id"] = args.session_id
    if args.parent_run_id:
        request_data["parent_run_id"] = args.parent_run_id
    if args.filters:
        request_data["filters"] = json.loads(args.filters)
    request = AnalysisRequest.model_validate(request_data)
    service = AnalysisService(Settings())
    runner = AgentRunner(service.settings, service)
    try:
        response = runner.analyze(request)
        export_result(response, service, args.output)
        print(
            json.dumps(
                {
                    "status": response.status,
                    "run_id": response.run_id,
                    "session_id": response.session_id,
                    "message": response.message,
                    "questions": response.questions,
                    "output": str(args.output),
                    "model_calls": response.meta.get("model_calls"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1 if response.status == "failed" else 0)
    finally:
        runner.close()
        service.close()


if __name__ == "__main__":
    main()
