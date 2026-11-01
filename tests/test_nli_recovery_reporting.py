import copy
import hashlib
import json
from zipfile import ZipFile

import pytest

from scripts import nli_recovery_reporting as recovery
from scripts import report_public_nli as report


def fixture():
    protocol = {"sha256": "frozen", "identity": {"parameters": {"ids": ["a", "b", "c"]}, "code": {"a.py": "hash"}}}
    paths = [f"a/{arm}.json" for arm in recovery.ARMS]
    paths += ["b/candidate-pool.json", "b/hybrid.json", "b/cross_encoder.json", "b/scores/jev/hash.json"]
    manifest = {"protocol_sha256": "frozen", "snapshot_sha256": "zip-hash", "created_at": "before-resume",
                "files": [{"path": path, "bytes": 1, "sha256": "hash"} for path in paths]}
    return manifest, protocol


def test_latency_exclusions_use_only_filenames_and_conservatively_include_future_arms():
    manifest, protocol = fixture()
    plan = recovery.recovery_plan(manifest, protocol)
    assert plan["cases_all_five_arm_results_saved"] == 1
    assert plan["cases_with_any_saved_activity"] == 2
    assert plan["saved_arm_results"] == 7
    assert {(r["case_id"], r["arm"]) for r in plan["latency_exclusions"]} == {
        ("b", "jev"), ("b", "luna"), ("b", "full")}
    assert plan["latency_exclusions"][0]["saved_passage_scores"] == 1
    assert plan["case_contents_read"] is False and plan["outcomes_used"] is False


def test_failed_marker_with_later_saved_result_is_also_excluded():
    manifest, protocol = fixture()
    manifest["files"].append({"path": "a/luna-failure.json"})
    plan = recovery.recovery_plan(manifest, protocol)
    row = next(r for r in plan["latency_exclusions"] if r["case_id"] == "a")
    assert row["arm_result_saved_before_restart"] is True
    assert row["reason"] == "pre_resume_failure_marker"


def test_excluding_partial_elapsed_time_preserves_all_quality_and_paired_denominators():
    manifest, protocol = fixture()
    rows = []
    for index, case in enumerate(("a", "b", "c")):
        rows.append({"case_id": case, "answer_planned": True, "shared_trial_component": case, "arms": {
            arm: {"completed": True, "complete_evidence": True, "answer_correct": True,
                  "evidence_recall": 1, "evidence_precision": 1, "citation_ids_valid": True,
                  "nonempty_citations": True, "context_tokens": 100, "ranking_seconds": (20, 0.01, 40)[index]}
            for arm in recovery.ARMS}})
    before = report.summarize(rows)
    recovery.tag_latency(rows, recovery.recovery_plan(manifest, protocol))
    after = report.summarize(rows)
    for arm in recovery.ARMS:
        for metric in ("planned", "complete_evidence", "answer_correct", "mean_evidence_recall"):
            assert after[arm][metric] == before[arm][metric]
    timing = after["jev"]["implementation_ranking_latency"]
    assert timing["successful_samples"] == 2 and timing["excluded_interruption_samples"] == 1
    assert timing["p50_seconds"] == 30
    assert timing["raw_saved_elapsed_diagnostic"]["samples"] == 3
    assert rows[1]["arms"]["jev"]["ranking_seconds"] == 0.01
    difference = report.paired_cluster_difference(rows, "jev", "hybrid", "answer_correct", draws=20)
    assert difference["denominator"] == 3 and difference["difference"] == 0
    common = report.common_case_latency(rows)
    assert common["retained_cases"] == 2 and common["excluded_cases"] == 1
    assert all(arm["successful_samples"] == 2 and arm["p50_seconds"] == 30
               for arm in common["arms"].values())


@pytest.mark.parametrize("name", ["../outside.json", "/absolute.json", "C:/file.json", "a\\jev.json"])
def test_invalid_metadata_paths_are_rejected(name):
    manifest, protocol = fixture()
    manifest["files"].append({"path": name})
    with pytest.raises(ValueError, match="Unsafe"):
        recovery.recovery_plan(manifest, protocol)


