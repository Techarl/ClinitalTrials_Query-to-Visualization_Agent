"""Server-derived portable citations, export compatibility, and read-only sharing."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

import app.v2.api as api_module
import app.v2.reading as reading_module
from app.config import Settings
from app.sources import content_hash
from app.storage import StateStore
from app.v2.contracts import Goal, ReadSourceInput, RunRequest, SourceFinding, TaskSpec
from app.v2.store import V2Store
from app.v2.tools import ToolRegistry

TRIAL_POINTER = "/protocolSection/eligibilityModule/eligibilityCriteria"


@pytest.fixture
def setup(tmp_path):
    reading = reading_module
    store = V2Store(StateStore(tmp_path))
    state = store.create(RunRequest(query="Summarize eligibility"), 60)
    state["task"] = TaskSpec(goal="Eligibility", goals=[Goal(id="answer", description="Cited answer", kind="answer")]).model_dump(mode="json")
    state["constraints_version"] = 1
    store.save(state)
    registry = ToolRegistry(Settings(_env_file=None, app_data_dir=tmp_path), SimpleNamespace(), store)
    reading.register_reading(registry)
    return reading, registry, state


def trial(text="Adults age 18 or older."):
    return {"protocolSection": {"identificationModule": {"nctId": "NCT01234567", "briefTitle": "Synthetic trial",
                                                          "url": "https://untrusted.invalid/"},
                                "eligibilityModule": {"eligibilityCriteria": text}}}


def save_finding(registry, state, raw, pointer, quotation):
    handle = registry.store.asset(state["session_id"], state["run_id"], "source_text", raw)
    return {"source_handle": handle, "pointer": pointer, "quotation": quotation}


def publish(reading, registry, state, finding):
    page = registry.read_source(ReadSourceInput(source_handle=finding["source_handle"], pointer=finding["pointer"]), state)
    state["observations"].append({"tool": "read_source", "status": "ok", **page})
    outcome = registry.call("publish_text_answer", {"goal_id": "answer", "answer": "A qualitative summary.",
                                                  "findings": [finding]}, state)
    assert outcome["status"] == "ok", outcome
    artifact = registry.artifact(outcome["result_id"], state)
    assert reading.verify_answer(registry, artifact, state)["validated"]
    return artifact


def test_trial_publish_derives_official_identity_hash_and_exact_field(setup):
    reading, registry, state = setup
    raw = trial()
    finding = save_finding(registry, state, raw, TRIAL_POINTER, "age 18 or older")
    artifact = publish(reading, registry, state, finding)
    saved = artifact.metadata["findings"][0]
    p = saved["provenance"]
    assert p["document_id"] == "NCT01234567"
    assert p["url"] == "https://clinicaltrials.gov/study/NCT01234567"
    assert p["source_hash"] == p["document_hash"] == content_hash(raw)
    assert p["retrieved_at"] is None and p["receipt_hash"] is None
    assert saved["source_field"]["value"] == "Adults age 18 or older."
    assert p["version_notice"].startswith("The official URL identifies")
    other = registry.store.asset(state["session_id"], "another-run", "source_text", raw)
    again = reading.enrich_finding(registry, {**finding, "source_handle": other}, state)
    assert again["provenance"]["citation_id"] == p["citation_id"]


def test_model_cannot_supply_url_or_provenance():
    with pytest.raises(ValidationError):
        SourceFinding(source_handle="x", quotation="text", url="https://model.invalid")
    with pytest.raises(ValidationError):
        SourceFinding(source_handle="x", quotation="text", provenance={"document_id": "invented"})


@pytest.mark.parametrize("mutation", ["url", "source_hash", "field_value", "strip_metadata"])
def test_enriched_metadata_is_checked_against_immutable_source(setup, mutation):
    reading, registry, state = setup
    artifact = publish(reading, registry, state, save_finding(registry, state, trial(), TRIAL_POINTER, "Adults"))
    artifact = artifact.model_copy(deep=True)
    saved = artifact.metadata["findings"][0]
    if mutation == "field_value":
        saved["source_field"]["value"] = "Different field"
    elif mutation == "strip_metadata":
        del saved["provenance"]
        del saved["source_field"]
    else:
        saved["provenance"][mutation] = "tampered"
    with pytest.raises(ValueError, match="provenance differs"):
        reading.verify_answer(registry, artifact, state)


def test_cross_session_source_rejected_and_legacy_artifact_remains_readable(setup):
    reading, registry, state = setup
    finding = save_finding(registry, state, trial(), TRIAL_POINTER, "Adults")
    with pytest.raises(ValueError, match="another session"):
        reading.enrich_finding(registry, finding, {**state, "session_id": "other"})
    legacy = SimpleNamespace(result_id="legacy", metadata={"findings": [finding]})
    assert reading.verify_answer(registry, legacy, state)["validated"]
    exported = reading.export_text_evidence([{"result_id": "legacy", "goal_id": "answer", "source_type": "answer",
                                             "metadata": legacy.metadata}])
    assert exported["artifacts"][0]["findings"][0]["provenance"]["resolution"] == "not_recorded"


def test_pubmed_article_identity_and_container_document_receipt_hashes_are_distinct(setup):
    reading, registry, state = setup
    article = {"pmid": "12345678", "title": "Synthetic article", "abstract": [{"text": "Example abstract."}],
               "source_hash": "a" * 64, "fetched_at": "2026-11-01T00:00:00Z", "url": "https://untrusted.invalid"}
    raw = {"articles": [article]}
    finding = save_finding(registry, state, raw, "/articles/0/abstract/0/text", "Example abstract")
    artifact = publish(reading, registry, state, finding)
    p = artifact.metadata["findings"][0]["provenance"]
    assert p["document_id"] == "12345678" and p["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert p["source_hash"] == content_hash(raw) and p["document_hash"] == content_hash(article)
    assert p["source_hash"] != p["document_hash"] and p["receipt_hash"] == "a" * 64
    assert p["document_pointer"] == "/articles/0"


def test_ambiguous_article_ancestor_requires_precise_pointer(setup):
    reading, registry, state = setup
    raw = {"articles": [{"pmid": pmid, "title": "A", "abstract": [{"text": "Shared quotation"}]} for pmid in ("111", "222")]}
    finding = save_finding(registry, state, raw, "/articles", "Shared quotation")
    with pytest.raises(ValueError, match="precise article"):
        reading.enrich_finding(registry, finding, state)
    enriched = reading.enrich_finding(registry, {**finding, "pointer": "/articles/1/abstract/0/text"}, state)
    assert enriched["provenance"]["document_id"] == "222"


@pytest.mark.parametrize("raw", [trial(), {"articles": [{"pmid": "javascript:evil", "abstract": [{"text": "quote"}]}]}, {"note": "NCT01234567 quote"}])
def test_invalid_or_prose_only_document_identity_is_not_promoted(setup, raw):
    reading, registry, state = setup
    if "protocolSection" in raw:
        raw["protocolSection"]["identificationModule"]["nctId"] = "NCT01234567/redirect"
    finding = save_finding(registry, state, raw, "", "quote" if "protocolSection" not in raw else "Adults")
    with pytest.raises(ValueError):
        reading.enrich_finding(registry, finding, state)


def test_large_field_and_whole_record_exports_are_bounded_and_explicit(setup):
    reading, registry, state = setup
    raw = trial("Exact quote. " + "x" * 50000)
    finding = save_finding(registry, state, raw, TRIAL_POINTER, "Exact quote.")
    enriched = reading.enrich_finding(registry, finding, state)
    assert enriched["source_field"]["value"] is None
    assert enriched["source_field"]["exact_quoted_value"] == "Exact quote."
    assert enriched["source_field"]["omission_reason"] == "field_exceeds_32000_characters"
    assert len(json.dumps(enriched)) < 4000
    root = reading.enrich_finding(registry, {**finding, "pointer": ""}, state)
    assert root["source_field"]["omission_reason"] == "whole_source_container"


def test_route_json_evidence_and_readonly_share_preserve_portable_metadata(setup, monkeypatch):
    reading, registry, state = setup
    artifact = publish(reading, registry, state, save_finding(registry, state, trial(), TRIAL_POINTER, "Adults"))
    text = artifact.model_dump(mode="json")
    chart = {"result_id": "chart", "goal_id": "chart", "source_type": "clinicaltrials", "evidence_ids": ["ev_chart"]}
    result = {"run_id": "run_fixture", "session_id": "session", "status": "completed", "artifacts": [chart, text]}
    page = {"citations": [{"nct_id": "NCT01234567"}], "next_offset": None, "run_id": "run_fixture"}
    shares = {}
    store = SimpleNamespace(
        run=lambda _: {"run_id": "run_fixture", "session_id": "session", "status": "completed"},
        base=SimpleNamespace(evidence_page=lambda *_: deepcopy(page)),
        share=lambda _, payload: shares.setdefault("token", payload) and "token",
        shared=lambda token: shares[token],
    )
    manager = SimpleNamespace(store=store, result=lambda _: deepcopy(result))
    api = api_module
    monkeypatch.setattr(api, "RunManager", lambda *_: manager)
    app = FastAPI()
    api.install_routes(app, SimpleNamespace(), lambda: None)
    # Invoke the registered backend handlers directly: this tests Response JSON
    # and share composition, not browser transport or an ASGI server deployment.
    routes = {route.path: route.endpoint for route in app.routes}
    export = routes["/v2/runs/{run_id}/export"]
    response = export("run_fixture", format="evidence")
    assert response.status_code == 200 and response.media_type == "application/json"
    exported = json.loads(response.body)
    assert exported["ev_chart"] == page  # Existing chart map entries remain identical.
    assert exported["__text_evidence__"]["artifacts"][0]["findings"] == text["metadata"]["findings"]
    assert json.loads(export("run_fixture", format="json").body)["artifacts"][1]["metadata"] == text["metadata"]
    assert routes["/v2/runs/{run_id}/share"]("run_fixture")["token"] == "token"
    public = routes["/v2/shares/{token}"]("token")
    assert public["readonly"] and "session_id" not in public["result"]
    assert public["result"]["artifacts"][1]["metadata"]["findings"] == text["metadata"]["findings"]
    result["artifacts"] = [chart]
    assert json.loads(export("run_fixture", format="evidence").body) == {"ev_chart": page}
