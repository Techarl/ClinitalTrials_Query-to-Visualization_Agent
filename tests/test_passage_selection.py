import json

import pytest

from app.v2.evidence_selection import eligibility_passages, pack_context
from experiments.passage_selection import metrics


def sample():
    text = "Inclusion Criteria:\n\n* At least 18 years.\n\nExclusion Criteria:\n\n* Pregnant participants.\n* Prior therapy within 4 weeks."
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Example"},
            "eligibilityModule": {"eligibilityCriteria": text},
        }
    }
    return text, eligibility_passages(study, "frozen.json", "a" * 64)


def test_passage_offsets_and_exclusion_headers_are_preserved():
    text, passages = sample()
    assert len(passages) == 3
    assert [p.section for p in passages] == ["inclusion", "exclusion", "exclusion"]
    for passage in passages:
        assert text[passage.start : passage.end] == passage.text
        assert passage.field.endswith("/eligibilityCriteria")


def test_context_budget_applies_to_serialized_full_passages_without_mutation():
    _, passages = sample()
    before = [p.model_dump() for p in passages]
    cap = len(json.dumps([passages[1].context()], ensure_ascii=False))
    packed = pack_context(passages, [passages[1].id, passages[0].id, passages[2].id], cap, len)
    assert [p["id"] for p in packed["passages"]] == [passages[1].id]
    assert packed["tokens"] <= cap
    assert [p.model_dump() for p in passages] == before
    assert packed["purpose"] == "explanation_only_not_statistical_population"
    with pytest.raises(ValueError, match="unknown"):
        pack_context(passages, ["invented"], cap, len)


def test_context_cap_cannot_be_smaller_than_empty_serialization():
    with pytest.raises(ValueError, match="empty serialized"):
        pack_context([], [], 1, len)
    assert pack_context([], [], 2, len)["tokens"] == 2


def test_missing_or_fabricated_evidence_cannot_pass_extractive_oracle():
    _, passages = sample()
    case = {"requirements": [{"passage_ids": [p.id]} for p in passages[:2]], "counterevidence": True}
    packed = pack_context(passages, [p.id for p in passages], 10000, len)
    good = {
        "answer": {
            "findings": [
                {"passage_id": p.id, "quotation": p.text, "section": p.section} for p in passages[:2]
            ]
        }
    }
    assert metrics(case, passages, [p.id for p in passages], packed, good)["answer_complete_and_relevant"]
    good["answer"]["findings"].pop()
    assert not metrics(case, passages, [p.id for p in passages], packed, good)["answer_complete_and_relevant"]
    good["answer"]["findings"][0]["quotation"] = "Invented text"
    assert not metrics(case, passages, [p.id for p in passages], packed, good)["citations_exact"]


def test_frozen_annotations_resolve_and_disease_groups_do_not_cross_split():
    from pathlib import Path

    data = json.loads(Path("evaluation/passage_selection.json").read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in data["passages"]}
    studies = {"development": set(), "heldout": set()}
    for case in data["cases"]:
        for requirement in case["requirements"]:
            studies[case["split"]].add(requirement["study"])
            for identity in requirement["passage_ids"]:
                p = by_id[identity]
                assert p["nct_id"] == requirement["study"]
                assert any(a.casefold() in p["text"].casefold() for a in requirement["anchors"])
    assert not studies["development"] & studies["heldout"]


def test_every_frozen_passage_matches_original_snapshot_bytes_and_span():
    import hashlib
    from pathlib import Path

    data = json.loads(Path("evaluation/passage_selection.json").read_text(encoding="utf-8"))
    sources = {}
    for row in data["passages"]:
        if row["source_path"] not in sources:
            path = Path(row["source_path"])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row["source_sha256"]
            sources[row["source_path"]] = {
                s["protocolSection"]["identificationModule"]["nctId"]: s
                for s in json.loads(path.read_text(encoding="utf-8"))
            }
        raw = sources[row["source_path"]][row["nct_id"]]["protocolSection"]["eligibilityModule"][
            "eligibilityCriteria"
        ]
        assert raw[row["start"] : row["end"]] == row["text"]
