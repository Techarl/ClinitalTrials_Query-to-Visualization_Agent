import copy
import json
import re
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
                    selected_ids=["PRIVATE ID", "p2"] if arm == "full" else ["PRIVATE ID"],
                    ranking=["PRIVATE ID", "p2"],
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
    assert {"jev-minus-luna", "jev-minus-cross_encoder"} <= set(strata["all"]["paired_differences"])


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


def test_ledger_retains_independent_equal_content_and_deduplicates_only_file_aliases(tmp_path):
    original = tmp_path / "original.json"
    duplicate = tmp_path / "duplicate.json"
    original.write_text(json.dumps(ledger()))
    duplicate.write_text(json.dumps(ledger(), indent=2))
    costs = report.accounting({"pilot": original, "pilot_alias": original.parent / "." / original.name})
    assert costs["totals"]["estimated_or_reserved_usd"] == pytest.approx(0.302)
    assert costs["totals"]["unknown_usage_attempts"] == 1
    assert costs["totals"]["embedding_cost_usd"] == pytest.approx(0.002)
    assert costs["totals"]["attempts"] == 3
    assert costs["duplicate_ledger_inputs_skipped"] == ["pilot_alias"]
    repeated = report.accounting({"pilot": original, "independent": duplicate})
    assert repeated["totals"]["attempts"] == 6
    assert repeated["identical_content_ledgers_retained"] == [{"ledger": "independent", "same_content_as": "pilot"}]
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


@pytest.mark.parametrize("mutation,match", [
    (lambda row: row.update(context_tokens=1025), "selection budget"),
    (lambda row: row.pop("context_tokens"), "execution measurement"),
    (lambda row: row.update(ranking=["PRIVATE ID"] + [str(i) for i in range(24)]), "candidate limit"),
    (lambda row: row.update(selected_ids=["outside"]), "outside the ranked"),
    (lambda row: row.update(ranking=["PRIVATE ID", "other"]), "different candidate pools"),
    (lambda row: row["answer"].update(citation_ids_valid=False), "membership flag"),
])
def test_budget_pool_and_citation_contracts_are_enforced(mutation, match):
    raw, protocol, strata = fixture()
    mutation(raw["cases"][0])
    with pytest.raises(ValueError, match=match):
        report.validate_and_sanitize(raw, protocol, strata, expected_cases=4, expected_answers=2)


def test_full_control_exemption_and_empty_citations_are_explicit():
    raw, protocol, strata = fixture()
    full = next(row for row in raw["cases"] if row["id"] == "a" and row["arm"] == "full")
    full["context_tokens"] = 8000
    full["answer"]["evidence_ids"] = []
    rows = report.validate_and_sanitize(raw, protocol, strata, expected_cases=4, expected_answers=2)
    values = report.summarize(rows)["full"]
    assert values["answer_correct"]["successes"] == 1
    assert values["valid_citation_ids"]["successes"] == 2  # Empty subset is membership-valid.
    assert values["nonempty_citations"]["successes"] == 1
    assert values["answer_correct_with_nonempty_valid_ids"]["successes"] == 0


def test_tagged_arm_costs_keep_unknown_attempts_and_shared_embeddings_separate():
    value = ledger()
    value["events"][0]["arm"] = "luna"
    value["events"][2]["arm"] = "jev"
    costs = report.ledger_summary(value)
    assert costs["nli_arm_costs"]["jev"]["unknown_reserved_usd"] == 0.2
    assert costs["nli_arm_costs"]["luna"]["known_cost_usd"] == 0.1
    assert costs["nli_arm_costs"]["shared_or_unattributed"]["known_cost_usd"] == 0.002
    assert sum(row["estimated_or_reserved_usd"] for row in costs["nli_arm_costs"].values()) == pytest.approx(0.302)


def test_two_identical_unknown_ledgers_are_not_assumed_to_be_copies(tmp_path):
    value = {"attempts": 1, "estimated_or_reserved_usd": 0.2,
             "events": [{"status": "attempted_usage_unknown", "reserved_upper_estimate_usd": 0.2}]}
    first, second = tmp_path / "attempt1.json", tmp_path / "attempt2.json"
    for path in (first, second):
        path.write_text(json.dumps(value))
    costs = report.accounting({"first_run": first, "second_run": second})
    assert costs["totals"]["unknown_usage_attempts"] == 2
    assert costs["totals"]["unknown_reserved_usd"] == pytest.approx(0.4)


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


@pytest.mark.parametrize("include_recovery", [False, True])
def test_english_markdown_preserves_results_costs_limits_and_optional_recovery(include_recovery):
    costs = report.ledger_summary(ledger())
    summary = {"strata": report.aggregate(validated(), draws=20)}
    if include_recovery:
        summary["recovery"] = {
            "prior_ledger": costs, "snapshot_files": 14983,
            "cases_all_five_arm_results_saved": 391, "latency_excluded_arm_count": 6,
            "new_attempts_after_restart": 7,
        }
    text = report.markdown(summary, {"studies": {"formal_v2": costs}, "totals": costs})
    assert not re.search(r"[\u4e00-\u9fff]", text)
    assert "**500 questions**" in text and "**200 questions**" in text
    assert "| jev | 3 | 0 | 1 / 1 |" in text
    assert "jev-minus-luna" in text and "jev-minus-cross_encoder" in text
    assert "| formal_v2 | 3 | 0.102000 | 0.200000 |" in text
    assert "**3 attempts, $0.302000**" in text
    for qualifier in (
        "seed 20261101 and 4000", "113 components", "237 questions", "326 questions",
        "without multiplicity correction", "not invoices", "exempt from the 1024-token",
        "membership accepts an empty list", "end-to-end scoring convention",
        "asymmetric client overhead", "not whole-corpus retrieval or chart correctness",
        "Question text, raw materials and gold answers are not published",
    ):
        assert qualifier in text
    assert "(JEV_LATENCY_CONTROL.md)" in text and "(PASSAGE_SELECTION_RESULTS.md)" in text
    assert ("## Interruption and recovery" in text) is include_recovery
    if include_recovery:
        assert "14983 files" in text and "391 questions" in text
        assert "6 case/arm" in text and "quality denominators and paired analyses remain unchanged" in text
        assert "$0.2000000 in reservations" in text and "Recovery added 7 attempts" in text
        assert "formal_v2 and are not added again" in text
        assert "(public-evaluation/nli4ct-recovery.json)" in text


