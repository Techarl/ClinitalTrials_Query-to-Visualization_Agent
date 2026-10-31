from app.models import AnalysisResponse, SourceField


def test_verbatim_source_whitespace_survives_response_validation_and_serialization():
    original = {"name": "  exact source name  ", "aliases": ["\nsource alias\n"]}
    field = SourceField(path="/protocolSection/example", value=original)
    assert field.value == original
    response = AnalysisResponse(
        status="failed", run_id="r", session_id="s", provenance={"fields": [field.model_dump()]}
    )
    assert response.provenance["fields"][0]["value"] == original
    reloaded = AnalysisResponse.model_validate_json(response.model_dump_json())
    assert reloaded.provenance["fields"][0]["value"] == original
