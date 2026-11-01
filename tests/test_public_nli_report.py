import copy
import json
import sys

import pytest

from scripts import report_public_nli as report


def fixture():
    ids = ["a", "b", "c", "d"]
    identity = {
        "parameters": {
            "ids": ids,
            "answer_ids": ["a", "b"],
            "arms": report.ARMS,
            "context_tokens": 1024,
            "candidate_limit": 24,
        }
    }
    protocol = {"identity": identity, "sha256": report.digest(identity)}
    strata = {
        "formal_protocol_sha256": protocol["sha256"],
        "label_access": False,
        "outcomes_used": False,
        "cases": [
            {
                "case_id": identity,
                "type": "Single" if index < 2 else "Comparison",
                "section": ["Intervention", "Eligibility", "Results", "Adverse Events"][index],
                "full_context_fits_1024": index < 2,
                "source_fits_24_candidate_limit": index < 3,
                "shared_trial_component": "first" if index < 2 else "second",
            }
            for index, identity in enumerate(ids)
        ],
    }
    cases = []
    for identity in ids:
        for arm in report.ARMS:
            failed = identity == "a" and arm == "jev"
            row = {
                "id": identity,
                "arm": arm,
                "status": "failed" if failed else "completed",
                "complete": not failed,
                "recall": 0 if failed else 1,
                "precision": 0 if failed else 0.5,
            }
            if not failed:
                row.update(
                    answer_correct=identity == "a" if identity in {"a", "b"} else None,
                    answer={
                        "label": "SECRET PREDICTION",
                        "evidence_ids": ["PRIVATE ID"],
                        "citation_ids_valid": True,
                    }
                    if identity in {"a", "b"}
                    else None,
                    context_tokens=100,
                    ranking_seconds=2.5,
                    statement="DO NOT EXPORT THIS",
                )
            cases.append(row)
    summary = {}
    for arm in report.ARMS:
        subset = [r for r in cases if r["arm"] == arm]
        summary[arm] = {
            "planned": 4,
            "answer_planned": 2,
            "completed": sum(r["status"] == "completed" for r in subset),
            "complete_evidence": sum(r["complete"] for r in subset),
            "answer_correct": sum(r.get("answer_correct") is True for r in subset),
            "evidence_recall": sum(r["recall"] for r in subset) / 4,
            "evidence_precision": sum(r["precision"] for r in subset) / 4,
        }
    return {"cases": cases, "summary": summary}, protocol, strata


def validated():
    return report.validate_and_sanitize(*fixture(), expected_cases=4, expected_answers=2)


def test_failure_stays_in_evidence_and_frozen_answer_denominators():
    rows = validated()
    summary = report.summarize(rows)
    assert summary["jev"]["complete_evidence"]["rate"] == 3 / 4
    assert summary["jev"]["answer_correct"]["denominator"] == 2
    assert summary["jev"]["answer_correct"]["successes"] == 0
    assert summary["jev"]["answer_failures"] == 1
    assert summary["full"]["answer_correct"]["rate"] == 1 / 2
    serialized = json.dumps(rows)
    assert "SECRET" not in serialized and "PRIVATE" not in serialized and "DO NOT EXPORT" not in serialized
    assert "evidence_ids" not in serialized and "label" not in serialized


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda raw, protocol, strata: raw["cases"].append(raw["cases"][0]), "Duplicate case/arm"),
        (lambda raw, protocol, strata: raw["cases"].pop(), "Incomplete formal report"),
        (
            lambda raw, protocol, strata: strata.update(formal_protocol_sha256="wrong"),
            "different formal protocol",
        ),
        (
            lambda raw, protocol, strata: protocol["identity"]["parameters"].update(context_tokens=2048),
            "digest mismatch",
        ),
        (lambda raw, protocol, strata: raw["summary"]["jev"].update(answer_correct=1), "disagree"),
        (lambda raw, protocol, strata: strata["cases"].append(strata["cases"][0]), "Duplicate stratum"),
        (lambda raw, protocol, strata: raw["cases"][0].update(answer_correct=None), "Planned answer missing"),
        (lambda raw, protocol, strata: strata.update(outcomes_used=True), "input-only"),
    ],
)
def test_protocol_and_result_mismatches_reject_before_publication(mutation, match):
    raw, protocol, strata = fixture()
    mutation(raw, protocol, strata)
    with pytest.raises(ValueError, match=match):
        report.validate_and_sanitize(raw, protocol, strata, expected_cases=4, expected_answers=2)


