"""Generate the five actual submission runs, their full evidence, and frozen sources.

This explicitly invokes the configured main LLM. Both experiments remain disabled.
"""

import json

from app.agent import AgentRunner
from app.cli import export_result
from app.config import PROJECT_ROOT, Settings
from app.models import AnalysisRequest
from app.service import AnalysisService

CASES = [
    ("01_phases", "Show the phase distribution of melanoma studies involving pembrolizumab.", "bar_chart"),
    (
        "02_followup_trend",
        "Keep the same condition and drug, only Phase 3, and show the number of studies starting each year from 2015 through 2025.",
        "time_series",
    ),
    (
        "03_comparison",
        "Compare the phase distributions for dupilumab versus omalizumab studies in asthma.",
        "grouped_bar",
    ),
    (
        "04_enrollment",
        "Show a histogram of enrollment counts for psoriasis studies involving secukinumab, using 8 equal-width bins. Include actual and estimated enrollment.",
        "histogram",
    ),
    (
        "05_shared_drugs",
        "For rheumatoid arthritis studies involving adalimumab, show the 10 strongest sponsor-to-sponsor links through shared drug or biological interventions. Weight each link by the number of shared interventions, and include supporting studies.",
        "network_graph",
    ),
]


def main():
    service = AnalysisService(Settings())
    runner = AgentRunner(service.settings, service)
    root = PROJECT_ROOT / "examples"
    root.mkdir(exist_ok=True)
    summary = []
    first = None
    try:
        for name, query, expected_type in CASES:
            data = {"query": query, "language": "en"}
            if name == "02_followup_trend":
                data.update({"session_id": first.session_id, "parent_run_id": first.run_id})
            request = AnalysisRequest.model_validate(data)
            response = runner.analyze(request)
            folder = root / name
            folder.mkdir(exist_ok=True)
            (folder / "request.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            export_result(response, service, folder / "response.json")
            run = service.store.run(response.run_id)
            (folder / "trace.json").write_text(
                json.dumps(run["trace"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            for manifest in response.provenance.get("datasets", []):
                dataset_id = manifest["dataset_id"]
                destination = root / "snapshots" / dataset_id
                destination.mkdir(parents=True, exist_ok=True)
                for filename in ("manifest.json", "studies.json"):
                    contents = (service.snapshots.root / dataset_id / filename).read_bytes()
                    target = destination / filename
                    if target.exists() and target.read_bytes() != contents:
                        raise ValueError("Refusing to overwrite a frozen example snapshot")
                    if not target.exists():
                        with target.open("xb") as handle:
                            handle.write(contents)
            item = {
                "name": name,
                "status": response.status,
                "run_id": response.run_id,
                "chart_type": response.visualization.type if response.visualization else None,
                "model_calls": response.meta.get("model_calls"),
            }
            summary.append(item)
            print(json.dumps(item), flush=True)
            if response.status != "ok" or response.visualization.type != expected_type:
                raise RuntimeError(f"Example {name} did not produce the expected successful chart")
            if first is None:
                first = response
        (root / "index.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    finally:
        runner.close()
        service.close()


if __name__ == "__main__":
    main()
