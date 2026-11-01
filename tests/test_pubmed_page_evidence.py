"""PubMed page evidence must preserve offsets, identity, and source coverage."""

import json
from types import SimpleNamespace

import pytest

import evaluation.scorers as scorer


@pytest.fixture
def pubmed():
    return scorer


def observations(body, cut=10):
    pointer = "/articles/0/abstract/0/text"
    return [
        {"tool": "fetch_articles", "status": "ok", "result_id": "article-result", "source_handle": "source-A"},
        {"tool": "read_source", "status": "ok", "source_handle": "source-A", "pointer": pointer,
         "source_hash": "hash-A", "offset": 0, "text": body[:cut]},
        {"tool": "read_source", "status": "ok", "source_handle": "source-A", "pointer": pointer,
         "source_hash": "hash-A", "offset": cut, "text": body[cut:]},
    ]


def artifact(body):
    return SimpleNamespace(result_id="article-result", metadata={"articles": [{"abstract": [{"text": body}]}]})


def test_pubmed_adjacent_pages_are_reconstructed_without_space(pubmed):
    body = "Cross-page abstract text"
    rows = observations(body)
    rows[1:] = reversed(rows[1:])
    assert pubmed.literature_body_read([artifact(body)], rows)
    assert pubmed.read_source_segments(rows)[0]["text"] == body


@pytest.mark.parametrize("mutation", ["gap", "other_handle", "other_pointer", "other_article", "other_hash", "missing_offset"])
def test_pubmed_missing_or_unrelated_fragments_do_not_complete_a_read(pubmed, mutation):
    body = "Cross-page abstract text"
    rows = observations(body)
    if mutation == "gap":
        rows[2]["offset"] += 1
    elif mutation == "other_handle":
        rows[2]["source_handle"] = "source-B"
    elif mutation == "other_pointer":
        rows[2]["pointer"] = "/articles/0/title"
    elif mutation == "other_article":
        rows[2]["pointer"] = "/articles/1/abstract/0/text"
    elif mutation == "other_hash":
        rows[2]["source_hash"] = "hash-B"
    else:
        rows[2].pop("offset")
    assert not pubmed.literature_body_read([artifact(body)], rows)


def test_pubmed_exact_overlap_is_safe_but_conflicts_are_not(pubmed):
    body = "Cross-page abstract text"
    rows = observations(body)
    rows[2].update(offset=5, text=body[5:])
    rows.append(dict(rows[1]))  # A replayed page must not duplicate characters.
    assert pubmed.literature_body_read([artifact(body)], rows)
    rows[-1]["text"] = "conflicting text"
    assert not pubmed.literature_body_read([artifact(body)], rows)


def test_pubmed_unrelated_field_with_identical_text_does_not_count(pubmed):
    body = "Cross-page abstract text"
    rows = observations(body)
    rows[1].update(pointer="/articles/0/title", text=body)
    del rows[2]
    assert not pubmed.literature_body_read([artifact(body)], rows)
    rows[1]["pointer"] = "/articles/0/abstract/0/text"
    rows[0]["result_id"] = "unrelated-artifact"
    assert not pubmed.literature_body_read([artifact(body)], rows)


def test_pubmed_serialized_ancestor_pages_preserve_escaping(pubmed):
    body = 'Quoted "sentence"\nand another line.'
    text = json.dumps({"articles": [{"abstract": [{"text": body}]}]}, ensure_ascii=False, sort_keys=True)
    rows = observations(text, cut=text.index("sentence") + 3)
    for row in rows[1:]:
        row["pointer"] = ""
    assert pubmed.literature_body_read([artifact(body)], rows)
