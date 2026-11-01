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


def verify_answer(registry, artifact, state):
    for finding in artifact.metadata["findings"]:
        raw = registry.store.get(finding["source_handle"], state["session_id"], "source_text")
        value = resolve_pointer(raw, finding["pointer"]) if finding["pointer"] else raw
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        if finding["quotation"] not in text:
            raise ValueError("Answer quotation is absent from its immutable source field")
    return {
        "result_id": artifact.result_id,
        "validated": True,
        "validation_scope": "Exact quotations and source ownership only; semantic entailment is not mechanically verified",
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
                "findings": [f.model_dump() for f in args.findings],
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
