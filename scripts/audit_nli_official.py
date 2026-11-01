"""Independent, closed-only NLI audit against the pinned original official ZIP.

No product adapter, scorer, runner or reporting helper is imported. Public output
contains checksums, counts and discrepancy codes, never passages or answer keys.
"""

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

ARCHIVE_SHA256 = "dd972c46e69a5bc5e5fb74c2105b5444722d81879ab96b214545b943e4e7ebbe"
ARMS = ("hybrid", "cross_encoder", "jev", "luna", "full")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_closed(study, exit_receipt, producer_confirmed_stopped):
    # These checks precede opening any source labels, input text or predictions.
    if not producer_confirmed_stopped:
        raise ValueError("Producer stop confirmation is required")
    if not (study / "report.json").exists() or not exit_receipt.exists():
        raise ValueError("Formal report and completion receipt are required")
    if read(exit_receipt).get("status") != "completed":
        raise ValueError("Producer completion receipt is not successful")


def official_cases(archive_path, expected_hash=ARCHIVE_SHA256):
    if file_hash(archive_path) != expected_hash:
        raise ValueError("Pinned official archive checksum mismatch")
    result = {}
    with ZipFile(archive_path) as archive:
        questions = json.loads(archive.read("Complete_dataset/Gold_test.json"))
        for identity, question in questions.items():
            section = question["Section_id"]
            fragments, gold = [], set()
            for role in ("Primary", "Secondary"):
                trial = question.get(role + "_id")
                if trial is None:
                    continue
                record = json.loads(archive.read("Complete_dataset/CT json/" + trial + ".json"))
                sentences = record[section]
                indices = question[role + "_evidence_index"]
                if any(type(i) is not int or not 0 <= i < len(sentences) for i in indices):
                    raise ValueError("Official evidence index is outside its source section")
                for number, sentence in enumerate(sentences):
                    key = trial + ":" + section + ":" + str(number)
                    fragments.append(
                        {
                            "id": key,
                            "study": trial,
                            "section": section,
                            "text": sentence,
                            "role": role,
                            "sentence_index": number,
                        }
                    )
                gold.update(trial + ":" + section + ":" + str(i) for i in indices)
            if len({p["id"] for p in fragments}) != len(fragments):
                raise ValueError("Official case contains duplicate source identifiers")
            result[identity] = {
                "input": {
                    "id": identity,
                    "split": "test",
                    "statement": question["Statement"],
                    "type": question["Type"],
                    "section": section,
                    "passages": fragments,
                },
                "gold": gold,
                "label": question["Label"],
            }
    return result


def independent_counts(selected, required):
    found = sum(identity in selected for identity in required)
    return {
        "recall": found / len(required) if required else 1.0,
        "precision": found / len(selected) if selected else (0.0 if required else 1.0),
        "complete": found == len(required),
    }


