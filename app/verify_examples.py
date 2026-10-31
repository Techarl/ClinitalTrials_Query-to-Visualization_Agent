"""Independent offline checks against frozen raw sources, without Neo4j or a model.

The reference logic intentionally does not import the query compiler, normalization,
or production aggregation implementation.
"""

import json
from collections import defaultdict
from pathlib import Path

from app.config import PROJECT_ROOT
from app.models import AnalysisResponse
from app.sources import content_hash

PHASES = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not applicable",
}


def pointer(raw, path):
    for key in path.split("/")[1:]:
        key = key.replace("~1", "/").replace("~0", "~")
        raw = raw[int(key)] if isinstance(raw, list) else raw[key]
    return raw


def selected(studies, filters):
    result = []
    for raw in studies:
        p = raw["protocolSection"]
        design, status = p.get("designModule", {}), p.get("statusModule", {})
        if filters["phases"] and not set(filters["phases"]) & set(design.get("phases", [])):
            continue
        if filters["statuses"] and status.get("overallStatus") not in filters["statuses"]:
            continue
        if filters["countries"] and not set(filters["countries"]) & {
            v.get("country") for v in p.get("contactsLocationsModule", {}).get("locations", [])
        }:
            continue
        date = status.get("startDateStruct", {}).get("date", "")
        year = int(date[:4]) if date else None
        if filters["start_year"] is not None and (year is None or year < filters["start_year"]):
            continue
        if filters["end_year"] is not None and (year is None or year > filters["end_year"]):
            continue
        if (
            filters["enrollment_type"]
            and design.get("enrollmentInfo", {}).get("type") != filters["enrollment_type"]
        ):
            continue
        result.append(raw)
    return result


def verify(folder: Path, root: Path) -> dict:
    response = AnalysisResponse.model_validate_json((folder / "response.json").read_text(encoding="utf-8"))
    assert response.status == "ok"
    plan = response.meta["plan"]
    raw_by_dataset = {}
    for manifest in response.provenance["datasets"]:
        dataset_id = manifest["dataset_id"]
        records = json.loads((root / "snapshots" / dataset_id / "studies.json").read_text(encoding="utf-8"))
        assert content_hash(records) == manifest["raw_hash"]
        assert manifest["complete"] is True
        assert len(records) == manifest["expected_count"] == manifest["retrieved_unique_count"]
        raw_by_dataset[dataset_id] = records
    records_by_cohort = {}
    for cohort in response.meta["cohorts"]:
        records = selected(raw_by_dataset[cohort["dataset_id"]], cohort["filters"])
        assert len(records) == cohort["selected_trial_count"]
        records_by_cohort[cohort["label"]] = records
    evidence = json.loads((folder / "response.evidence.json").read_text(encoding="utf-8"))
    raw_index = {
        (dataset_id, raw["protocolSection"]["identificationModule"]["nctId"]): raw
        for dataset_id, records in raw_by_dataset.items()
        for raw in records
    }
    checked_fields = 0
    for evidence_id, entry in evidence.items():
        assert entry["total_contributors"] == len(entry["citations"])
        assert len({(c["dataset_id"], c["nct_id"]) for c in entry["citations"]}) == len(entry["citations"])
        for citation in entry["citations"]:
            raw = raw_index[(citation["dataset_id"], citation["nct_id"])]
            for field in citation["fields"]:
                assert pointer(raw, field["path"]) == field["value"]
                checked_fields += 1
            for absent in citation.get("missing_fields", []):
                try:
                    pointer(raw, absent)
                except (KeyError, IndexError):
                    pass
                else:
                    raise AssertionError("A field marked missing exists")
        assert evidence_id in response.provenance["evidence"]

    def ids(entry):
        return {c["nct_id"] for c in entry["citations"]}

    visual = response.visualization
    if visual.type != "network_graph":
        for row in visual.data:
            contributors = set()
            for raw in records_by_cohort[row.cohort]:
                p = raw["protocolSection"]
                if visual.type in ("bar_chart", "grouped_bar"):
                    assert plan["group_by"] == "phase", (
                        "This submission verifier covers the demonstrated category dimension"
                    )
                    phases = p.get("designModule", {}).get("phases", [])
                    category = (
                        " / ".join(PHASES[v] for v in sorted(phases, key=lambda v: list(PHASES).index(v)))
                        if phases
                        else "Not reported"
                    )
                    include = category == row.category
                elif visual.type == "time_series":
                    field = (
                        "startDateStruct"
                        if plan["time_field"] == "start_year"
                        else "studyFirstPostDateStruct"
                    )
                    date = p.get("statusModule", {}).get(field, {}).get("date", "")
                    include = bool(date) and int(date[:4]) == row.year
                else:
                    count = p.get("designModule", {}).get("enrollmentInfo", {}).get("count")
                    last_end = max(item.bin_end for item in visual.data)
                    include = (
                        count is not None
                        and row.bin_start <= count
                        and (count < row.bin_end or (row.bin_end == last_end and count == row.bin_end))
                    )
                if include:
                    contributors.add(p["identificationModule"]["nctId"])
            assert row.trial_count == row.contributor_count == len(contributors)
            assert ids(evidence[row.evidence_set_id]) == contributors
    else:
        assert plan["relationship"] == "sponsor_shared_drug"
        nodes = {node.id: node for node in visual.data.nodes}
        sponsors = defaultdict(lambda: defaultdict(set))
        for records in records_by_cohort.values():
            for raw in records:
                p = raw["protocolSection"]
                name = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name")
                if not name:
                    continue
                sponsor = " ".join(name.split()).casefold()
                for intervention in p.get("armsInterventionsModule", {}).get("interventions", []):
                    if intervention.get("type") in ("DRUG", "BIOLOGICAL"):
                        key = (intervention["type"], " ".join(intervention["name"].split()).casefold())
                        sponsors[sponsor][key].add(p["identificationModule"]["nctId"])
        for edge in visual.data.edges:
            left = sponsors[" ".join(nodes[edge.source].label.split()).casefold()]
            right = sponsors[" ".join(nodes[edge.target].label.split()).casefold()]
            common = set(left) & set(right)
            assert edge.weight == len(common)
            contributors = set().union(*(left[key] | right[key] for key in common))
            assert edge.contributor_count == len(contributors)
            assert ids(evidence[edge.evidence_set_id]) == contributors
    return {
        "example": folder.name,
        "status": "verified",
        "exact_fields_checked": checked_fields,
        "evidence_sets": len(evidence),
        "source_records": len(raw_index),
    }


def main():
    root = PROJECT_ROOT / "examples"
    reports = [verify(folder, root) for folder in sorted(root.glob("[0-9][0-9]_*")) if folder.is_dir()]
    if len(reports) != 5:
        raise AssertionError("Expected five complete examples")
    report = {"checks": reports, "network_or_database_calls": 0, "model_calls": 0}
    (root / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
