import json

import pytest

from evaluation.contracts import Episode
from experiments.harness_v2 import Component, ExperienceBank
from experiments.harness_v3 import (
    StudyBudget,
    copy_bank,
    metrics,
    progress,
    public_episode_probe,
    selection,
    source_hashes,
)


def test_locked_selection_has_unique_disjoint_tasks_and_group_coverage():
    from pathlib import Path

    corpus = Path("data/core300-frozen/cases.json")
    if not corpus.exists():
        pytest.skip("Frozen core300 dataset not installed")
    cases = [Episode.model_validate(e) for e in json.loads(corpus.read_text(encoding="utf-8"))["episodes"]]
    development, formal = selection(cases)
    assert len(development) == len({e.id for e in development}) == 20
    assert len(formal) == len({e.id for e in formal}) == 40
    assert {e.entity_group for e in development}.isdisjoint({e.entity_group for e in formal})
    assert len({e.entity_group for e in formal}) == 10
    assert all(sum(t.entity_group == e.entity_group for t in formal) == 4 for e in formal)
    # Mutating hidden labels cannot alter selection.
    changed = [e.model_copy(update={"turns": []}) for e in cases]
    assert [[e.id for e in group] for group in selection(changed)] == [
        [e.id for e in development],
        [e.id for e in formal],
    ]


def test_usd_retry_scope_enforces_reservation_and_recovers_after_restart(tmp_path):
    budget = StudyBudget(tmp_path / "ledger.json", 1)
    with budget.scope("retry", 0.05):
        budget.before(1000, 4096)
        with pytest.raises(RuntimeError, match="allocation exhausted"):
            budget.before(1000, 4096)
    restored = StudyBudget(tmp_path / "ledger.json", 1)
    assert metrics(restored.scope_events("retry"))["unknown_usage_calls"] == 1
    with restored.scope("retry", 0.05):
        with pytest.raises(RuntimeError, match="allocation exhausted"):
            restored.before(1000, 4096)
    assert len(restored.events) == 1
    with restored.scope("another-arm", 0.05):
        restored.before(1000, 4096)
    assert len(restored.events) == 2


def test_frozen_bank_copy_isolated_and_negative_lessons_survive_resume(tmp_path):
    bank = ExperienceBank()
    before = {"model_calls": 5, "observations": [], "trace": "before"}
    after = {"model_calls": 7, "observations": [], "trace": "after"}
    bank.add(
        "phase", before, after, Component(skill="Inspect every field first.", rationale="diagnostic"), False
    )
    checkpoint = tmp_path / "bank.json"
    checkpoint.write_text(json.dumps(bank.entries))
    restored = copy_bank(json.loads(checkpoint.read_text()))
    assert restored.retrieve("phase", before) == bank.retrieve("phase", before)
    assert restored.retrieve("phase", before)[0]["adopted"] is False
    independent = copy_bank(restored.entries)
    independent.entries.clear()
    assert len(restored.entries) == 1


def test_metrics_include_failed_unknown_and_unselected_costs():
    events = [
        {"status": "attempted_usage_unknown", "reserved_upper_estimate_usd": 0.2},
        {
            "status": "completed",
            "reserved_upper_estimate_usd": 0.4,
            "estimated_cost_usd": 0.1,
            "metadata": {"usage": {"input_tokens": 400, "output_tokens": 20}},
        },
    ]
    result = metrics(events)
    assert result["usd"] == pytest.approx(0.3)
    assert result["calls"] == 2 and result["unknown_usage_calls"] == 1
    assert result["input_tokens"] == 400 and result["output_tokens"] == 20


def test_retry_public_selection_checks_later_turn_failure_without_reading_gold(tmp_path):
    for index, status in enumerate(["completed", "failed"], 1):
        (tmp_path / f"turn-{index}.json").write_text(
            json.dumps(
                {
                    "response": {
                        "status": status,
                        "unmet_goals": [] if index == 1 else ["follow-up"],
                        "usage": {"model_calls": 2, "estimated_or_reserved_usd": 0.01, "receipts": []},
                    },
                    "state": {"observations": []},
                    "grade": {"correct": True, "hidden_answer": "not an input"},
                }
            )
        )
    first = public_episode_probe(tmp_path)
    assert first["status"] == "partial" and first["unmet_goals"] == ["follow-up"]
    assert first["model_calls"] == 4
    for file in tmp_path.glob("turn-*.json"):
        data = json.loads(file.read_text())
        data["grade"] = {"correct": False, "hidden_answer": "different"}
        file.write_text(json.dumps(data))
    assert public_episode_probe(tmp_path) == first


