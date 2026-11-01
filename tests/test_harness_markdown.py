import copy
import hashlib
import json
from zipfile import ZipFile

import pytest

import scripts.render_harness_v3 as renderer
from scripts.report_harness_v3 import ARMS, aggregate


def synthetic():
    correct = {"h0": 35, "ppc_no_memory": 34, "ppc_memory": 37, "usd_retries": 36}
    research = {"h0": 0.01, "ppc_no_memory": 0.02, "ppc_memory": 0.0225, "usd_retries": 0.0175}
    rows = [
        {
            "episode_id": f"fake-{i}",
            "arms": {
                arm: {
                    "correct": i < correct[arm],
                    "original_correct": i < 36,
                    "model_calls": 1,
                    "final_execution_usd": 0.01,
                    "research_usd": research[arm],
                    "elapsed_seconds": 2,
                }
                for arm in ARMS
            },
        }
        for i in range(40)
    ]
    editors = {
        "editor_retrieved_id_occurrences": 7,
        "editor_declared_reuse_id_occurrences": 3,
        "publicly_adopted_tasks": 5,
        "adopted_nonempty_procedure_tasks": 4,
        "adopted_empty_procedure_tasks": 1,
        "final_nonempty_injected_procedure_tasks": 6,
        "combined_probe_context_clipping_tasks": 2,
    }
    formal = {
        "planned_tasks": 40,
        "observed_complete_task_rows": 40,
        "complete": True,
        "arms": aggregate(rows),
        "rows": rows,
        "task_exceptions": [],
        "unrun_ids": [],
        "paired": {
            arm: {"difference": (correct[arm] - 35) / 40, "cluster_bootstrap95": [-0.01, 0.12]}
            for arm in ARMS[1:]
        },
        "formal_costs": {
            "attempts": 200,
            "known_cost_usd": 1.5,
            "unknown_reserved_usd": 0.1,
            "estimated_or_reserved_usd": 1.6,
            "unknown_usage_attempts": 1,
            "recorded_input_tokens": 100000,
            "recorded_output_tokens": 10000,
        },
        "prior_main_receipts": [],
        "prior_main_study_usd": 3.0,
        "combined_main_study_usd": 4.6,
        "budget_matching": {
            "retry_extra_allowance_usd": 0.4,
            "retry_actual_or_reserved_usd": 0.3,
            "ppc_no_memory_incremental_usd": 0.4,
            "maximum_attempts_per_task": 20,
            "partial_or_exception_task_cost_usd": 0,
        },
        "mechanisms": {
            "editors": {"ppc_no_memory": dict.fromkeys(editors, 0), "ppc_memory": editors},
            "memory_final_all_turn_tool_events": {
                "search_calls": 2,
                "learned_read": 1,
                "engineering_builtin_read": 3,
            },
            "memory_final_tasks_with_learned_read": 1,
            "memory_final_tasks_with_learned_apply": 0,
        },
        "recovery_amendment": {
            "interrupted_runs": [{}, {}],
            "completed_tasks": 6,
            "private_local_path": "C:/Users/private@example.com/secret",
        },
        "protocol_sha256": "a" * 64,
        "ledger_sha256": "b" * 64,
    }
    return formal


def ledger_archive(tmp_path, formal):
    archive = tmp_path / "closed-ledgers.zip"
    inputs = [
        ("main01/stopped-infrastructure-ledger.json", 1.0, True, True),
        ("main02/completed-development-and-online-ledger.json", 2.0, True, True),
        ("pilot/completed-all-pilot-ledger.json", 0.25, False, False),
        ("smoke/completed-ledger.json", 0.1, True, False),
    ]
    with ZipFile(archive, "w") as zipped:
        for name, cost, known, main in inputs:
            event = {
                "status": "completed" if known else "attempted_usage_unknown",
                "reserved_upper_estimate_usd": cost,
            }
            if known:
                event["estimated_cost_usd"] = cost
            data = json.dumps({"events": [event], "attempts": 1, "estimated_or_reserved_usd": cost}).encode()
            zipped.writestr(name, data)
            if main:
                formal["prior_main_receipts"].append(
                    {"sha256": hashlib.sha256(data).hexdigest(), "attempts": 1, "usd": cost}
                )
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"files": {archive.name: {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}}})
    )
    return archive, index


