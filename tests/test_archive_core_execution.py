import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.archive_core_execution import MANIFEST, build, select_files, verify_archive
from scripts.report_public_nli import digest


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path, code=b"public source"):
    study = tmp_path / "data/core300-formal-v1"
    output = tmp_path / "docs/core300-execution.zip"
    episodes = [{"id": "ep_a", "turns": [{}, {}]}, {"id": "ep_b", "turns": [{}]}]
    identity = {
        "parameters": {"episodes": episodes},
        "code": {"app/example.py": hashlib.sha256(code).hexdigest()},
    }
    write(study / "study.json", {"identity": identity, "sha256": digest(identity)})
    write(study / "environment.json", {"identity": {}, "sha256": digest({})})
    with ZipFile(study / "study.source.zip", "w") as archive:
        archive.writestr("app/example.py", code)
    summaries = []
    for episode in episodes:
        folder = study / episode["id"]
        summary = {"episode_id": episode["id"], "correct": True, "turns": len(episode["turns"])}
        write(folder / "summary.json", summary)
        write(folder / "execution.json", {"identity": {}, "sha256": digest({})})
        for number, _ in enumerate(episode["turns"], 1):
            write(folder / f"turn-{number}.json", {"response": {}, "journal": []})
            write(folder / f"turn-{number}-run.json", {"duplicate": True})
        write(folder / "snapshots/ignored.json", {"duplicated_raw": True})
        (folder / "checkpoint.sqlite").write_bytes(b"ignored sqlite")
        summaries.append(summary)
    ledger = {"events": []}
    write(study / "ledger.json", ledger)
    write(study / "report.json", {"budget": ledger, "episodes": summaries})
    return study, output


def test_exact_allowlist_hashes_omissions_and_deterministic_rebuild(tmp_path):
    study, output = fixture(tmp_path)
    receipt = build(tmp_path, study, output, expected_episodes=2, expected_turns=3, environment={})
    assert receipt["episodes"] == receipt["summaries"] == 2
    assert receipt["turns"] == 3
    assert receipt["files"] == 12
    assert receipt["credential_scan"]["archives_checked"] >= 3
    with ZipFile(output) as archive:
        names = archive.namelist()
        assert not any(name.endswith(("-run.json", ".sqlite")) or "/snapshots/" in name for name in names)
        manifest = json.loads(archive.read(MANIFEST))
    verify_archive(output, manifest)
    assert (
        build(tmp_path, study, output, expected_episodes=2, expected_turns=3, environment={})["sha256"]
        == receipt["sha256"]
    )
    with ZipFile(output, "a") as archive:
        archive.writestr("unexpected.json", b"{}")
    with pytest.raises(ValueError, match="member set mismatch"):
        verify_archive(output, manifest)


@pytest.mark.parametrize(
    "mutation", ["no_report", "missing_turn", "extra_turn", "changed_summary", "changed_ledger"]
)
def test_refuse_incomplete_or_inconsistent_study(tmp_path, mutation):
    study, _ = fixture(tmp_path)
    if mutation == "no_report":
        (study / "report.json").unlink()
    elif mutation == "missing_turn":
        (study / "ep_a/turn-2.json").unlink()
    elif mutation == "extra_turn":
        write(study / "ep_a/turn-3.json", {})
    elif mutation == "changed_summary":
        write(study / "ep_a/summary.json", {"correct": False})
    else:
        write(study / "ledger.json", {"events": ["different"]})
    with pytest.raises(ValueError):
        select_files(tmp_path, study, expected_episodes=2, expected_turns=3)


@pytest.mark.parametrize("location", ["file", "environment"])
def test_recursive_scan_uses_both_file_and_environment_credentials(tmp_path, location):
    file_secret, env_secret = "file-secret-987654321", "env-secret-123456789"
    hidden = file_secret if location == "file" else env_secret
    study, output = fixture(tmp_path, code=hidden.encode())
    (tmp_path / ".env").write_text("OPENAI_API_KEY=" + file_secret, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Credential found"):
        build(
            tmp_path,
            study,
            output,
            expected_episodes=2,
            expected_turns=3,
            environment={"OPENAI_API_KEY": env_secret},
        )
    assert not output.exists()


def test_archive_size_cap_and_output_boundary(tmp_path):
    study, output = fixture(tmp_path)
    with pytest.raises(ValueError, match="size cap"):
        build(tmp_path, study, output, expected_episodes=2, expected_turns=3, environment={}, max_bytes=1)
    assert not output.exists()
    with pytest.raises(ValueError, match="inside the repository"):
        build(tmp_path, study, Path(tmp_path).parent / "outside.zip", expected_episodes=2, expected_turns=3)
