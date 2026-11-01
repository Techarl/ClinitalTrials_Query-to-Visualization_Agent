import hashlib
from copy import deepcopy
from zipfile import ZipFile

import pytest

from scripts.report_public_nli import digest
from scripts.report_trialqa import (
    build_report,
    paired_statistics,
    quotation_integrity,
    summarize,
    validated_rows,
    verify_archive,
)


def envelope():
    splits = {
        "a": {"group": "one", "split": "heldout"},
        "b": {"group": "one", "split": "heldout"},
        "c": {"group": "two", "split": "calibration"},
    }
    identity = {"parameters": {"planned_ids": list(splits), "split_manifest": splits}, "code": {}}
    protocol = {"identity": identity, "sha256": digest(identity)}
    cases = []
    for key in splits:
        for arm in ("given_sources", "frozen_retrieval"):
            cases.append(
                {
                    "id": key,
                    "arm": arm,
                    "status": "completed",
                    "quote_integrity": True,
                    "judgment": {"correct": arm == "frozen_retrieval", "citations_support_answer": True},
                }
            )
    report = {
        "protocol_hash": protocol["sha256"],
        "planned_questions": 3,
        "cases": cases,
        "summary": {
            "given_sources": {"correct": 0, "cited_correct": 0},
            "frozen_retrieval": {"correct": 3, "cited_correct": 3},
        },
    }
    return report, protocol, splits


def test_split_cited_accuracy_keeps_uncited_correct_and_missing_judges_separate():
    report, protocol, splits = envelope()
    report["cases"][1]["quote_integrity"] = False
    report["cases"][3].pop("judgment")
    report["cases"][3]["judge_error"] = "APIConnectionError"
    report["summary"]["frozen_retrieval"] = {"correct": 2, "cited_correct": 1}
    rows = validated_rows(report, protocol, splits, expected_questions=3)
    result = summarize(rows, draws=100)
    assert result["heldout"]["arms"]["frozen_retrieval"]["denominator"] == 2
    assert result["heldout"]["arms"]["frozen_retrieval"]["correct"] == 1
    assert result["heldout"]["arms"]["frozen_retrieval"]["cited_correct"] == 0
    assert result["heldout"]["arms"]["frozen_retrieval"]["judge_missing"] == 1
    assert result["all"]["arms"]["frozen_retrieval"]["cited_correct"] == 1


def test_paired_sign_flip_uses_trial_groups_instead_of_independent_questions():
    report, protocol, splits = envelope()
    rows = validated_rows(report, protocol, splits, expected_questions=3)
    stats = paired_statistics(rows, draws=100)
    assert stats["groups"] == 2 and stats["questions"] == 3
    assert stats["difference"] == 1
    assert stats["cluster_bootstrap95"] == [1, 1]
    assert stats["sign_flip"]["assignments"] == 4
    assert stats["sign_flip"]["two_sided_p"] == 0.5  # Three independent questions would give .25.
    assert stats == paired_statistics(rows, draws=100)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "wrong_total", "cross_split"])
def test_reject_incomplete_or_inconsistent_reports(mutation):
    report, protocol, splits = deepcopy(envelope())
    if mutation == "missing":
        report["cases"].pop()
    elif mutation == "duplicate":
        report["cases"].append(report["cases"][0])
    elif mutation == "unknown":
        report["cases"][0]["id"] = "unplanned"
    elif mutation == "wrong_total":
        report["summary"]["given_sources"]["cited_correct"] = 1
    else:
        splits["a"]["split"] = "development"
        protocol["sha256"] = digest(protocol["identity"])
        report["protocol_hash"] = protocol["sha256"]
    with pytest.raises(ValueError):
        validated_rows(report, protocol, splits, expected_questions=3)


def test_archive_verifies_immutable_bytes_not_current_workspace(tmp_path):
    contents = b"frozen code"
    protocol = {"identity": {"code": {"nonexistent_workspace_file.py": hashlib.sha256(contents).hexdigest()}}}
    protocol["sha256"] = digest(protocol["identity"])
    with ZipFile(tmp_path / "protocol.source.zip", "w") as archive:
        archive.writestr("nonexistent_workspace_file.py", contents)
    result = verify_archive(tmp_path, protocol)
    assert result["archive_matches_frozen_manifest"]
    assert result["current_workspace_differs"] == ["nonexistent_workspace_file.py"]
    with ZipFile(tmp_path / "protocol.source.zip", "w") as archive:
        archive.writestr("nonexistent_workspace_file.py", b"changed")
    with pytest.raises(ValueError, match="archive hash mismatch"):
        verify_archive(tmp_path, protocol)


def test_reporter_refuses_unfinished_producer(tmp_path):
    with pytest.raises(ValueError, match="refusing partial scoring"):
        build_report(tmp_path)


def test_quote_audit_traverses_escaped_pointer_and_rejects_wrong_trial():
    records = {"NCT1": {"a/b": [{"~field": "Required four prescriptions"}]}, "NCT2": {"a/b": []}}
    finding = {"nct_id": "NCT1", "pointer": "/a~1b/0/~0field", "quotation": "four prescriptions"}
    assert quotation_integrity([finding], records)
    assert not quotation_integrity([{**finding, "nct_id": "NCT2"}], records)
    assert not quotation_integrity([{**finding, "quotation": "three prescriptions"}], records)
    assert not quotation_integrity([], records)
