"""Trial record lookup and cited text answers, distinct from population statistics."""

import json
import re

from pydantic import Field

from app.analysis import resolve_pointer
from app.models import Contract
from app.sources import ClinicalTrialsClient, content_hash
from app.v2.contracts import Advisory, Artifact, Coverage, SourceFinding


class LookupInput(Contract):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class TextAnswerInput(Contract):
    goal_id: str
    answer: str = Field(min_length=1, max_length=4000)
    findings: list[SourceFinding] = Field(min_length=1, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=8)


TEXT_EVIDENCE_KEY = "__text_evidence__"
MAX_EXPORTED_FIELD_CHARACTERS = 32000


def _source_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _source_document(raw, pointer, quotation):
    """Map only recognized immutable source shapes, never an ID mentioned in prose."""
    if isinstance(raw, dict) and isinstance(raw.get("protocolSection"), dict):
        identification = raw["protocolSection"].get("identificationModule", {})
        if not isinstance(identification, dict):
            raise ValueError("Source record identification is malformed")
        identity = identification.get("nctId", "")
        if not isinstance(identity, str) or not re.fullmatch(r"NCT[0-9]{8}", identity):
            raise ValueError("Source record has no valid NCT identity")
        return {
            "source_type": "clinicaltrials", "document_id": identity,
            "title": identification.get("briefTitle") if isinstance(identification.get("briefTitle"), str) else "",
            "url": "https://clinicaltrials.gov/study/" + identity,
            "document_pointer": "", "document": raw, "retrieved_at": None, "receipt_hash": None,
        }
    if isinstance(raw, dict) and isinstance(raw.get("articles"), list):
        indexed = re.match(r"^/articles/(0|[1-9][0-9]*)(?:/|$)", pointer)
        if indexed:
            index = int(indexed.group(1))
            candidates = [(index, raw["articles"][index])] if index < len(raw["articles"]) else []
        elif pointer in ("", "/articles"):
            candidates = [(i, article) for i, article in enumerate(raw["articles"])
                          if quotation in _source_text(article)]
        else:
            candidates = []
        if len(candidates) != 1:
            raise ValueError("Use a precise article field pointer for unambiguous portable provenance")
        index, article = candidates[0]
        if not isinstance(article, dict):
            raise ValueError("Source article is malformed")
        identity = article.get("pmid", "")
        if not isinstance(identity, str) or not re.fullmatch(r"[1-9][0-9]*", identity):
            raise ValueError("Source article has no valid PMID identity")
        receipt = article.get("source_hash")
        return {
            "source_type": "pubmed", "document_id": identity,
            "title": article.get("title") if isinstance(article.get("title"), str) else "",
            "url": "https://pubmed.ncbi.nlm.nih.gov/" + identity + "/",
            "document_pointer": f"/articles/{index}", "document": article,
            "retrieved_at": article.get("fetched_at") if isinstance(article.get("fetched_at"), str) else None,
            "receipt_hash": receipt if isinstance(receipt, str) and re.fullmatch(r"[a-fA-F0-9]{64}", receipt) else None,
        }
    raise ValueError("Portable text answers require an owned trial record or a specific PubMed article")


def enrich_finding(registry, finding, state):
    """Only handle/pointer/quotation come from the model; all source metadata is server-derived."""
    source = registry.store.get(finding["source_handle"], state["session_id"], "source_text")
    pointer, quotation = finding["pointer"], finding["quotation"]
    value = resolve_pointer(source, pointer) if pointer else source
    text = _source_text(value)
    if quotation not in text:
        raise ValueError("Answer quotation is absent from its immutable source field")
    document = _source_document(source, pointer, quotation)
    document_hash = content_hash(document["document"])
    citation_id = "text_" + content_hash([
        document["source_type"], document["document_id"], document_hash, pointer, quotation,
    ])[:24]
    # Never embed an entire root record/article just because a root pointer was read.
    whole_document = pointer in ("", "/articles", document["document_pointer"])
    included = not whole_document and len(text) <= MAX_EXPORTED_FIELD_CHARACTERS
    return {
        "source_handle": finding["source_handle"], "pointer": pointer, "quotation": quotation,
        "provenance": {
            "version": "text-finding-v1", "resolution": "exact", "citation_id": citation_id,
            **{k: document[k] for k in ("source_type", "document_id", "title", "url", "document_pointer",
                                        "retrieved_at", "receipt_hash")},
            "source_hash": content_hash(source), "source_hash_scope": "canonical_source_text_container",
            "document_hash": document_hash, "field_value_hash": content_hash(value),
            "quotation_hash": content_hash(quotation), "hash_serialization": "canonical_json_v1",
            "version_notice": "The official URL identifies the source; its current content may differ from this saved version.",
        },
        "source_field": {
            "pointer": pointer, "value_included": included, "value": value if included else None,
            "serialized_character_count": len(text), "exact_quoted_value": quotation,
            "omission_reason": None if included else "whole_source_container" if whole_document else "field_exceeds_32000_characters",
        },
    }