def test_cached_ppc_restores_exact_public_bank_without_model_call(tmp_path, monkeypatch):
    from app.config import Settings
    from experiments import harness_v2

    episode = Episode(
        id="resume",
        family="phase",
        entity_group="heldout disease",
        split="heldout",
        origin="authored",
        turns=[{"query": "Show phase counts", "expected": {}}],
    )
    incoming = {"negative": {"id": "negative", "adopted": False}}
    checkpoint = {**incoming, "positive": {"id": "positive", "adopted": True}}
    monkeypatch.setattr(harness_v2, "execution_identity", lambda *args, **kwargs: kwargs)
    # Baseline path and incoming bank are protocol inputs even for cached PPC.
    root, baseline = tmp_path / "ppc", tmp_path / "h0"
    root.mkdir()
    (root / "ppc.json").write_text(json.dumps({"bank_checkpoint": checkpoint, "adopted": True}))
    bank = copy_bank(incoming)
    result = harness_v2.ppc(episode, Settings(), root, None, baseline, bank)
    assert bank.entries == checkpoint and result["adopted"]
    restarted = copy_bank(incoming)
    harness_v2.ppc(episode, Settings(), root, None, baseline, restarted)
    assert restarted.entries == bank.entries
    with pytest.raises(ValueError, match="identity changed"):
        harness_v2.ppc(episode, Settings(), root, None, baseline, copy_bank({}))


def test_source_identity_includes_nested_records_and_progress_keeps_unrun(tmp_path):
    nested = tmp_path / "snapshots" / "ds_abc"
    nested.mkdir(parents=True)
    raw = nested / "studies.json"
    raw.write_text("[]")
    first = source_hashes(tmp_path)
    assert str(raw) in first
    raw.write_text('[{"changed":true}]')
    assert source_hashes(tmp_path)[str(raw)] != first[str(raw)]
    from types import SimpleNamespace

    planned = [SimpleNamespace(id="failed"), SimpleNamespace(id="unrun")]
    result = progress(
        [{"episode_id": "failed", "h0": {"correct": False}, "ppc_memory": {"final": {"correct": False}}}],
        planned,
    )
    assert result["planned_count"] == 2 and result["evaluated_count"] == 1
    assert result["unrun_ids"] == ["unrun"] and result["h0_correct"] == 0


def test_retry_stops_after_denied_reservation_without_repeating_empty_runs(tmp_path, monkeypatch):
    from app.config import Settings
    from experiments import harness_v3

    episode = Episode(
        id="budget",
        family="phase",
        entity_group="x",
        split="development",
        turns=[{"query": "phase", "expected": {}}],
    )
    baseline = tmp_path / "h0"
    baseline.mkdir()

    def files(directory, status, calls):
        directory.mkdir(exist_ok=True, parents=True)
        (directory / "summary.json").write_text(
            json.dumps({"model_calls": calls, "correct": status == "completed"})
        )
        (directory / "turn-1.json").write_text(
            json.dumps(
                {
                    "response": {
                        "status": status,
                        "unmet_goals": [],
                        "usage": {"model_calls": calls, "estimated_or_reserved_usd": 0, "receipts": []},
                    },
                    "state": {"observations": []},
                }
            )
        )

    files(baseline, "completed", 4)
    attempted = []

    def denied(_episode, _settings, path, _budget, _frozen):
        attempted.append(path)
        files(path, "failed", 1)
        return {"model_calls": 1}

    monkeypatch.setattr(harness_v3, "run_episode", denied)
    budget = StudyBudget(tmp_path / "ledger.json", 1)
    result = harness_v3.retry_selection(
        episode, Settings(), tmp_path / "retry", budget, tmp_path, baseline, 0.01
    )
    assert len(attempted) == 1 and result["selected_trace"] == str(baseline)
    assert result["actual"]["calls"] == 0
