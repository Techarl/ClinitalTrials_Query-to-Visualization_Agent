import copy
import hashlib
import json
import sys
from collections import Counter

import pytest

from scripts import report_core300 as report


def fixture():
    episodes = []
    for index in range(4):
        episodes.append(
            {
                "id": str(index),
                "family": "single_phase" if index < 3 else "dialogue_start",
                "entity_group": "diseaseA" if index < 2 else "diseaseB",
                "split": "development" if index < 2 else "heldout",
                "turns": [{"query": "PRIVATE QUESTION", "expected": {"private": "ANSWER"}}]
                * (3 if index == 3 else 1),
            }
        )
    corpus = {"episodes": episodes}
    identity = {"parameters": {"episodes": copy.deepcopy(episodes)}}
    protocol = {"identity": identity, "sha256": report.digest(identity)}
    raw = {
        "episodes": [
            {"episode_id": "0", "split": "development", "correct": True, "turns": 1, "failures": [[]]},
            {"episode_id": "1", "split": "development", "correct": False, "status": "runner_failed"},
            {"episode_id": "2", "split": "heldout", "correct": False, "status": "not_run"},
            {
                "episode_id": "3",
                "split": "heldout",
                "correct": False,
                "turns": 3,
                "failures": [[], ["constraint:medicine", "source_verification:ValueError"], []],
            },
        ],
        "summary": {"development": {"episodes": 2, "correct": 1}, "heldout": {"episodes": 2, "correct": 0}},
    }
    return corpus, protocol, raw


def test_fixed_denominator_failed_and_not_run_included_and_safe_export():
    corpus, protocol, raw = fixture()
    episodes = report.validate_corpus(corpus, protocol, expected=4)
    rows = report.validate_results(raw, episodes)
    summary = report.aggregate(rows, draws=50)
    assert summary["all"]["rate"] == 0.25
    assert summary["all"]["denominator"] == 4
    assert summary["all"]["planned_turns"] == 6
    assert summary["split"]["heldout"]["denominator"] == 2
    assert summary["all"]["status_counts"] == {"graded": 2, "runner_failed": 1, "not_run": 1}
    assert summary["all"]["failure_code_occurrences"]["constraint"] == 1
    assert "PRIVATE" not in json.dumps(rows) and "ANSWER" not in json.dumps(rows)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda raw: raw["episodes"].pop(), "every planned episode"),
        (lambda raw: raw["episodes"].append(raw["episodes"][0]), "Duplicate result"),
        (lambda raw: raw["episodes"][1].update(correct=True), "cannot be successful"),
        (lambda raw: raw["episodes"][3].update(turns=2), "Incomplete dialogue"),
        (lambda raw: raw["episodes"][0].update(correct=False), "contradicts"),
        (lambda raw: raw["summary"]["heldout"].update(correct=1), "summary differs"),
        (lambda raw: raw["episodes"][0].update(split="heldout"), "split/family"),
    ],
)
def test_malformed_final_reports_rejected(mutation, match):
    corpus, _, raw = fixture()
    mutation(raw)
    with pytest.raises(ValueError, match=match):
        report.validate_results(raw, corpus["episodes"])


def test_protocol_mismatch_and_group_split_leak_rejected():
    corpus, protocol, _ = fixture()
    corpus["episodes"][0]["turns"][0]["query"] = "Changed prompt"
    with pytest.raises(ValueError, match="contract differs"):
        report.validate_corpus(corpus, protocol, expected=4)
    corpus, protocol, _ = fixture()
    corpus["episodes"][1]["split"] = "heldout"
    protocol["identity"]["parameters"]["episodes"] = copy.deepcopy(corpus["episodes"])
    protocol["sha256"] = report.digest(protocol["identity"])
    with pytest.raises(ValueError, match="leaks across splits"):
        report.validate_corpus(corpus, protocol, expected=4)


