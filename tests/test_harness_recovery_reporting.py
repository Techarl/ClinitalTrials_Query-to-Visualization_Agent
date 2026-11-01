import copy
import hashlib
import json
from zipfile import ZipFile

import pytest

from scripts.harness_recovery_reporting import audit_recovery


def fixture(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    events = [
        {"status": "completed", "estimated_cost_usd": 0.2},
        {"status": "attempted_usage_unknown", "reserved_upper_estimate_usd": 0.1},
    ]
    prior = {"attempts": 2, "events": events, "estimated_or_reserved_usd": 0.3}
    old = json.dumps(prior).encode()
    backup = tmp_path / "backup.zip"
    result = b'{"completed": true}'
    with ZipFile(backup, "w") as archive:
        archive.writestr("ledger.json", old)
        archive.writestr("ep_ab/result.json", result)
    (study / "ep_ab").mkdir()
    (study / "ep_ab/result.json").write_bytes(result)
    amendment = {
        "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "backup_files": 2,
        "prior_ledger_sha256": hashlib.sha256(old).hexdigest(),
        "prior_attempts": 2,
        "prior_estimated_or_reserved_usd": 0.3,
        "prior_unknown_attempts": 1,
        "completed_tasks": 1,
    }
    (study / "recovery-amendment.json").write_text(json.dumps(amendment))
    final = copy.deepcopy(prior)
    final["events"].append({"status": "completed", "estimated_cost_usd": 0.4})
    final.update(attempts=3, estimated_or_reserved_usd=0.7)
    (study / "completed-or-stopped-ledger.json").write_text(json.dumps(final))
    return study, backup


def test_closed_recovery_retains_original_unknown_reservation_and_task(tmp_path):
    study, backup = fixture(tmp_path)
    result = audit_recovery(study, backup)
    assert result["verified"]
    assert result["added_attempts"] == 1
    assert result["retained_unknown_attempts"] == 1
    assert result["retained_unknown_reserved_usd"] == 0.1
    assert len(result["unchanged_completed_tasks"]) == 1


@pytest.mark.parametrize("change", ["delete", "zero_reservation", "complete_unknown", "reorder"])
def test_changed_original_attempts_are_rejected(tmp_path, change):
    study, backup = fixture(tmp_path)
    path = study / "completed-or-stopped-ledger.json"
    value = json.loads(path.read_text())
    if change == "delete":
        value["events"].pop(1)
        value["attempts"] -= 1
    elif change == "zero_reservation":
        value["events"][1]["reserved_upper_estimate_usd"] = 0
    elif change == "complete_unknown":
        value["events"][1].update(status="completed", estimated_cost_usd=0)
    else:
        value["events"][:2] = reversed(value["events"][:2])
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unchanged prefix"):
        audit_recovery(study, backup)


@pytest.mark.parametrize("field", ["backup_sha256", "prior_ledger_sha256", "backup_files", "completed_tasks", "prior_unknown_attempts"])
def test_amendment_identity_and_counts_are_bound(tmp_path, field):
    study, backup = fixture(tmp_path)
    path = study / "recovery-amendment.json"
    value = json.loads(path.read_text())
    value[field] = "incorrect" if field.endswith("sha256") else 99
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        audit_recovery(study, backup)


def test_completed_task_cannot_be_silently_replaced(tmp_path):
    study, backup = fixture(tmp_path)
    (study / "ep_ab/result.json").write_text('{"completed": false}')
    with pytest.raises(ValueError, match="changed or removed"):
        audit_recovery(study, backup)


def test_live_study_refused_before_opening_any_backup_or_amendment(tmp_path):
    with pytest.raises(ValueError, match="not closed"):
        audit_recovery(tmp_path, tmp_path / "missing-backup.zip")
