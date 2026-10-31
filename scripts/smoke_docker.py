"""Exercise the running Docker API with two real planner calls and optional restart.

Use --restart-api to verify that a follow-up survives a Compose API restart.
The baseline's decision/harness experiments remain disabled.
"""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def wait_for_health(client):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if client.get("/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("Container API health deadline exceeded")


def pointer(raw, path):
    for key in path.split("/")[1:]:
        key = key.replace("~1", "/").replace("~0", "~")
        raw = raw[int(key)] if isinstance(raw, list) else raw[key]
    return raw


def compare_frozen(first, followup):
    baseline = json.loads((ROOT / "examples/01_phases/response.json").read_text(encoding="utf-8"))
    if first["provenance"]["datasets"][0]["raw_hash"] != baseline["provenance"]["datasets"][0]["raw_hash"]:
        return "not_compared_source_changed"
    assert {r["category"]: r["trial_count"] for r in first["visualization"]["data"]} == {
        r["category"]: r["trial_count"] for r in baseline["visualization"]["data"]
    }
    expected_followup = json.loads(
        (ROOT / "examples/02_followup_trend/response.json").read_text(encoding="utf-8")
    )
    assert {r["year"]: r["trial_count"] for r in followup["visualization"]["data"]} == {
        r["year"]: r["trial_count"] for r in expected_followup["visualization"]["data"]
    }
    return "passed_same_source_hash"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--restart-api", action="store_true")
    parser.add_argument("--compose-project", help="Optional isolated Compose project to restart")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(exist_ok=True)
    with httpx.Client(base_url=args.base_url, timeout=330) as client:
        wait_for_health(client)
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        first_http = client.post(
            "/analyze",
            json={"query": "Show the phase distribution of melanoma studies involving pembrolizumab."},
        )
        first = first_http.json()
        (output / "docker-first-response.json").write_text(
            json.dumps(first, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert first_http.status_code == 200 and first["status"] == "ok", first.get("message")
        assert first["visualization"]["type"] == "bar_chart"
        assert first["meta"]["experiments"] == {"decision": False, "harness": False}
        assert first["meta"]["completeness"] == "complete"
        selected_count = first["meta"]["cohorts"][0]["selected_trial_count"]
        assert sum(row["trial_count"] for row in first["visualization"]["data"]) == selected_count
        manifest = first["provenance"]["datasets"][0]
        assert manifest["complete"] and manifest["expected_count"] == manifest["retrieved_unique_count"]
        evidence_id, preview = next(iter(first["provenance"]["evidence"].items()))
        page = client.get(f"/evidence/{evidence_id}", params={"limit": 3}).json()
        assert page["total_contributors"] == preview["contributor_count"]
        refs = list(page["citations"])
        offset = page["next_offset"]
        while offset is not None:
            page = client.get(f"/evidence/{evidence_id}", params={"offset": offset, "limit": 3}).json()
            refs.extend(page["citations"])
            offset = page["next_offset"]
        assert len(refs) == preview["contributor_count"]
        citation = refs[0]
        raw_http = client.get(f"/datasets/{citation['dataset_id']}/studies/{citation['nct_id']}")
        assert raw_http.status_code == 200
        raw = raw_http.json()
        assert all(pointer(raw, field["path"]) == field["value"] for field in citation["fields"])
        print(
            json.dumps(
                {
                    "stage": "initial_analysis",
                    "status": "passed",
                    "selected_trials": selected_count,
                    "model_calls": first["meta"]["model_calls"],
                }
            ),
            flush=True,
        )
        if args.restart_api:
            command = ["docker", "compose"]
            if args.compose_project:
                command.extend(["-p", args.compose_project])
            subprocess.run([*command, "restart", "api"], cwd=ROOT, check=True, capture_output=True)
            wait_for_health(client)
        followup_http = client.post(
            "/analyze",
            json={
                "query": "Keep the same condition and drug, only Phase 3, and show the number of studies starting each year from 2015 through 2025.",
                "session_id": first["session_id"],
                "parent_run_id": first["run_id"],
            },
        )
        followup = followup_http.json()
        (output / "docker-followup-response.json").write_text(
            json.dumps(followup, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert followup_http.status_code == 200 and followup["status"] == "ok", followup.get("message")
        assert followup["visualization"]["type"] == "time_series"
        assert followup["provenance"]["datasets"][0]["dataset_id"] == manifest["dataset_id"]
        filters = followup["meta"]["plan"]["cohorts"][0]["filters"]
        assert (
            filters["phases"] == ["PHASE3"] and filters["start_year"] == 2015 and filters["end_year"] == 2025
        )
        run = client.get(f"/runs/{followup['run_id']}").json()
        assert any(event.get("stage") == "source" and event.get("cached") for event in run["trace"])
        assert client.post("/runs/" + followup["run_id"] + "/resume").json() == followup
        assert client.post("/analyze", json={"query": " "}).status_code == 422
        report = {
            "frozen_reference_comparison": compare_frozen(first, followup),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "passed",
            "runtime": "docker-compose",
            "api_restart_between_turns": args.restart_api,
            "source_records": selected_count,
            "followup_selected_records": followup["meta"]["cohorts"][0]["selected_trial_count"],
            "source_data_timestamp": manifest["source_version_before"].get("dataTimestamp"),
            "source_hash": manifest["raw_hash"],
            "first_run_id": first["run_id"],
            "followup_run_id": followup["run_id"],
            "model_calls": first["meta"]["model_calls"] + followup["meta"]["model_calls"],
            "evidence_paging": "passed",
            "exact_source_fields": "passed",
            "snapshot_cache_reuse": "passed",
            "completed_resume_without_new_model_call": "passed",
            "experiments": followup["meta"]["experiments"],
        }
        (output / "docker-validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