def test_wilson_known_values_and_empty_denominator():
    assert report.wilson(0, 0)["wilson95"] is None
    assert report.wilson(5, 10)["wilson95"] == pytest.approx([0.2365930905, 0.7634069095])
    assert report.wilson(0, 10)["wilson95"][1] == pytest.approx(0.2775327999)


def test_cluster_bootstrap_resamples_components_and_preserves_pairs():
    rows = validated()
    difference = report.paired_cluster_difference(rows, "jev", "hybrid", "complete_evidence", draws=500)
    reverse = report.paired_cluster_difference(rows, "hybrid", "jev", "complete_evidence", draws=500)
    assert difference["difference"] == -0.25
    assert difference["clusters"] == 2
    assert difference["cluster_bootstrap95"] == [-0.5, 0]
    assert reverse["cluster_bootstrap95"] == [0, 0.5]
    answers = report.paired_cluster_difference(rows, "jev", "hybrid", "answer_correct", draws=500)
    assert answers["clusters"] == 1 and answers["denominator"] == 2
    assert answers["cluster_bootstrap95"] == [-0.5, -0.5]
    assert difference == report.paired_cluster_difference(
        rows, "jev", "hybrid", "complete_evidence", draws=500
    )


def test_every_registered_stratum_and_all_arms_are_retained():
    strata = report.aggregate(validated(), draws=20)
    assert len(strata) == 11  # all + budget pair + pool pair + types pair + four sections
    assert strata["full_context_fits_1024=True"]["arms"]["jev"]["complete_evidence"]["denominator"] == 2
    assert strata["section=Results"]["arms"]["jev"]["answer_correct"]["denominator"] == 0
    assert set(strata["all"]["arms"]) == set(report.ARMS)


def ledger():
    return {
        "attempts": 3,
        "estimated_or_reserved_usd": 0.302,
        "events": [
            {
                "status": "completed",
                "estimated_cost_usd": 0.1,
                "metadata": {"reported_model": "test-model"},
                "stage": "answer",
            },
            {
                "status": "completed",
                "estimated_cost_usd": 0.002,
                "metadata": {"requested_model": "text-embedding-3-small"},
            },
            {"status": "attempted_usage_unknown", "reserved_upper_estimate_usd": 0.2},
        ],
    }


def test_ledger_includes_unknown_embedding_and_deduplicates_only_exact_ledger_copies(tmp_path):
    original = tmp_path / "original.json"
    duplicate = tmp_path / "duplicate.json"
    original.write_text(json.dumps(ledger()))
    duplicate.write_text(json.dumps(ledger(), indent=2))
    costs = report.accounting({"pilot": original, "pilot_copy": duplicate})
    assert costs["totals"]["estimated_or_reserved_usd"] == pytest.approx(0.302)
    assert costs["totals"]["unknown_usage_attempts"] == 1
    assert costs["totals"]["embedding_cost_usd"] == pytest.approx(0.002)
    assert costs["totals"]["attempts"] == 3
    assert costs["duplicate_ledger_inputs_skipped"] == ["pilot_copy"]
    other = copy.deepcopy(ledger())
    other["events"][0]["estimated_cost_usd"] = 0.15
    other["estimated_or_reserved_usd"] = 0.352
    duplicate.write_text(json.dumps(other))
    assert report.accounting({"pilot": original, "formal": duplicate})["totals"]["attempts"] == 6
    other["attempts"] = 0
    with pytest.raises(ValueError, match="attempt count"):
        report.ledger_summary(other)
    other["attempts"], other["estimated_or_reserved_usd"] = 3, 0
    with pytest.raises(ValueError, match="reconcile"):
        report.ledger_summary(other)


def test_absent_report_does_not_read_partial_case_outputs_or_emit_results(tmp_path, monkeypatch):
    study = tmp_path / "study"
    study.mkdir()
    (study / "partially-scored-case.json").write_text("invalid JSON, must never read")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["report_public_nli", "--study", str(study)])
    with pytest.raises(SystemExit) as error:
        report.main()
    assert error.value.code == 2
    assert not (tmp_path / "docs").exists()
