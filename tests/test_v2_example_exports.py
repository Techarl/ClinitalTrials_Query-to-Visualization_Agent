import copy
import gzip
import json
import shutil
import socket
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.sources import content_hash
from scripts.verify_v2_examples import ArchiveSnapshots, read, verify, verify_example

ROOT = Path(__file__).resolve().parents[1] / "examples/v2"


def test_recorded_v2_examples_verify_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline verification attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = verify(ROOT)
    assert result["examples"] == 4 and result["turns"] == 5
    assert result["exact_fields_checked"] == 57805


def test_missing_chart_point_rejected_even_with_consistent_asset_hash(tmp_path):
    entry = copy.deepcopy(read(ROOT / "index.json")["examples"][0])
    folder = tmp_path / entry["directory"]
    shutil.copytree(ROOT / entry["directory"], folder)
    response_path = folder / entry["turns"][0]["directory"] / "response.json"
    response = read(response_path)
    artifact = response["artifacts"][0]
    artifact["visualization"]["data"].pop()
    response_path.write_text(json.dumps(response), encoding="utf-8")
    assets_path = folder / "assets.json.gz"
    assets = read(assets_path)
    asset = next(a for a in assets if a["id"] == artifact["result_id"])
    # Stored artifact omits the public handle fields, as the real store does.
    asset["data"]["artifact"]["visualization"]["data"].pop()
    asset["hash"] = content_hash(asset["data"])
    assets_path.write_bytes(gzip.compress(json.dumps(assets).encode(), mtime=0))
    with ZipFile(ROOT / "sources.zip") as archive, pytest.raises(ValueError, match="item coverage"):
        verify_example(tmp_path, entry, ArchiveSnapshots(archive))


def test_followup_parent_cannot_point_to_an_unrelated_run(tmp_path):
    entry = copy.deepcopy(read(ROOT / "index.json")["examples"][1])
    folder = tmp_path / entry["directory"]
    shutil.copytree(ROOT / entry["directory"], folder)
    state_path = folder / entry["turns"][1]["directory"] / "state.json"
    state = read(state_path)
    state["parent_run_id"] = "vr_wrong_parent"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with ZipFile(ROOT / "sources.zip") as archive, pytest.raises(ValueError, match="parent/session"):
        verify_example(tmp_path, entry, ArchiveSnapshots(archive))