def audit_population(
    official, inputs, protocol, report, pool_loader, arm_loader, *, expected_cases=500, expected_answers=200
):
    params = protocol["identity"]["parameters"]
    order = sorted(official, key=lambda key: digest(["nli4ct-evidence-v2", key]))
    answer_ids = set(order[:expected_answers])
    if (
        len(official) != expected_cases
        or params["ids"] != order
        or len(params["answer_ids"]) != expected_answers
        or set(params["answer_ids"]) != answer_ids
        or params["arms"] != list(ARMS)
        or params["candidate_limit"] != 24
        or params["context_tokens"] != 1024
    ):
        raise ValueError("Frozen population, arms or budgets differ from the full official study")
    if digest(protocol["identity"]) != protocol["sha256"]:
        raise ValueError("Protocol identity checksum mismatch")
    by_input = {r["id"]: r for r in inputs}
    if len(inputs) != expected_cases or set(by_input) != set(order):
        raise ValueError("Prepared input population differs from the official archive")
    if digest([by_input[key] for key in order]) != params["dataset"]:
        raise ValueError("Prepared inputs differ from the frozen dataset identity")
    indexed = {}
    for row in report["cases"]:
        pair = row["id"], row["arm"]
        if pair in indexed or pair[0] not in official or pair[1] not in ARMS:
            raise ValueError("Duplicate or foreign reported case/arm")
        indexed[pair] = row
    if len(indexed) != expected_cases * len(ARMS):
        raise ValueError("Formal report omits a planned case/arm")
    if set(report["summary"]) != set(ARMS):
        raise ValueError("Reported aggregate arm population mismatch")
    differences = []
    counters = {arm: Counter() for arm in ARMS}

    def check(condition, code, key=None, arm=None):
        if not condition:
            item = {"check": code}
            if key is not None:
                item["case_id_sha256"] = hashlib.sha256(key.encode()).hexdigest()
            if arm:
                item["arm"] = arm
            differences.append(item)

    for key in order:
        case = official[key]
        check(by_input[key] == case["input"], "raw_source_input_mapping", key)
        source_order = [p["id"] for p in case["input"]["passages"]]
        source_set = set(source_order)
        pool = pool_loader(key)
        for method in ("bm25", "dense", "hybrid"):
            ids = pool.get(method)
            check(
                isinstance(ids, list)
                and len(ids) == len(set(ids))
                and len(ids) <= 24
                and set(ids) <= source_set,
                "candidate_pool_source_membership",
                key,
                method,
            )
        shared_pool = pool.get("hybrid", [])
        for arm in ARMS:
            claimed = indexed[key, arm]
            saved, failure = arm_loader(key, arm)
            counter = counters[arm]
            counter["planned"] += 1
            counter["answer_planned"] += key in answer_ids
            completed = saved is not None
            check(
                claimed.get("status") == ("completed" if completed else "failed"), "terminal_status", key, arm
            )
            metrics = {"recall": 0.0, "precision": 0.0, "complete": False}
            answer_correct = False
            if not completed:
                check(
                    failure is not None
                    and failure.get("id") == key
                    and failure.get("arm") == arm
                    and failure.get("status") == "failed",
                    "missing_failure_record",
                    key,
                    arm,
                )
                counter["failed"] += 1
                counter["answer_failed"] += key in answer_ids
            else:
                counter["completed"] += 1
                check(
                    saved.get("id") == key and saved.get("arm") == arm and saved.get("status") == "completed",
                    "saved_arm_identity",
                    key,
                    arm,
                )
                for field, value in saved.items():
                    allowed = {
                        "id",
                        "arm",
                        "status",
                        "selected_ids",
                        "ranking",
                        "context_tokens",
                        "ranking_seconds",
                        "answer",
                    }
                    check(
                        field in allowed and claimed.get(field) == value,
                        "saved_report_field_" + field if field in allowed else "unexpected_saved_field",
                        key,
                        arm,
                    )
                selected, ranking = saved["selected_ids"], saved["ranking"]
                check(
                    len(ranking) == len(set(ranking)) and set(ranking) <= source_set,
                    "ranking_source_membership",
                    key,
                    arm,
                )
                check(
                    len(selected) == len(set(selected)) and set(selected) <= set(ranking),
                    "selected_ranking_membership",
                    key,
                    arm,
                )
                if arm == "full":
                    check(
                        ranking == source_order and selected == source_order, "full_source_complete", key, arm
                    )
                else:
                    check(
                        len(ranking) == len(shared_pool) and set(ranking) == set(shared_pool),
                        "identical_candidate_pool",
                        key,
                        arm,
                    )
                    check(
                        [pid for pid in ranking if pid in set(selected)] == selected,
                        "selected_order",
                        key,
                        arm,
                    )
                    if arm == "hybrid":
                        check(ranking == shared_pool, "hybrid_order", key, arm)
                metrics = independent_counts(set(selected), case["gold"])
                if key in answer_ids:
                    answer = saved.get("answer")
                    check(isinstance(answer, dict), "planned_answer_missing", key, arm)
                    if isinstance(answer, dict):
                        check(
                            answer.get("label") in {"Entailment", "Contradiction", "Insufficient evidence"},
                            "answer_label_schema",
                            key,
                            arm,
                        )
                        answer_correct = answer.get("label") == case["label"]
                        citations = answer.get("evidence_ids", [])
                        valid = isinstance(citations, list) and set(citations) <= set(selected)
                        check(answer.get("citation_ids_valid") is valid, "answer_citation_flag", key, arm)
                        counter["valid_citation_ids"] += valid
                else:
                    check(saved.get("answer") is None, "unplanned_answer", key, arm)
            for field, computed in metrics.items():
                reported = claimed.get(field)
                equal = (
                    reported is computed
                    if isinstance(computed, bool)
                    else (type(reported) in (float, int) and math.isclose(reported, computed, abs_tol=1e-12))
                )
                check(equal, "official_metric_" + field, key, arm)
            check(
                claimed.get("answer_correct")
                is (answer_correct if completed and key in answer_ids else None),
                "official_answer_correct",
                key,
                arm,
            )
            counter["evidence_recall_sum"] += metrics["recall"]
            counter["evidence_precision_sum"] += metrics["precision"]
            counter["complete_evidence"] += metrics["complete"]
            counter["answer_correct"] += answer_correct
    summaries = {}
    for arm, count in counters.items():
        summary = {
            name: count[name]
            for name in (
                "planned",
                "answer_planned",
                "completed",
                "failed",
                "answer_failed",
                "complete_evidence",
                "answer_correct",
                "valid_citation_ids",
            )
        }
        summary["evidence_recall"] = count["evidence_recall_sum"] / expected_cases
        summary["evidence_precision"] = count["evidence_precision_sum"] / expected_cases
        fields = {
            "planned",
            "answer_planned",
            "completed",
            "evidence_recall",
            "evidence_precision",
            "complete_evidence",
            "answer_correct",
        }
        check(set(report["summary"][arm]) == fields, "aggregate_fields", arm=arm)
        for field in sorted(fields):
            value = report["summary"][arm].get(field)
            check(
                type(value) in (float, int) and math.isclose(value, summary[field], abs_tol=1e-12),
                "aggregate_" + field,
                arm=arm,
            )
        summaries[arm] = summary
    return {
        "verified": not differences,
        "planned_cases": expected_cases,
        "planned_answer_cases": expected_answers,
        "audited_case_arm_rows": len(indexed),
        "official_source_passage_occurrences": sum(len(v["input"]["passages"]) for v in official.values()),
        "summary": summaries,
        "difference_count": len(differences),
        "differences": differences,
    }


