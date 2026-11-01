import json
from pathlib import Path

import pytest

from evaluation.public_datasets import nli4ct
from experiments.nli4ct_study import evidence_metrics


def test_evidence_metrics_count_missing_and_conflicting_required_evidence():
    assert evidence_metrics(["support"], ["support", "conflict"]) == {
        "recall": 0.5, "precision": 1.0, "complete": False,
    }
    assert evidence_metrics(["support", "conflict", "noise"], ["support", "conflict"])["complete"]


def test_official_nli_adapter_keeps_gold_out_of_runtime():
    root = Path("data/external-benchmarks")
    if not (root / "nli4ct.zip").exists():
        pytest.skip("Optional official dataset not downloaded")
    tasks, gold = nli4ct(root, "dev")
    assert len(tasks) == len(gold) == 200
    for task in tasks:
        assert not {"label", "Label", "evidence_ids", "Primary_evidence_index"} & task.keys()
        ids = {p["id"] for p in task["passages"]}
        assert set(gold[task["id"]]["evidence_ids"]) <= ids
        assert all("text" in p and "role" in p for p in task["passages"])
    assert "Primary_evidence_index" not in json.dumps(tasks)
