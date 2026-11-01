"""Verify non-record artifacts against immutable upstream receipts, never by label alone."""

import json
from xml.etree import ElementTree as ET

from app.analysis import resolve_pointer
from app.reference import expected_network
from app.sources import content_hash


def ranking_from_records(records, anchor, limit):
    edges, _, entities, _ = expected_network(
        {"relationship": "sponsor_shared_drug"}, {"root": records}, 10**9
    )
    matches = []
    for pair, edge in edges:
        left, right = pair
        other = (
            right
            if entities[left][0].casefold() == anchor.casefold()
            else left
            if entities[right][0].casefold() == anchor.casefold()
            else None
        )
        if other:
            matches.append(
                {
                    "id": other,
                    "name": entities[other][0],
                    "shared_intervention_count": edge["weight"],
                    "contributors": sorted(edge["ids"]),
                }
            )
    return sorted(matches, key=lambda row: (-row["shared_intervention_count"], row["id"]))[:limit]


def verify_receipts(registry, artifact, state):
    if artifact.coverage.statistics_basis == "official_counts":
        receipts = artifact.metadata["receipts"]
        rows = artifact.visualization["data"]
        root = receipts[0]["raw"]["totalCount"]
        if (
            len(rows) != len(receipts) - 1
            or sum(row["trial_count"] for row in rows) != root
            or artifact.coverage.matched_count != root
        ):
            raise ValueError("Official count bucket coverage mismatch")
        if (
            artifact.coverage.evidence_coverage != "sample_only"
            or artifact.coverage.population_coverage != "count_only"
        ):
            raise ValueError("Official count evidence must remain sample-only")
        for row, reference in zip(rows, receipts[1:], strict=True):
            receipt = registry.store.get(reference["receipt_id"], state["session_id"], "count")
            raw = receipt["raw"]
            if (
                row["category"] != reference["category"]
                or row["trial_count"] != raw["totalCount"]
                or row["count_receipt_id"] != reference["receipt_id"]
            ):
                raise ValueError("Official count differs from its source receipt")
            source = {
                r["protocolSection"]["identificationModule"]["nctId"]: r for r in raw.get("studies", [])
            }
            page = registry.store.base.evidence_page(row["evidence_set_id"], 0, 1000)
            if (
                page is None
                or row["sample_count"] != len(source)
                or {c["nct_id"] for c in page["citations"]} != set(source)
            ):
                raise ValueError("Official count sample receipt mismatch")
            for citation in page["citations"]:
                if citation["dataset_id"] != reference["receipt_id"] or any(
                    resolve_pointer(source[citation["nct_id"]], f["path"]) != f["value"]
                    for f in citation["fields"]
                ):
                    raise ValueError("Official count sample field mismatch")
    elif artifact.source_type == "pubmed":
        source = registry.store.get(artifact.metadata["source_handle"], state["session_id"], "literature")
        if artifact.metadata["articles"] != source["articles"] or artifact.coverage.fetched_count != len(
            source["articles"]
        ):
            raise ValueError("Literature metadata differs from immutable source")
        articles = {a["pmid"]: a for a in source["articles"]}
        fetched = set()
        for receipt in source["receipts"]:
            if content_hash(receipt["raw"]) != receipt["raw_hash"]:
                raise ValueError("Literature source hash mismatch")
            if receipt["endpoint"].endswith("esearch.fcgi"):
                search = json.loads(receipt["raw"])["esearchresult"]
                if (
                    set(search["idlist"]) != set(articles)
                    or int(search["count"]) != artifact.coverage.matched_count
                ):
                    raise ValueError("Literature search coverage mismatch")
            else:
                for raw in ET.fromstring(receipt["raw"]).findall("PubmedArticle"):
                    pmid = raw.findtext("./MedlineCitation/PMID")
                    title = raw.find("./MedlineCitation/Article/ArticleTitle")
                    abstract = [
                        {"label": x.get("Label"), "text": "".join(x.itertext())}
                        for x in raw.findall("./MedlineCitation/Article/Abstract/AbstractText")
                    ]
                    if (
                        articles[pmid]["title"] != ("".join(title.itertext()) if title is not None else "")
                        or articles[pmid]["abstract"] != abstract
                    ):
                        raise ValueError("Literature title or abstract differs from XML")
                    fetched.add(pmid)
        if fetched != set(articles):
            raise ValueError("Literature fetch coverage mismatch")
    elif artifact.source_type == "entity" and "ranking" in artifact.metadata:
        _, _, records = registry.scope(artifact.scope_id, state)
        raw_by_id = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
        expected = ranking_from_records(records, artifact.metadata["anchor"], artifact.metadata["top_n"])
        rows = artifact.metadata["ranking"]
        if len(rows) != len(expected):
            raise ValueError("Ranking coverage mismatch")
        for row, target in zip(rows, expected, strict=True):
            if any(row.get(k) != target[k] for k in ("id", "name", "shared_intervention_count")):
                raise ValueError("Ranking differs from complete source relationships")
            page = registry.store.base.evidence_page(row["evidence_set_id"], 0, 1000000)
            if page is None or {c["nct_id"] for c in page["citations"]} != set(target["contributors"]):
                raise ValueError("Ranking relation evidence incomplete")
            for citation in page["citations"]:
                if any(
                    resolve_pointer(raw_by_id[citation["nct_id"]], field["path"]) != field["value"]
                    for field in citation["fields"]
                ):
                    raise ValueError("Ranking source field mismatch")
    elif artifact.source_type == "entity" and "entity_handle" in artifact.metadata:
        source = registry.store.get(artifact.metadata["entity_handle"], state["session_id"], "entity")
        if source != artifact.metadata["resolution"]:
            raise ValueError("Entity mapping source mismatch")
    else:
        raise ValueError("No verifier for this artifact type")
    return {
        "result_id": artifact.result_id,
        "validated": True,
        "basis": artifact.coverage.statistics_basis,
        "receipt_coverage_checked": True,
    }
