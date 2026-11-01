"""Recorded demos retain full evidence and cannot initiate live work."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts.view_recorded_examples import create_app


@pytest.fixture(scope="module")
def viewer(tmp_path_factory):
    frontend = tmp_path_factory.mktemp("recorded-ui")
    (frontend / "index.html").write_text("<html>built UI placeholder</html>")
    (frontend / "assets").mkdir()
    with TestClient(create_app(Path("examples/v2"), frontend)) as client:
        yield client


def test_recorded_evidence_is_complete_and_exports_original(viewer):
    payload = viewer.get("/v2/shares/01_phase-1").json()
    assert payload["readonly"] is True
    result = payload["result"]
    assert result == viewer.get("/recordings/01_phase-1-result").json()
    for artifact in result["artifacts"]:
        for key in artifact["evidence_ids"]:
            evidence = payload["evidence"][key]
            assert evidence["total_contributors"] == len(evidence["citations"])
            assert evidence["next_offset"] is None
    assert viewer.get("/s/01_phase-1").status_code == 200


def test_viewer_cannot_execute_or_read_arbitrary_files(viewer):
    assert viewer.post("/v2/runs", json={"query": "start paid work"}).status_code == 404
    assert viewer.get("/v2/shares/diagnostics-dependent-1").status_code == 404
    assert viewer.get("/recordings/.env").status_code == 404
    assert viewer.get("/s/unknown").status_code == 404
    assert viewer.get("/.env").status_code == 404


def test_index_labels_recordings_and_exposes_both_followup_versions(viewer):
    page = viewer.get("/").text
    assert "not new executions" in page
    assert "/s/02_followup-1" in page and "/s/02_followup-2" in page
    assert "57,805" in page


def test_missing_ui_has_actionable_error(tmp_path):
    with pytest.raises(RuntimeError, match="npm --prefix frontend run build"):
        create_app(frontend=tmp_path)
