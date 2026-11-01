import copy
import json

import pytest

from scripts.report_harness_v3 import (
    aggregate,
    audit_arm,
    report,
    retry_path,
    validate_population,
    verify_arm_accounting,
)
from scripts.report_public_nli import digest


def population():
    cases = [
        {
            "id": f"ep_{i:02}",
            "split": "heldout",
            "entity_group": f"disease_{i // 4}",
            "family": f"family_{i % 20}",
        }
        for i in range(40)
    ]
    identity = {"parameters": {"order": [c["id"] for c in cases], "workers": 4}}
    protocol = {"identity": identity, "sha256": digest(identity)}
    partial = {
        "results": [],
        "task_exceptions": [],
        "pending_or_unrun_ids": identity["parameters"]["order"],
        "planned_unique": 40,
        "evaluated_count": 0,
    }
    return cases, protocol, partial


def test_report_refuses_unclosed_study_before_reading_report_or_ledger(tmp_path):
    # Invalid active files would raise JSON errors if the reporter touched them.
    (tmp_path / "ledger.json").write_text("active incomplete")
    (tmp_path / "report.json").write_text("active incomplete")
    with pytest.raises(ValueError, match="not closed"):
        report(tmp_path, tmp_path / "missing-cases.json")


def test_partial_run_retains_all_forty_planned_denominators():
    cases, protocol, partial = population()
    assert len(validate_population(partial, protocol, {"episodes": cases})) == 40
    result = aggregate([])
    assert all(v["correct_over_planned"]["denominator"] == 40 for v in result.values())
    assert all(v["unobserved_tasks"] == 40 for v in result.values())
    assert all(v["correct_over_observed"]["denominator"] == 0 for v in result.values())


@pytest.mark.parametrize("mutation", ["missing_unrun", "development", "duplicate_error", "wrong_workers"])
def test_population_integrity_rejects_inconsistent_report(mutation):
    cases, protocol, partial = population()
    if mutation == "missing_unrun":
        partial["pending_or_unrun_ids"].pop()
    elif mutation == "development":
        cases[0]["split"] = "development"
    elif mutation == "duplicate_error":
        partial["task_exceptions"] = [{"episode_id": "ep_00"}] * 2
    else:
        protocol["identity"]["parameters"]["workers"] = 2
        protocol["sha256"] = digest(protocol["identity"])
    with pytest.raises(ValueError):
        validate_population(partial, protocol, {"episodes": cases})


def test_retry_trace_is_portable_but_cannot_escape_or_select_an_editor(tmp_path):
    assert (
        retry_path(tmp_path, "ep_00", "C:\\old\\study\\ep_00\\usd-retries\\attempt-3")
        == tmp_path / "ep_00/usd-retries/attempt-3"
    )
    with pytest.raises(ValueError):
        retry_path(tmp_path, "ep_00", "C:/study/ep_00/../../other/h0")
    with pytest.raises(ValueError):
        retry_path(tmp_path, "ep_00", "C:/study/ep_00/memory/final")


def test_linkage_audit_cannot_erase_unrelated_failures_or_missing_turns(tmp_path):
    episode = {"turns": [{"expected": {}}, {"expected": {}}]}
    turn = {"grade": {"failures": ["wrong_count"]}, "response": {"artifacts": []}, "journal": []}
    (tmp_path / "turn-1.json").write_text(json.dumps(turn))
    result = audit_arm(tmp_path, copy.deepcopy(episode))
    assert not result["correct"]
    assert result["turn_failures"] == [["wrong_count"], ["missing_recorded_turn"]]


def test_arm_accounting_detects_cross_task_scope_leakage_and_retry_overspend():
    row = {
        "episode_id": "ep_01",
        "costs": {"no-memory": {"calls": 1, "usd": 0.2}},
        "usd_retries": {"extra_allocation_usd": 0.2, "actual": {"usd": 0.1}},
    }
    events = [
        {
            "study_scope": "C:\\study\\ep_01\\no-memory",
            "status": "attempted_usage_unknown",
            "reserved_upper_estimate_usd": 0.2,
        }
    ]
    verify_arm_accounting(row, events)
    with pytest.raises(ValueError, match="Per-arm accounting"):
        verify_arm_accounting(row, [{**events[0], "study_scope": "C:/study/ep_02/no-memory"}])
    row["usd_retries"]["actual"]["usd"] = 0.21
    with pytest.raises(ValueError, match="exceeded"):
        verify_arm_accounting(row, events)
