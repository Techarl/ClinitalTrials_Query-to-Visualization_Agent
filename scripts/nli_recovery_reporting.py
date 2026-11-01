"""Outcome-blind interruption metadata and final-only append-only cost audit."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ARMS = ("hybrid", "cross_encoder", "jev", "luna", "full")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def recovery_plan(manifest, protocol):
    """Use filenames only: no case JSON, selected passages, predictions or gold."""
    if manifest["protocol_sha256"] != protocol["sha256"]:
        raise ValueError("Recovery belongs to a different frozen protocol")
    ids = protocol["identity"]["parameters"]["ids"]
    paths = [item["path"] for item in manifest["files"]]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate recovery snapshot member")
    for name in paths:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe recovery snapshot member")
    names = set(paths)
    score_counts = Counter((parts[0], parts[2]) for name in paths
                           if len(parts := name.split("/")) == 4 and parts[1] == "scores"
                           and parts[3].endswith(".json"))
    touched = {name.split("/")[0] for name in paths if "/" in name} & set(ids)
    saved = {(case, arm) for case in ids for arm in ARMS if f"{case}/{arm}.json" in names}
    exclusions = []
    for case in ids:
        for arm in ARMS:
            failure = f"{case}/{arm}-failure.json" in names
            cached = score_counts[case, arm]
            if (case in touched and (case, arm) not in saved) or failure:
                exclusions.append({
                    "case_id": case, "arm": arm,
                    "reason": "pre_resume_failure_marker" if failure else "interruption_window_unsaved_arm",
                    "saved_passage_scores": cached,
                    "arm_result_saved_before_restart": (case, arm) in saved,
                })
    return {
        "schema": "nli-recovery-latency-plan-v1",
        "protocol_sha256": protocol["sha256"],
        "snapshot_sha256": manifest["snapshot_sha256"],
        "snapshot_created_at": manifest["created_at"],
        "snapshot_files": len(paths),
        "case_contents_read": False, "outcomes_used": False,
        "cases_with_any_saved_activity": len(touched),
        "cases_all_five_arm_results_saved": sum(all((case, arm) in saved for arm in ARMS) for case in ids),
        "saved_arm_results": len(saved),
        "saved_arm_counts": dict(sorted(Counter(arm for _, arm in saved).items())),
        "latency_exclusions": exclusions,
        "latency_excluded_arm_count": len(exclusions),
        "policy": "Exclude elapsed ranking times for any touched case whose arm result was absent, or with a prior failure marker. This conservatively includes possibly not-yet-started later arms. Keep every case/arm in quality denominators. Unexcluded timings are implementation observations, not end-to-end or pure model latency.",
        "frozen_source": {"identity": protocol["sha256"], "files": len(protocol["identity"].get("code", {}))},
    }


def verify_snapshot(manifest, protocol):
    """Read only archive directory plus protocol metadata; never open case results."""
    snapshot = Path(manifest["snapshot"])
    if file_hash(snapshot) != manifest["snapshot_sha256"]:
        raise ValueError("Pre-resume snapshot digest mismatch")
    with ZipFile(snapshot) as archive:
        members = [info.filename for info in archive.infolist() if not info.is_dir()]
        expected = [item["path"] for item in manifest["files"]]
        indexed = {item["path"]: item for item in manifest["files"]}
        if len(members) != len(set(members)) or set(members) != set(expected):
            raise ValueError("Pre-resume snapshot member set mismatch")
        for info in archive.infolist():
            if not info.is_dir():
                item = indexed[info.filename]
                if info.file_size != item["bytes"]:
                    raise ValueError("Pre-resume snapshot member size mismatch")
        if json.loads(archive.read("protocol.json")) != protocol:
            raise ValueError("Snapshot protocol differs from final frozen protocol")


def tag_latency(rows, plan):
    exclusions = {(item["case_id"], item["arm"]): item for item in plan["latency_exclusions"]}
    present = {(row["case_id"], arm) for row in rows for arm in ARMS}
    if not set(exclusions) <= present:
        raise ValueError("Recovery exclusions refer to unknown case/arm")
    for row in rows:
        for arm in ARMS:
            exclusion = exclusions.get((row["case_id"], arm))
            row["arms"][arm]["ranking_latency_eligible"] = exclusion is None
            if exclusion:
                row["arms"][arm]["ranking_latency_exclusion"] = exclusion["reason"]


def final_recovery_audit(manifest_path, protocol, final_ledger, completion_path, summarize_ledger):
    """Call ONLY after validating complete final report; old ledger is not extra cost."""
    completion = read(completion_path)
    if completion.get("status") != "completed":
        raise ValueError("Recovery producer has not closed successfully")
    if completion.get("frozen_identity", protocol["sha256"]) != protocol["sha256"]:
        raise ValueError("Recovery completion protocol mismatch")
    manifest = read(manifest_path)
    plan = recovery_plan(manifest, protocol)
    verify_snapshot(manifest, protocol)
    with ZipFile(manifest["snapshot"]) as archive:
        old_bytes = archive.read("ledger.json")
    expected = next(row["sha256"] for row in manifest["files"] if row["path"] == "ledger.json")
    if hashlib.sha256(old_bytes).hexdigest() != expected:
        raise ValueError("Pre-resume ledger digest mismatch")
    previous = json.loads(old_bytes)
    prior_events = previous["events"]
    if final_ledger["events"][:len(prior_events)] != prior_events:
        raise ValueError("Pre-resume attempts/reservations were removed or changed")
    prior_costs = summarize_ledger(previous)
    return {
        **plan,
        "manifest_sha256": file_hash(manifest_path),
        "completion_receipt_sha256": file_hash(completion_path),
        "closed_at": completion.get("closed_at"),
        "reason": "Desktop application crash; frozen-source execution resumed with saved arm/passage caches",
        "prior_ledger_preserved_as_exact_prefix": True,
        "prior_ledger": prior_costs,
        "new_attempts_after_restart": len(final_ledger["events"]) - len(prior_events),
        "accounting_note": "The final formal ledger already includes this prefix and all prior unknown reservations. Snapshot costs are disclosed, not added to the totals a second time. Retry attempts remain separate billable attempts.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, protocol = read(args.manifest), read(args.protocol)
    plan = recovery_plan(manifest, protocol)
    verify_snapshot(manifest, protocol)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if read(args.output) != plan:
            raise ValueError("Registered recovery latency plan changed")
    else:
        args.output.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in plan.items() if k not in ("latency_exclusions", "policy")}))


if __name__ == "__main__":
    main()
