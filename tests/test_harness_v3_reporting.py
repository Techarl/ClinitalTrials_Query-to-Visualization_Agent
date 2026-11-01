import copy
import hashlib
import json
from zipfile import ZipFile

import pytest

from scripts.archive_harness_v3_formal import records
from scripts.report_harness_v3 import (
    aggregate,
    audit_arm,
    report,
    retry_path,
    summarize_mechanisms,
    unexplained_cost_usd,
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


def accounting_fixture():
    row = {
        "episode_id": "ep_01",
        "costs": {
            "h0": {"calls": 0, "usd": 0},
            "no-memory": {"calls": 1, "usd": 0.2},
            "memory": {"calls": 0, "usd": 0},
            "usd-retries": {"calls": 1, "usd": 0.1},
        },
        "usd_retries": {"extra_allocation_usd": 0.2, "actual": {"calls": 1, "usd": 0.1}},
    }
    events = [
        {
            "study_scope": "C:\\study\\ep_01\\no-memory",
            "status": "attempted_usage_unknown",
            "reserved_upper_estimate_usd": 0.2,
        },
        {"study_scope": "C:/study/ep_01/usd-retries", "status": "completed", "estimated_cost_usd": 0.1},
    ]
    return row, events


def test_arm_accounting_detects_cross_task_scope_leakage_and_retry_overspend():
    row, events = accounting_fixture()
    verify_arm_accounting(row, events)
    with pytest.raises(ValueError, match="Per-arm accounting"):
        verify_arm_accounting(row, [{**events[0], "study_scope": "C:/study/ep_02/no-memory"}, events[1]])
    row["usd_retries"]["actual"]["usd"] = 0.21
    row["costs"]["usd-retries"]["usd"] = 0.21
    events[1]["estimated_cost_usd"] = 0.21
    with pytest.raises(ValueError, match="exceeded"):
        verify_arm_accounting(row, events)


@pytest.mark.parametrize(
    "mutation", ["remove_scope", "extra_scope", "actual_calls", "actual_usd", "nan_actual", "nan_claim"]
)
def test_arm_scope_and_retry_actual_mutations_are_rejected(mutation):
    row, events = accounting_fixture()
    if mutation == "remove_scope":
        row["costs"].pop("h0")
    elif mutation == "extra_scope":
        row["costs"]["extra"] = {"calls": 0, "usd": 0}
    elif mutation == "actual_calls":
        row["usd_retries"]["actual"]["calls"] = 0
    elif mutation == "actual_usd":
        row["usd_retries"]["actual"]["usd"] = 0.08
    elif mutation == "nan_actual":
        row["usd_retries"]["actual"]["usd"] = float("nan")
    else:
        row["costs"]["usd-retries"]["usd"] = float("nan")
    with pytest.raises(ValueError):
        verify_arm_accounting(row, events)


def test_unexplained_residual_rejects_overlap_and_tolerates_only_roundoff():
    row, _ = accounting_fixture()
    with pytest.raises(ValueError, match="Negative unexplained"):
        unexplained_cost_usd(0.29, [row])
    assert unexplained_cost_usd(0.3 - 1e-10, [row]) == 0
    assert unexplained_cost_usd(0.5, [row]) == pytest.approx(0.2)


def test_mechanism_report_distinguishes_declared_reuse_and_first_turn_from_all_turns():
    ppc = {
        "adopted": True,
        "final": {"skill": " "},
        "retrieved": ["a", "b"],
        "applied": ["a"],
        "executor_read_skill_ids": [],
        "executor_activated_skill_ids": [],
        "context_policy_effective": False,
    }
    row = {
        "ppc_no_memory": copy.deepcopy(ppc),
        "ppc_memory": copy.deepcopy(ppc),
        "mechanism": {"counts": {"learned_read": 2, "learned_applied": 1}},
    }
    result = summarize_mechanisms([row])
    editor = result["editors"]["ppc_memory"]
    assert editor["adopted_empty_procedure_tasks"] == 1
    assert editor["adopted_nonempty_procedure_tasks"] == 0
    assert editor["editor_declared_reuse_id_occurrences"] == 1
    assert editor["first_turn_public_probe_read_events"] == 0
    assert result["memory_final_all_turn_tool_events"]["learned_read"] == 2
    assert result["memory_final_tasks_with_learned_read"] == 1
    row["ppc_memory"]["composition"] = {"component": {"skill": ""}}
    row["ppc_memory"]["final"]["skill"] = "Fixed engineering policy instruction"
    second = summarize_mechanisms([row])["editors"]["ppc_memory"]
    assert second["adopted_empty_procedure_tasks"] == 1
    assert second["adopted_nonempty_procedure_tasks"] == 0
    assert second["final_nonempty_injected_procedure_tasks"] == 1


def test_closed_report_reconciles_all_arms_and_full_population(tmp_path):
    cases, protocol, raw = population()
    for case in cases:
        case["turns"] = [{"expected": {}}]
    corpus = tmp_path / "cases.json"
    corpus.write_text(json.dumps({"episodes": cases}))
    protocol["identity"]["parameters"]["source_files"] = {
        "cases.json": hashlib.sha256(corpus.read_bytes()).hexdigest()
    }
    protocol["identity"]["code"] = {}
    protocol["sha256"] = digest(protocol["identity"])
    with ZipFile(tmp_path / "protocol.source.zip", "w"):
        pass
    raw.update(
        evaluated_count=40, pending_or_unrun_ids=[], budget={"usd": 0}, prior_usd=0, combined_harness_usd=0
    )
    for case in cases:
        summary = {
            "episode_id": case["id"],
            "correct": True,
            "model_calls": 0,
            "estimated_or_reserved_usd": 0,
            "elapsed_seconds": 1,
            "skill": "",
        }
        ppc = {
            "final": summary,
            "adopted": False,
            "retrieved": [],
            "applied": [],
            "executor_read_skill_ids": [],
            "executor_activated_skill_ids": [],
            "context_policy_effective": False,
        }
        raw["results"].append(
            {
                "episode_id": case["id"],
                "family": case["family"],
                "entity_group": case["entity_group"],
                "h0": summary,
                "ppc_no_memory": ppc,
                "ppc_memory": ppc,
                "usd_retries": {
                    "selected_trace": f"C:/study/{case['id']}/h0",
                    "selected_summary": summary,
                    "extra_allocation_usd": 0,
                    "actual": {"calls": 0, "usd": 0},
                },
                "costs": {
                    scope: {"calls": 0, "usd": 0} for scope in ("h0", "no-memory", "memory", "usd-retries")
                },
                "mechanism": {"counts": {}},
            }
        )
        for suffix in ("h0", "no-memory/final", "memory/final"):
            path = tmp_path / case["id"] / suffix
            path.mkdir(parents=True)
            (path / "turn-1.json").write_text(
                json.dumps(
                    {
                        "grade": {"failures": []},
                        "response": {"artifacts": []},
                        "journal": [],
                    }
                )
            )
    for name, value in (
        ("protocol.json", protocol),
        ("report.json", raw),
        ("completed-or-stopped-ledger.json", {"events": [], "attempts": 0, "estimated_or_reserved_usd": 0}),
    ):
        (tmp_path / name).write_text(json.dumps(value))
    result = report(tmp_path, corpus)
    assert result["complete"]
    assert result["observed_complete_task_rows"] == 40
    assert all(arm["correct_over_planned"]["successes"] == 40 for arm in result["arms"].values())
    assert "nli_arm_costs" not in result["formal_costs"]
    assert len(result["by_family"]) == 20
    assert all(v["h0"]["correct_over_planned"]["denominator"] == 2 for v in result["by_family"].values())
    amendment = {
        "source_protocol_identity": protocol["sha256"],
        "interrupted_runs": [{"recording": "ep_00/h0/turn-1-run.json"}],
    }
    (tmp_path / "recovery-amendment.json").write_text(json.dumps(amendment))
    recovered = report(tmp_path, corpus)
    assert recovered["recovery_amendment"] == amendment
    assert recovered["rows"][0]["had_pending_recording_at_desktop_crash"]
    amendment["source_protocol_identity"] = "another protocol"
    (tmp_path / "recovery-amendment.json").write_text(json.dumps(amendment))
    with pytest.raises(ValueError, match="another protocol"):
        report(tmp_path, corpus)


def test_archive_requires_closed_receipt_and_excludes_runtime_and_active_alias(tmp_path):
    (tmp_path / "ledger.json").write_text("must not read this")
    with pytest.raises(ValueError, match="not closed"):
        records(tmp_path)
    (tmp_path / "completed-or-stopped-ledger.json").write_text("{}")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/private.json").write_text("{}")
    (tmp_path / "report.json").write_text("{}")
    assert {p.name for p in records(tmp_path)} == {"completed-or-stopped-ledger.json", "report.json"}
