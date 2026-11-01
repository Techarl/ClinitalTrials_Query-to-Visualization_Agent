"""Conservative sourced identity candidates; fuzzy similarity never proves identity."""

from difflib import SequenceMatcher

from app.sources import content_hash
from app.v2.contracts import EntityInput

# Curated identifiers only. Biosimilars, formulations and combinations are not merged.
VOCABULARY = [
    {
        "canonical_name": "pembrolizumab",
        "aliases": ["Keytruda", "MK-3475", "SCH 900475"],
        "source": "https://www.cancer.gov/publications/dictionaries/cancer-drug/def/pembrolizumab",
    },
    {
        "canonical_name": "nivolumab",
        "aliases": ["Opdivo", "BMS-936558"],
        "source": "https://www.accessdata.fda.gov/drugsatfda_docs/nda/2014/125554Orig1s000OtherR.pdf",
    },
    {
        "canonical_name": "omalizumab",
        "aliases": ["Xolair"],
        "source": "https://purplebooksearch.fda.gov/index.cfm?blaNo=103976&event=productdetails",
    },
    {
        "canonical_name": "secukinumab",
        "aliases": ["Cosentyx"],
        "source": "https://www.fda.gov/media/151035/download",
    },
    {
        "canonical_name": "adalimumab",
        "aliases": ["Humira"],
        "source": "https://www.fda.gov/media/101972/download",
    },
]
VERSION = "curated-identity-2026-11-01-v1"


def normalize(value):
    return " ".join(value.split()).casefold()


def resolve(names, records=(), source_version=None, rejected=()):
    candidates = []
    for item in VOCABULARY:
        candidates.append(
            {
                **item,
                "canonical_id": "drug:" + content_hash(item["canonical_name"])[:20],
                "source_version": VERSION,
                "identity_basis": "curated_single_ingredient_mapping",
            }
        )
    for raw in records:
        protocol = raw["protocolSection"]
        nct = protocol["identificationModule"]["nctId"]
        for index, item in enumerate(protocol.get("armsInterventionsModule", {}).get("interventions", [])):
            if item.get("type") not in ("DRUG", "BIOLOGICAL") or not item.get("name"):
                continue
            name = item["name"]
            candidates.append(
                {
                    "canonical_name": name,
                    "canonical_id": "record-intervention:"
                    + content_hash([item["type"], normalize(name)])[:20],
                    "aliases": item.get("otherNames", []),
                    "source": f"https://clinicaltrials.gov/study/{nct}",
                    "source_version": source_version,
                    "identity_basis": "source_record_intervention",
                    "source_field": f"/protocolSection/armsInterventionsModule/interventions/{index}",
                }
            )
    resolutions = []
    for name in names:
        exact, fuzzy = {}, {}
        for candidate in candidates:
            if candidate["canonical_id"] in rejected:
                continue
            values = [candidate["canonical_name"], *candidate["aliases"]]
            matches = [v for v in values if normalize(v) == normalize(name)]
            if matches:
                exact.setdefault(
                    candidate["canonical_id"],
                    {
                        **candidate,
                        "match_kind": "exact"
                        if normalize(name) == normalize(candidate["canonical_name"])
                        else "alias",
                    },
                )
            else:
                similarity = max(SequenceMatcher(None, normalize(name), normalize(v)).ratio() for v in values)
                if similarity >= 0.66:
                    fuzzy.setdefault(
                        candidate["canonical_id"],
                        {**candidate, "match_kind": "fuzzy_candidate", "similarity": round(similarity, 3)},
                    )
        # A curated mapping and a record whose canonical spelling is identical corroborate, rather than compete.
        curated = [c for c in exact.values() if c["identity_basis"] == "curated_single_ingredient_mapping"]
        if len(curated) == 1:
            same_names = {normalize(curated[0]["canonical_name"])}
            exact = {
                k: v
                for k, v in exact.items()
                if v["identity_basis"] == "curated_single_ingredient_mapping"
                or normalize(v["canonical_name"]) not in same_names
            }
        result = (
            list(exact.values())
            or sorted(fuzzy.values(), key=lambda c: (-c["similarity"], c["canonical_id"]))[:5]
        )
        status = (
            result[0]["match_kind"] if len(exact) == 1 else "ambiguous" if len(exact) > 1 else "unresolved"
        )
        resolutions.append({"raw_name": name, "resolution_status": status, "candidates": result})
    identities = [
        item["candidates"][0]["canonical_id"]
        for item in resolutions
        if item["resolution_status"] in ("exact", "alias")
    ]
    return {
        "resolutions": resolutions,
        "same_entity_comparison": len(identities) == len(names) and len(set(identities)) < len(identities),
        "vocabulary_version": VERSION,
        "candidate_policy": "Fuzzy matches require user confirmation; record aliases apply only to their explicitly named intervention.",
    }


def register_entities(registry):
    def execute(args, state):
        records, version = [], None
        if args.scope_id:
            _, snapshot, records = registry.scope(args.scope_id, state)
            version = snapshot.raw_hash
        result = resolve(args.names, records, version, args.rejected_ids)
        handle = registry.store.asset(state["session_id"], state["run_id"], "entity", result)
        output = {"entity_handle": handle, **result}
        if args.goal_id:
            from app.v2.contracts import Artifact, Coverage

            registry.goal(args.goal_id, state, "entity")
            if any(r["resolution_status"] not in ("exact", "alias") for r in result["resolutions"]):
                return {**output, "goal_unmet": args.goal_id}
            artifact = Artifact(
                artifact_id="",
                result_id="",
                goal_id=args.goal_id,
                source_type="entity",
                title="Sourced drug identity mappings",
                metric="Verified name mappings, not trial counts",
                coverage=Coverage(
                    statistics_basis="entity",
                    count_exactness="not_applicable",
                    population_coverage="not_applicable",
                    evidence_coverage="mapping_sources",
                    fetched_count=len(result["resolutions"]),
                ),
                metadata={"entity_handle": handle, "resolution": result},
            )
            output.update(registry.publish(artifact, state, {}))
        return output

    registry.register(
        "resolve_entity_candidates",
        "Resolve brands, generic names or codes using versioned authoritative mappings and optional CTgov otherNames. Similarity gives candidates only; same-entity comparisons are flagged.",
        EntityInput,
        execute,
    )
