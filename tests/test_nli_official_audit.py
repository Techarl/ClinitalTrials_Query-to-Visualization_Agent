import copy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

import scripts.audit_nli_official as audit_module
from scripts.audit_nli_official import (
    ARMS,
    audit,
    audit_population,
    digest,
    file_hash,
    official_cases,
)


def fixture(tmp_path):
    archive = tmp_path / "official.zip"
    questions = {
        "alpha": {
            "Primary_id": "NCT00000001",
            "Section_id": "Eligibility",
            "Primary_evidence_index": [1],
            "Statement": "Synthetic alpha claim",
            "Type": "Single",
            "Label": "Entailment",
        },
        "beta": {
            "Primary_id": "NCT00000001",
            "Secondary_id": "NCT00000002",
            "Section_id": "Eligibility",
            "Primary_evidence_index": [1],
            "Secondary_evidence_index": [0],
            "Statement": "Synthetic beta claim",
            "Type": "Comparison",
            "Label": "Contradiction",
        },
    }
    with ZipFile(archive, "w") as z:
        z.writestr("Complete_dataset/Gold_test.json", json.dumps(questions))
        z.writestr(
            "Complete_dataset/CT json/NCT00000001.json",
            json.dumps({"Eligibility": ["synthetic irrelevant", "synthetic required"]}),
        )
        z.writestr(
            "Complete_dataset/CT json/NCT00000002.json", json.dumps({"Eligibility": ["synthetic opposing"]})
        )
    official = official_cases(archive, file_hash(archive))
    inputs = [v["input"] for v in official.values()]
    order = sorted(official, key=lambda key: digest(["nli4ct-evidence-v2", key]))
    identity = {
        "parameters": {
            "ids": order,
            "answer_ids": [order[0]],
            "arms": list(ARMS),
            "candidate_limit": 24,
            "context_tokens": 1024,
            "dataset": digest([official[key]["input"] for key in order]),
        }
    }
    protocol = {"identity": identity, "sha256": digest(identity)}
    pools, saved, reported = {}, {}, []
    for key in order:
        ids = [p["id"] for p in official[key]["input"]["passages"]]
        pools[key] = {name: list(ids) for name in ("bm25", "dense", "hybrid")}
        for arm in ARMS:
            if arm == "luna" and key == order[0]:
                failed = {"id": key, "arm": arm, "status": "failed"}
                saved[key, arm] = None, failed
                reported.append({**failed, "recall": 0, "precision": 0, "complete": False})
                continue
            answer = (
                {"label": questions[key]["Label"], "evidence_ids": list(ids), "citation_ids_valid": True}
                if key == order[0]
                else None
            )
            row = {
                "id": key,
                "arm": arm,
                "status": "completed",
                "ranking": list(ids),
                "selected_ids": list(ids),
                "answer": answer,
            }
            saved[key, arm] = copy.deepcopy(row), None
            precision = 0.5 if key == "alpha" else 2 / 3
            reported.append(
                {
                    **copy.deepcopy(row),
                    "recall": 1.0,
                    "precision": precision,
                    "complete": True,
                    "answer_correct": True if answer else None,
                }
            )
    summary = {}
    for arm in ARMS:
        cells = [r for r in reported if r["arm"] == arm]
        summary[arm] = {
            "planned": 2,
            "answer_planned": 1,
            "completed": sum(r["status"] == "completed" for r in cells),
            "evidence_recall": sum(r["recall"] for r in cells) / 2,
            "evidence_precision": sum(r["precision"] for r in cells) / 2,
            "complete_evidence": sum(r["complete"] for r in cells),
            "answer_correct": sum(r.get("answer_correct") is True for r in cells),
        }
    return official, inputs, protocol, {"cases": reported, "summary": summary}, pools, saved


def execute(data):
    official, inputs, protocol, report, pools, saved = data
    return audit_population(
        official,
        inputs,
        protocol,
        report,
        pools.__getitem__,
        lambda key, arm: saved[key, arm],
        expected_cases=2,
        expected_answers=1,
    )