def test_full_renderer_separates_original_audit_costs_and_mechanism(tmp_path):
    formal = synthetic()
    archive, index = ledger_archive(tmp_path, formal)
    costs = renderer.historical_costs(archive, index, formal)
    text = renderer.render(formal, costs)
    assert "| Strong H0 | 36/40 | 35/40 | 35/40 | 40 | 80.000 |" in text
    assert "Selected-final seconds (sum)" in text
    assert "exclude editor calls, candidate probes and unselected retries" in text
    assert "pure model latency" in text and "resumed segment" in text
    assert "| PPC with frozen memory | 40 | 2 | 0 | +5.00 | [-1.00, +12.00] |" in text
    assert "**$4.950000**" in text
    assert "| Adopted empty procedures | 0 | 1 |" in text
    assert "| Learned skill reads | 1 |" in text
    assert "| Learned skill activations | 0 |" in text
    assert "unused difference is $0.100000" in text
    assert "at most 20 retries" in text
    assert "recursive self-improvement" in text and "do not by themselves" in text
    assert "not byte-identical" in text
    assert "private@example.com" not in text and "C:/Users" not in text
    assert "not a wholly independent rescore" in text
    assert costs["receipts"][2]["unknown_reserved_usd"] == 0.25


def test_incomplete_report_retains_forty_denominator(tmp_path):
    formal = synthetic()
    archive, index = ledger_archive(tmp_path, formal)
    prior = renderer.historical_costs(archive, index, formal)
    formal.update(
        rows=formal["rows"][:38],
        observed_complete_task_rows=38,
        complete=False,
        unrun_ids=["fake-38", "fake-39"],
    )
    formal["arms"] = aggregate(formal["rows"])
    text = renderer.render(formal, prior)
    assert "**closed incomplete**" in text and "**38/40 complete four-arm" in text
    assert "| Strong H0 | 36/40 | 35/40 | 35/38 |" in text
    assert "Unrun tasks: **2**" in text


@pytest.mark.parametrize("mutation", ["archive_hash", "main_total", "receipt_hash", "overlap"])
def test_prior_costs_reject_unverifiable_or_double_counted_receipts(tmp_path, mutation):
    formal = synthetic()
    archive, index = ledger_archive(tmp_path, formal)
    if mutation == "archive_hash":
        archive.write_bytes(archive.read_bytes() + b"changed")
    elif mutation == "main_total":
        formal["prior_main_study_usd"] += 0.1
    elif mutation == "receipt_hash":
        formal["prior_main_receipts"][0]["sha256"] = "wrong"
    else:
        # A cumulative receipt cannot be added a second time under a new filename.
        with ZipFile(archive, "a") as zipped:
            zipped.writestr(
                "overlapping.json", zipped.read("main02/completed-development-and-online-ledger.json")
            )
        index.write_text(
            json.dumps(
                {"files": {archive.name: {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}}}
            )
        )
    with pytest.raises(ValueError):
        renderer.historical_costs(archive, index, formal)


def test_cli_refuses_active_study_before_writing_markdown(tmp_path, monkeypatch):
    output = tmp_path / "results.md"
    (tmp_path / "ledger.json").write_text("do not read active ledger")
    monkeypatch.setattr(
        "sys.argv", ["renderer", "--study", str(tmp_path), "--cases", "missing", "--output", str(output)]
    )
    with pytest.raises(ValueError, match="not closed"):
        renderer.main()
    assert not output.exists()


def test_pure_renderer_is_deterministic_and_does_not_mutate_input(tmp_path):
    formal = synthetic()
    archive, index = ledger_archive(tmp_path, formal)
    prior = renderer.historical_costs(archive, index, formal)
    original = copy.deepcopy(formal)
    assert renderer.render(formal, prior) == renderer.render(formal, prior)
    assert formal == original