def reconciliation_fixture(tmp_path):
    from zipfile import ZipFile

    source = tmp_path / 'protocol.source.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('experiments/passage_selection.py',
                         'budget.after(event, metadata)\nmetadata["elapsed_seconds"] = time.monotonic() - started')
    event = {'status': 'completed', 'arm': 'luna', 'case_id': 'abc-123', 'passage_id': 'passage1',
             'estimated_cost_usd': 0.1, 'metadata': {'usage': {'input_tokens': 12}}}
    persisted = {'attempts': 1, 'events': [event], 'estimated_or_reserved_usd': 0.1}
    reported = copy.deepcopy(persisted)
    reported['events'][0]['metadata']['elapsed_seconds'] = 3.7
    proof = tmp_path / 'abc-123' / 'scores' / 'luna' / (report.digest('passage1')[:24] + '.json')
    proof.parent.mkdir(parents=True)
    proof.write_text(json.dumps({'metadata': reported['events'][0]['metadata']}))
    return reported, persisted, source, proof


def test_exact_ledger_and_saved_timing_reconciliation_preserve_originals(tmp_path):
    reported, persisted, source, proof = reconciliation_fixture(tmp_path)
    before = copy.deepcopy((reported, persisted))
    exact = report.reconcile_final_ledger(persisted, persisted, tmp_path, source)
    assert not exact['timing_only_discrepancies']
    receipt = report.reconcile_final_ledger(reported, persisted, tmp_path, source)
    assert (reported, persisted) == before
    assert receipt['reported_budget_sha256'] != receipt['persisted_ledger_sha256']
    assert receipt['timing_only_discrepancies'][0]['value'] == 3.7
    assert receipt['timing_only_discrepancies'][0]['score_file_sha256']


@pytest.mark.parametrize('mutation', [
    lambda x: x['events'][0]['metadata']['usage'].update(input_tokens=13),
    lambda x: x['events'][0].update(estimated_cost_usd=0.2),
    lambda x: x['events'][0].update(status='attempted_usage_unknown'),
    lambda x: x['events'][0]['metadata'].update(extra='unknown'),
    lambda x: x.update(attempts=2),
    lambda x: x['events'][0].update(arm='full'),
    lambda x: x['events'][0].update(stage='answer'),
])
def test_reconciliation_rejects_every_non_timing_change(tmp_path, mutation):
    reported, persisted, source, proof = reconciliation_fixture(tmp_path)
    mutation(reported)
    with pytest.raises(ValueError, match='beyond elapsed'):
        report.reconcile_final_ledger(reported, persisted, tmp_path, source)


@pytest.mark.parametrize('elapsed', [-1, float('nan'), float('inf'), True, '3.7', None])
def test_reconciliation_rejects_invalid_elapsed(tmp_path, elapsed):
    reported, persisted, source, proof = reconciliation_fixture(tmp_path)
    reported['events'][0]['metadata']['elapsed_seconds'] = elapsed
    with pytest.raises(ValueError, match='beyond elapsed'):
        report.reconcile_final_ledger(reported, persisted, tmp_path, source)


@pytest.mark.parametrize('proof_change', ['missing', 'different', 'existing_elapsed'])
def test_reconciliation_requires_exact_independent_saved_proof(tmp_path, proof_change):
    reported, persisted, source, proof = reconciliation_fixture(tmp_path)
    if proof_change == 'missing':
        proof.unlink()
    elif proof_change == 'different':
        proof.write_text(json.dumps({'metadata': {'elapsed_seconds': 3.8}}))
    else:
        persisted['events'][0]['metadata']['elapsed_seconds'] = 3.6
    with pytest.raises(ValueError):
        report.reconcile_final_ledger(reported, persisted, tmp_path, source)


def test_recovery_common_case_latency_is_descriptive_with_explicit_samples():
    costs = report.ledger_summary(ledger())
    summary = {'strata': report.aggregate(validated(), draws=20),
               'recovery_common_case_latency': {'retained_cases': 497, 'excluded_cases': 3,
                 'arms': {'jev': {'successful_samples': 497, 'p50_seconds': 51.453, 'p95_seconds': 161.644},
                          'full': {'successful_samples': 497, 'p50_seconds': 0, 'p95_seconds': 0}}},
               'final_ledger_reconciliation': {'timing_only_discrepancies': [{}]}}
    text = report.markdown(summary, {'studies': {'formal_v2': costs}, 'totals': costs})
    assert '| jev | 497 | 51.453 | 161.644 |' in text
    assert 'same 497 questions' in text and '3 questions excluded only' in text
    assert 'zero does not mean zero answer latency' in text
    assert '1 ledger event(s)' in text and 'Neither original was modified' in text
    assert 'nli4ct-independent-audit.json' in text
    assert 'local official dataset and saved study outputs' in text