def test_original_zip_mapping_and_failed_answer_denominators(tmp_path):
    data = fixture(tmp_path)
    assert data[0]["beta"]["gold"] == {"NCT00000001:Eligibility:1", "NCT00000002:Eligibility:0"}
    assert data[0]["alpha"]["input"]["passages"][1]["text"] == "synthetic required"
    result = execute(data)
    assert result["verified"]
    assert result["audited_case_arm_rows"] == 10
    assert result["summary"]["luna"]["answer_planned"] == 1
    assert result["summary"]["luna"]["answer_correct"] == 0
    assert result["summary"]["luna"]["answer_failed"] == 1
    assert "synthetic" not in json.dumps(result)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("prepared_text", "raw_source_input_mapping"),
        ("evidence", "official_metric_recall"),
        ("label", "official_answer_correct"),
        ("foreign_pool", "candidate_pool_source_membership"),
        ("report_answer", "saved_report_field_answer"),
        ("denominator", "aggregate_planned"),
        ("missing_aggregate", "aggregate_fields"),
    ],
)
def test_coordinated_mutations_cannot_hide_behind_runner_claims(tmp_path, mutation, expected):
    data = fixture(tmp_path)
    official, inputs, protocol, report, pools, saved = data
    key = protocol["identity"]["parameters"]["answer_ids"][0]
    row = next(r for r in report["cases"] if r["id"] == key and r["arm"] == "jev")
    recorded = saved[key, "jev"][0]
    if mutation == "prepared_text":
        inputs[0]["passages"][0]["text"] = "silently changed source"
        # Break the fixture alias: the independent official data remains original.
        official = official_cases(tmp_path / "official.zip", file_hash(tmp_path / "official.zip"))
        data = (official, inputs, protocol, report, pools, saved)
        by_id = {r["id"]: r for r in inputs}
        protocol["identity"]["parameters"]["dataset"] = digest(
            [by_id[k] for k in protocol["identity"]["parameters"]["ids"]]
        )
        protocol["sha256"] = digest(protocol["identity"])
    elif mutation == "evidence":
        missing = next(iter(official[key]["gold"]))
        recorded["selected_ids"].remove(missing)
        row["selected_ids"].remove(missing)
    elif mutation == "label":
        recorded["answer"]["label"] = row["answer"]["label"] = "Insufficient evidence"
    elif mutation == "foreign_pool":
        pools[key]["hybrid"].append("foreign-source")
    elif mutation == "report_answer":
        row["answer"]["label"] = "Insufficient evidence"
    elif mutation == "missing_aggregate":
        report["summary"]["luna"].pop("answer_correct")
    else:
        report["summary"]["luna"]["planned"] = 1
    result = execute(data)
    assert not result["verified"]
    assert expected in {d["check"] for d in result["differences"]}


def test_dropped_case_arm_is_not_silently_removed_from_denominator(tmp_path):
    data = fixture(tmp_path)
    data[3]["cases"].pop()
    with pytest.raises(ValueError, match="omits"):
        execute(data)


def test_no_source_or_prediction_read_before_closed_confirmation(tmp_path):
    (tmp_path / "report.json").write_text("invalid outcomes must stay unread")
    exit_receipt = tmp_path / "exit.json"
    exit_receipt.write_text(json.dumps({"status": "running"}))
    with pytest.raises(ValueError, match="confirmation"):
        audit(tmp_path, Path("absent gold"), Path("absent input"), exit_receipt)
    with pytest.raises(ValueError, match="not successful"):
        audit(
            tmp_path, Path("absent gold"), Path("absent input"), exit_receipt, producer_confirmed_stopped=True
        )


def test_official_zip_pin_cannot_be_bypassed_by_modified_gold(tmp_path):
    fixture(tmp_path)
    with pytest.raises(ValueError, match="checksum"):
        official_cases(tmp_path / "official.zip")


def test_failed_audit_writes_receipt_and_exits_nonzero(tmp_path, monkeypatch):
    output = tmp_path / "failed.json"
    result = {
        "verified": False,
        "planned_cases": 500,
        "audited_case_arm_rows": 2500,
        "difference_count": 1,
        "differences": [{"check": "official_metric_recall"}],
    }
    monkeypatch.setattr(audit_module, "audit", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit",
            "--study",
            "unused",
            "--archive",
            "unused",
            "--inputs",
            "unused",
            "--completion-receipt",
            "unused",
            "--producer-confirmed-stopped",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as raised:
        audit_module.main()
    assert raised.value.code == 1
    assert json.loads(output.read_text()) == result