def audit(study, archive_path, inputs_path, exit_receipt, *, producer_confirmed_stopped=False):
    require_closed(study, exit_receipt, producer_confirmed_stopped)
    official = official_cases(archive_path)

    def arm_loader(key, arm):
        complete, failed = study / key / (arm + ".json"), study / key / (arm + "-failure.json")
        return (read(complete) if complete.exists() else None, read(failed) if failed.exists() else None)

    result = audit_population(
        official,
        read(inputs_path),
        read(study / "protocol.json"),
        read(study / "report.json"),
        lambda key: read(study / key / "candidate-pool.json"),
        arm_loader,
    )
    result.update(
        version="independent-official-nli-audit-v1",
        source_archive_sha256=file_hash(archive_path),
        prepared_inputs_sha256=file_hash(inputs_path),
        final_report_sha256=file_hash(study / "report.json"),
        protocol_file_sha256=file_hash(study / "protocol.json"),
        completion_receipt_sha256=file_hash(exit_receipt),
        auditor_sha256=file_hash(__file__),
        independence="Standard-library reconstruction of official source sections and evidence indices; no product adapters, scorers or report helpers imported.",
        scope="All official test cases and five saved arms, same frozen answer subset; failed cases retain the planned denominator. Does not independently reproduce selection token lengths, model/reranker inference or costs.",
        privacy="Only hashes, counts and discrepancy codes are published; no statements, passages, predictions or answer keys.",
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--completion-receipt", type=Path, required=True)
    parser.add_argument("--producer-confirmed-stopped", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path("docs/public-evaluation/nli4ct-independent-audit.json")
    )
    args = parser.parse_args()
    result = audit(
        args.study,
        args.archive,
        args.inputs,
        args.completion_receipt,
        producer_confirmed_stopped=args.producer_confirmed_stopped,
    )
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("verified", "planned_cases", "audited_case_arm_rows", "difference_count")
            }
        )
    )
    if not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