def test_cluster_bootstrap_resamples_whole_disease_groups():
    rows = [{"entity_group": group, "correct": group == "a"} for group in "ab" for _ in range(20)]
    observed = report.clustered_rate(rows, draws=400)
    assert observed["clusters"] == 2
    assert observed["cluster_bootstrap95"] == [0, 1]
    assert observed["few_cluster_warning"] is True
    assert observed == report.clustered_rate(rows, draws=400)
    assert report.clustered_rate([])["cluster_bootstrap95"] is None


def test_tagged_accounting_retains_unknown_and_unattributed_attempts():
    events = {
        "events": [
            {"episode_id": "a", "status": "completed", "estimated_cost_usd": 0.01},
            {"episode_id": "a", "status": "attempted_usage_unknown", "reserved_upper_estimate_usd": 0.03},
            {
                "episode_id": "unexpected",
                "status": "completed_usage_unknown",
                "reserved_upper_estimate_usd": 0.02,
            },
        ]
    }
    counts = report.tagged_costs(events, [{"id": "a"}])
    assert counts["a"]["attempts"] == 2
    assert counts["a"]["estimated_or_reserved_usd"] == pytest.approx(0.04)
    assert counts["unattributed"]["attempts"] == 1
    assert "unexpected" not in counts


def test_frozen_source_count_hash_and_cross_population_dedup(tmp_path):
    paths = {}
    for index in range(15):
        folder = tmp_path / "snapshots" / f"ds_{index}"
        folder.mkdir(parents=True)
        raw = [{"protocolSection": {"identificationModule": {"nctId": f"nct_{max(0, index - 1)}"}}}]
        manifest = {
            "dataset_id": folder.name,
            "query": {},
            "expected_count": 1,
            "retrieved_unique_count": 1,
            "complete": True,
            "raw_hash": report.digest(raw),
            "finished_at": "2026-11-01",
            "source_version_before": {"version": 1},
            "source_version_after": {"version": 1},
        }
        (folder / "studies.json").write_text(json.dumps(raw))
        path = folder / "manifest.json"
        path.write_text(json.dumps(manifest))
        paths[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    identity = {"source_manifests_sha256": paths}
    environment = {"identity": identity, "sha256": report.digest(identity)}
    sources = report.source_audit(tmp_path, environment)
    assert sources["population_record_sum"] == 15 and sources["unique_trial_count"] == 14
    (tmp_path / "snapshots" / "ds_0" / "studies.json").write_text("[]")
    with pytest.raises(ValueError, match="hash/count mismatch"):
        report.source_audit(tmp_path, environment)


def test_partial_formal_run_never_read_or_published(tmp_path, monkeypatch):
    study = tmp_path / "study"
    study.mkdir()
    (study / "partial-result.json").write_text("must not read this")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["report_core300", "--study", str(study)])
    with pytest.raises(SystemExit) as error:
        report.main()
    assert error.value.code == 2
    assert not (tmp_path / "docs").exists()


def test_full300_corpus_shape_from_synthetic_inputs():
    episodes = []
    for group in range(15):
        split = "development" if group < 3 else "calibration" if group < 5 else "heldout"
        families = ["single_phase"] * 8 + ["compound_phase_start"] * (6 if group < 5 else 5)
        families += ["dialogue_start"] * 4 + ["boundary_empty_time"] * (2 if group < 5 else 3)
        for number, family in enumerate(families):
            episodes.append(
                {
                    "id": f"{group}-{number}",
                    "split": split,
                    "entity_group": str(group),
                    "family": family,
                    "turns": [{}] * (3 if family.startswith("dialogue") else 1),
                }
            )
    identity = {"parameters": {"episodes": episodes}}
    validated = report.validate_corpus(
        {"episodes": episodes}, {"identity": identity, "sha256": report.digest(identity)}
    )
    assert len(validated) == 300
    assert Counter(e["split"] for e in validated)["heldout"] == 200