def archived_fixture(tmp_path):
    manifest, protocol = fixture()
    old = {"attempts": 2, "estimated_or_reserved_usd": 0.3, "events": [
        {"status": "completed", "estimated_cost_usd": 0.1},
        {"status": "attempted_usage_unknown", "reserved_upper_estimate_usd": 0.2}]}
    contents = {item["path"]: b"PRIVATE CASE CONTENT MUST NOT BE PARSED" for item in manifest["files"]}
    contents.update({"protocol.json": json.dumps(protocol).encode(), "ledger.json": json.dumps(old).encode()})
    archive = tmp_path / "snapshot.zip"
    with ZipFile(archive, "w") as stream:
        for name, data in contents.items():
            stream.writestr(name, data)
    manifest.update(snapshot=str(archive), snapshot_sha256=recovery.file_hash(archive), files=[
        {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in contents.items()])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    completion = tmp_path / "exit-2.json"
    completion.write_text(json.dumps({"status": "completed", "closed_at": "end", "frozen_identity": "frozen"}))
    final = copy.deepcopy(old)
    final["events"].append({"status": "completed", "estimated_cost_usd": 0.05})
    final.update(attempts=3, estimated_or_reserved_usd=0.35)
    return manifest_path, protocol, final, completion


def test_final_cost_prefix_audit_keeps_unknown_reservation_without_double_counting(tmp_path):
    args = archived_fixture(tmp_path)
    audit = recovery.final_recovery_audit(*args, report.ledger_summary)
    assert audit["prior_ledger_preserved_as_exact_prefix"] is True
    assert audit["prior_ledger"]["unknown_usage_attempts"] == 1
    assert audit["prior_ledger"]["unknown_reserved_usd"] == 0.2
    assert audit["new_attempts_after_restart"] == 1
    assert report.ledger_summary(args[2])["estimated_or_reserved_usd"] == pytest.approx(0.35)
    assert "PRIVATE" not in json.dumps(audit)


def test_prior_unknown_attempt_cannot_be_dropped_or_zeroed(tmp_path):
    args = archived_fixture(tmp_path)
    args[2]["events"][1]["reserved_upper_estimate_usd"] = 0
    with pytest.raises(ValueError, match="removed or changed"):
        recovery.final_recovery_audit(*args, report.ledger_summary)


def test_failed_resume_exit_is_rejected_before_snapshot_or_ledger_read(tmp_path, monkeypatch):
    manifest, protocol, final, completion = archived_fixture(tmp_path)
    completion.write_text('{"status":"failed"}')
    monkeypatch.setattr(recovery, "verify_snapshot", lambda *args: pytest.fail("Snapshot read before closed"))
    with pytest.raises(ValueError, match="not closed"):
        recovery.final_recovery_audit(manifest, protocol, final, completion, report.ledger_summary)


def test_known_interrupted_protocol_requires_disclosure_before_reading_final_scores(tmp_path, monkeypatch):
    study = tmp_path / "study"
    study.mkdir()
    (study / "report.json").write_text("DO NOT PARSE OUTCOMES")
    (study / "protocol.json").write_text(json.dumps({"sha256": report.INTERRUPTED_PROTOCOL}))
    strata = tmp_path / "strata.json"
    strata.write_text("{}")
    monkeypatch.setattr("sys.argv", ["report", "--study", str(study), "--strata", str(strata)])
    with pytest.raises(ValueError, match="requires recovery disclosure"):
        report.main()


def test_failed_completion_marker_prevents_final_score_read(tmp_path, monkeypatch):
    study = tmp_path / "study"
    study.mkdir()
    (study / "report.json").write_text("DO NOT PARSE OUTCOMES")
    (study / "protocol.json").write_text(json.dumps({"sha256": report.INTERRUPTED_PROTOCOL}))
    strata = tmp_path / "strata.json"
    strata.write_text("{}")
    completion = tmp_path / "first-failed-attempt.json"
    completion.write_text('{"status":"failed"}')
    monkeypatch.setattr("sys.argv", ["report", "--study", str(study), "--strata", str(strata),
                                    "--recovery-manifest", "not-read.json", "--recovery-completion", str(completion)])
    with pytest.raises(ValueError, match="final scores not read"):
        report.main()


def test_snapshot_modification_is_rejected(tmp_path):
    path, protocol, _, _ = archived_fixture(tmp_path)
    manifest = recovery.read(path)
    with ZipFile(manifest["snapshot"], "a") as archive:
        archive.writestr("extra-case.json", "NOT READ")
    with pytest.raises(ValueError, match="digest mismatch"):
        recovery.verify_snapshot(manifest, protocol)
