import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.diagnostics import check_ctgov, check_openai


def test_configuration_rejects_unimplemented_experiments_without_echoing_secrets():
    secret = "fake-test-secret-do-not-log"
    with pytest.raises(ValidationError) as captured:
        Settings(_env_file=None, openai_api_key=secret, enable_decision_experiment=True)
    assert secret not in str(captured.value)
    assert "must remain false" in str(captured.value)


def test_configuration_repr_and_json_mask_credentials():
    secret = "fake-test-secret-do-not-log"
    settings = Settings(_env_file=None, openai_api_key=secret, neo4j_password=secret)
    assert secret not in repr(settings)
    assert secret not in settings.model_dump_json()


def test_openai_probe_never_generates_or_echoes_provider_error_body():
    secret = "fake-test-secret-do-not-log"

    def respond(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/models/gpt-6.1-sol"
        return httpx.Response(401, json={"error": f"Invalid key: {secret}"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = check_openai(Settings(_env_file=None, openai_api_key=secret), client)
    assert result["http_status"] == 401
    assert secret not in json.dumps(result)


def test_ctgov_probe_does_not_claim_one_page_is_a_complete_dataset():
    def respond(request):
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"apiVersion": "test", "dataTimestamp": "2026-10-30"})
        return httpx.Response(200, json={"totalCount": 400, "studies": [{}], "nextPageToken": "next"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = check_ctgov(client)
    assert result["status"] == "passed"
    assert result["records_read"] == 1
    assert result["complete_dataset_fetched"] is False
