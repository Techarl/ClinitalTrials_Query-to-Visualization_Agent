"""Explicit, read-only readiness checks. Never prints secrets or provider error bodies.

Run with: python -m app.diagnostics
OpenAI is checked via model metadata only; no generation or experiment calls are made.
"""

import json
from datetime import datetime, timezone
from typing import Any

import httpx
from neo4j import GraphDatabase

from app.config import Settings

CTGOV_BASE_URL = "https://clinicaltrials.gov/api/v2"


def check_openai(settings: Settings, client: httpx.Client) -> dict[str, Any]:
    key = settings.openai_api_key.get_secret_value()
    if not key:
        return {"status": "failed", "detail": "OPENAI_API_KEY is empty."}
    try:
        response = client.get(
            f"{str(settings.openai_base_url).rstrip('/')}/models/{settings.openai_model}",
            headers={"Authorization": f"Bearer {key}"},
        )
        if response.status_code != 200:
            return {
                "status": "failed",
                "http_status": response.status_code,
                "detail": "Could not retrieve the configured model. Check key, access, and endpoint.",
            }
        model = response.json()
        if model.get("id") != settings.openai_model:
            return {"status": "failed", "detail": "Provider returned a different model ID."}
        return {
            "status": "passed",
            "model": settings.openai_model,
            "reasoning_effort": settings.openai_reasoning_effort,
            "detail": "Model metadata accessible; generation has not been tested.",
        }
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        return {"status": "failed", "error_type": type(error).__name__}


def check_ctgov(client: httpx.Client) -> dict[str, Any]:
    try:
        version_response = client.get(f"{CTGOV_BASE_URL}/version")
        version_response.raise_for_status()
        version = version_response.json()
        if not isinstance(version, dict) or not version.get("dataTimestamp"):
            return {"status": "failed", "detail": "API version response lacks a data timestamp."}
        response = client.get(
            f"{CTGOV_BASE_URL}/studies",
            params={
                "query.cond": "melanoma",
                "query.intr": "pembrolizumab",
                "pageSize": 1,
                "countTotal": "true",
                "format": "json",
            },
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, dict) or not isinstance(page.get("studies"), list):
            return {"status": "failed", "detail": "Unexpected studies response schema."}
        return {
            "status": "passed",
            "api_version": version.get("apiVersion"),
            "data_timestamp": version["dataTimestamp"],
            "sample_query": {"condition": "melanoma", "intervention": "pembrolizumab"},
            "total_count_reported": page.get("totalCount"),
            "records_read": len(page["studies"]),
            "complete_dataset_fetched": False,
            "detail": "Connectivity probe only; this is not an analysis dataset.",
        }
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        return {"status": "failed", "error_type": type(error).__name__}


def check_neo4j(settings: Settings) -> dict[str, Any]:
    if not settings.neo4j_password.get_secret_value():
        return {"status": "failed", "detail": "NEO4J_PASSWORD is empty."}
    try:
        with GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password.get_secret_value()),
            connection_timeout=10,
            connection_acquisition_timeout=15,
            max_transaction_retry_time=0,
        ) as driver:
            driver.verify_connectivity()
            records, _, _ = driver.execute_query(
                "RETURN 1 AS connection_ok",
                database_=settings.neo4j_database,
                routing_="r",
            )
            if not records or records[0]["connection_ok"] != 1:
                return {"status": "failed", "detail": "Unexpected database probe result."}
        return {"status": "passed", "database": settings.neo4j_database}
    except Exception as error:
        # DB and driver exceptions can embed connection details; expose only the class.
        return {"status": "failed", "error_type": type(error).__name__}


def run_checks(settings: Settings) -> dict[str, Any]:
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        checks = {
            "neo4j": check_neo4j(settings),
            "clinicaltrials": check_ctgov(client),
            "openai_model_access": check_openai(settings, client),
            "openrouter_experiment": {"status": "skipped", "detail": "Experiment disabled; no API call."},
            "harness_experiment": {"status": "skipped", "detail": "Experiment disabled."},
        }
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ready": all(item["status"] != "failed" for item in checks.values()),
        "generation_calls": 0,
        "checks": checks,
    }


def main() -> None:
    settings = Settings()
    report = run_checks(settings)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    settings.data_directory.mkdir(parents=True, exist_ok=True)
    (settings.data_directory / "readiness.json").write_text(output + "\n", encoding="utf-8")
    print(output)
    raise SystemExit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()