def export_text_evidence(artifacts):
    """Return a separate reserved-key payload, without changing chart evidence map entries."""
    output = []
    for artifact in artifacts:
        if artifact.get("source_type") != "answer":
            continue
        findings = []
        for saved in artifact.get("metadata", {}).get("findings", []):
            finding = {k: saved[k] for k in ("source_handle", "pointer", "quotation")}
            finding["provenance"] = saved.get("provenance") or {
                "version": "legacy", "resolution": "not_recorded",
                "notice": "This legacy artifact did not record portable source metadata.",
            }
            finding["source_field"] = saved.get("source_field")
            findings.append(finding)
        output.append({"result_id": artifact["result_id"], "goal_id": artifact["goal_id"], "findings": findings})
    return {"schema": "text-evidence-v1", "artifacts": output} if output else None


def verify_answer(registry, artifact, state):
    for finding in artifact.metadata["findings"]:
        raw = registry.store.get(finding["source_handle"], state["session_id"], "source_text")
        value = resolve_pointer(raw, finding["pointer"]) if finding["pointer"] else raw
        if finding["quotation"] not in _source_text(value):
            raise ValueError("Answer quotation is absent from its immutable source field")
        if artifact.metadata.get("text_provenance_version") or "provenance" in finding or "source_field" in finding:
            expected = enrich_finding(registry, finding, state)
            if finding.get("provenance") != expected["provenance"] or finding.get("source_field") != expected["source_field"]:
                raise ValueError("Answer provenance differs from its immutable source")
    return {
        "result_id": artifact.result_id,
        "validated": True,
        "validation_scope": "Exact quotations, source ownership, and recorded portable metadata only; semantic entailment is not mechanically verified",
    }


def register_reading(registry):
    def lookup(args, state):
        frozen = getattr(registry, "reading_records", None)
        if frozen is not None:
            from experiments.retrieval import HybridIndex

            documents = [
                {"id": nct, "text": json.dumps(raw, ensure_ascii=False)} for nct, raw in frozen.items()
            ]
            ids = re.findall(r"NCT\d{8}", args.query)
            if not ids:
                ids = [r["id"] for r in HybridIndex(documents).search(args.query, args.limit)["bm25"]]
            records = [frozen[nct] for nct in ids[: args.limit] if nct in frozen]
            basis = "frozen_trial_records_keyword_search"
        else:
            client = ClinicalTrialsClient(registry.settings)
            try:
                if re.fullmatch(r"NCT\d{8}", args.query):
                    records = [client.get("/studies/" + args.query, None, registry.deadline(state))]
                else:
                    response = client.get(
                        "/studies",
                        {"query.term": args.query, "pageSize": args.limit, "format": "json"},
                        registry.deadline(state),
                    )
                    records = response.get("studies", [])
                basis = "official_CTgov_search_sample_for_reading_only"
            finally:
                client.close()
        found = []
        for raw in records:
            identification = raw["protocolSection"]["identificationModule"]
            handle = registry.store.asset(state["session_id"], state["run_id"], "source_text", raw)
            found.append(
                {
                    "nct_id": identification["nctId"],
                    "title": identification.get("briefTitle"),
                    "source_handle": handle,
                    "source_hash": content_hash(raw),
                    "readable_fields": list(raw["protocolSection"]),
                }
            )
        return {
            "matches": found,
            "retrieval_basis": basis,
            "query": args.query,
            "statistical_scope": False,
            "instruction": "Use read_source with a precise pointer before answering. These sample records cannot be used as a statistical population.",
        }

    def publish(args, state):
        registry.goal(args.goal_id, state, "answer")
        for finding in args.findings:
            if not any(
                o.get("status") == "ok"
                and o.get("source_handle") == finding.source_handle
                and o.get("pointer", "") == finding.pointer
                and finding.quotation in o.get("text", "")
                for o in state["observations"]
            ):
                raise ValueError("Read each quoted field with read_source before publishing the answer")
        artifact = Artifact(
            artifact_id="",
            result_id="",
            goal_id=args.goal_id,
            source_type="answer",
            title="Source-grounded text answer",
            metric="Qualitative answer from quoted source fields; no population counts",
            coverage=Coverage(
                statistics_basis="source_excerpts",
                count_exactness="not_applicable",
                population_coverage="not_applicable",
                evidence_coverage="quoted_sources",
                fetched_count=len(args.findings),
            ),
            metadata={
                "answer": args.answer,
                "text_provenance_version": "text-finding-v1",
                "findings": [enrich_finding(registry, f.model_dump(), state) for f in args.findings],
                "limitations": args.limitations,
                "validation_scope": "quoted-source integrity; semantic answer correctness requires separate evaluation",
            },
            warnings=[
                Advisory(
                    code="qualitative_answer",
                    message="Quoted source text is checked exactly. Its interpretation is model-generated and is not a verified statistic.",
                )
            ],
        )
        return registry.publish(artifact, state, {})

    registry.register(
        "lookup_trials",
        "Find trial records for textual questions using an NCT ID or focused keyword query. This is sampled reading retrieval, never a statistical population.",
        LookupInput,
        lookup,
    )
    registry.register(
        "publish_text_answer",
        "Publish a qualitative answer with exact quotations already read from sources. Quote integrity is checked; semantic interpretation is explicitly model-generated.",
        TextAnswerInput,
        publish,
    )
