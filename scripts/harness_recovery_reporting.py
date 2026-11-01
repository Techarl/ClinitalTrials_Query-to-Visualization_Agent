"""Validate a closed Harness continuation against its immutable crash snapshot."""

import hashlib
import json
import math
import re
from pathlib import Path
from zipfile import ZipFile


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def audit_recovery(study, backup):
    study, backup = Path(study), Path(backup)
    closed = study / "completed-or-stopped-ledger.json"
    if not closed.is_file():
        raise ValueError("Harness producer has not closed; recovery audit refused")
    amendment = json.loads((study / "recovery-amendment.json").read_text(encoding="utf-8"))
    payload = backup.read_bytes()
    if sha(payload) != amendment["backup_sha256"]:
        raise ValueError("Recovery backup hash differs from the saved amendment")
    with ZipFile(backup) as archive:
        names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        if len(names) != len(set(names)) or len(names) != amendment["backup_files"]:
            raise ValueError("Recovery backup membership differs from the amendment")
        old_bytes = archive.read("ledger.json")
        if sha(old_bytes) != amendment["prior_ledger_sha256"]:
            raise ValueError("Pre-resume ledger hash differs from the amendment")
        prior = json.loads(old_bytes)
        final_bytes = closed.read_bytes()
        final = json.loads(final_bytes)
        count = prior["attempts"]
        if (count != len(prior["events"]) or count != amendment["prior_attempts"]
                or final["attempts"] != len(final["events"]) or final["attempts"] < count
                or canonical(prior["events"]) != canonical(final["events"][:count])):
            raise ValueError("Original attempts are not an unchanged prefix of the closed ledger")
        total = unknown = 0.0
        unknown_count = 0
        for event in prior["events"]:
            completed = event["status"] == "completed"
            value = event["estimated_cost_usd"] if completed else event["reserved_upper_estimate_usd"]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid pre-resume cost")
            total += value
            if not completed:
                unknown += value
                unknown_count += 1
        if (abs(total - prior["estimated_or_reserved_usd"]) > 1e-8
                or abs(total - amendment["prior_estimated_or_reserved_usd"]) > 1e-8
                or unknown_count != amendment["prior_unknown_attempts"]):
            raise ValueError("Pre-resume accounting does not reconcile")
        completed_names = sorted(n for n in names if re.fullmatch(r"ep_[0-9a-f]+/result\.json", n))
        if len(completed_names) != amendment["completed_tasks"]:
            raise ValueError("Pre-resume completed task count differs from the amendment")
        unchanged = []
        for name in completed_names:
            expected = sha(archive.read(name))
            current = study / name
            if not current.is_file() or current.is_symlink() or sha(current.read_bytes()) != expected:
                raise ValueError("A completed pre-resume task result was changed or removed")
            unchanged.append({"record": name, "sha256": expected})
    return {
        "verified": True,
        "auditor_sha256": sha(Path(__file__).read_bytes()),
        "backup_sha256": sha(payload),
        "prior_ledger_sha256": sha(old_bytes),
        "closed_ledger_sha256": sha(final_bytes),
        "prior_attempts": count,
        "final_attempts": final["attempts"],
        "added_attempts": final["attempts"] - count,
        "prior_actual_or_reserved_usd": total,
        "retained_unknown_attempts": unknown_count,
        "retained_unknown_reserved_usd": unknown,
        "unchanged_completed_tasks": unchanged,
        "scope": "Exact original event prefix and completed task bytes; no inference rerun or attribution of pending failures to the crash.",
        "accounting": "Snapshot amounts are already included in the cumulative closed ledger and must not be added again.",
    }
