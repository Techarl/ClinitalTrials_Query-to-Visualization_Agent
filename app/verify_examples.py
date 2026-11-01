"""Offline completeness and exact-evidence verification, independently from Neo4j."""

import json
import math
from collections import Counter
from pathlib import Path

from app.config import PROJECT_ROOT
from app.models import AnalysisPlan, AnalysisResponse
from app.reference import expected_network, expected_table
from app.sources import content_hash


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
        countries = {v.get("country") for v in p.get("contactsLocationsModule", {}).get("locations", [])}
        if filters["countries"] and not set(filters["countries"]) & countries:
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


def query_for(filters):
    query = {}
    for key, field in [("query.cond", "condition"), ("query.lead", "sponsor")]:
        if filters[field]:
            query[key] = '"' + filters[field] + '"'
    if filters["interventions"]:
        joiner = " AND " if filters["intervention_match"] == "all" else " OR "
        query["query.intr"] = (
            "(" + joiner.join('"' + d + '"' for d in sorted(set(filters["interventions"]))) + ")"
        )
    return query


def verify(folder: Path, root: Path) -> dict:
    response = AnalysisResponse.model_validate_json((folder / "response.json").read_text(encoding="utf-8"))
    assert response.status in ("ok", "no_data"), "Only completed analyses can be verified"
    plan = AnalysisPlan.model_validate(response.meta["plan"]).model_dump()
    assert response.meta["completeness"] == "complete"
    assert response.meta["source_truncated"] is False
    manifests, raw_by_dataset = {}, {}
    for manifest in response.provenance["datasets"]:
        dataset_id = manifest["dataset_id"]
        assert dataset_id not in manifests
        assert dataset_id.startswith("ds_") and dataset_id[3:].isalnum()
        source = root / "snapshots" / dataset_id
        assert json.loads((source / "manifest.json").read_text(encoding="utf-8")) == manifest
        records = json.loads((source / "studies.json").read_text(encoding="utf-8"))
        assert content_hash(records) == manifest["raw_hash"]
        assert manifest["complete"] is True
        assert content_hash(manifest["query"]) == manifest["query_hash"]
        assert len(records) == manifest["expected_count"] == manifest["retrieved_unique_count"]
        assert len({r["protocolSection"]["identificationModule"]["nctId"] for r in records}) == len(records)
        manifests[dataset_id], raw_by_dataset[dataset_id] = manifest, records
    records_by_cohort, dataset_by_label = {}, {}
    assert len(response.meta["cohorts"]) == len(plan["cohorts"])
    for actual, cohort in zip(response.meta["cohorts"], plan["cohorts"], strict=True):
        assert (actual["id"], actual["label"], actual["filters"]) == (
            cohort["id"],
            cohort["label"],
            cohort["filters"],
        )
        dataset_id = actual["dataset_id"]
        assert manifests[dataset_id]["query"] == query_for(cohort["filters"])
        records = selected(raw_by_dataset[dataset_id], cohort["filters"])
        assert actual["source_record_count"] == len(raw_by_dataset[dataset_id])
        assert actual["selected_trial_count"] == len(records)
        assert actual["missing_enrollment"] == sum(
            r["protocolSection"].get("designModule", {}).get("enrollmentInfo", {}).get("count") is None
            for r in records
        )
        records_by_cohort[cohort["label"]] = records
        assert actual["missing_start_year"] == sum(
            not r["protocolSection"].get("statusModule", {}).get("startDateStruct", {}).get("date")
            for r in records
        )
        assert actual["estimated_start_dates"] == sum(
            r["protocolSection"].get("statusModule", {}).get("startDateStruct", {}).get("type") == "ESTIMATED"
            for r in records
        )
        assert actual["enrollment_types"] == {
            kind: sum(
                r["protocolSection"].get("designModule", {}).get("enrollmentInfo", {}).get("type") == kind
                for r in records
            )
            for kind in ("ACTUAL", "ESTIMATED")
        }
        dataset_by_label[cohort["label"]] = dataset_id
    assert set(manifests) == set(dataset_by_label.values())
    evidence_path = folder / "response.evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
    assert set(evidence) == set(response.provenance["evidence"])
    raw_index = {
        (ds, r["protocolSection"]["identificationModule"]["nctId"]): r
        for ds, records in raw_by_dataset.items()
        for r in records
    }
    checked_fields = 0
    for evidence_id, entry in evidence.items():
        assert entry["total_contributors"] == len(entry["citations"])
        assert len({(c["dataset_id"], c["nct_id"]) for c in entry["citations"]}) == len(entry["citations"])
        preview = response.provenance["evidence"][evidence_id]
        assert preview["contributor_count"] == len(entry["citations"])
        assert preview["citations"] == entry["citations"][: len(preview["citations"])]
        assert preview["preview_complete"] == (len(preview["citations"]) == len(entry["citations"]))
        assert preview["url"] == f"/evidence/{evidence_id}"
        for citation in entry["citations"]:
            raw = raw_index[(citation["dataset_id"], citation["nct_id"])]
            assert citation["url"] == f"https://clinicaltrials.gov/study/{citation['nct_id']}"
            paths = [field["path"] for field in citation["fields"]]
            assert len(paths) == len(set(paths))
            assert "/protocolSection/identificationModule/nctId" in paths
            required, absent_fields = set(), set()
            for relative in [
                "designModule/phases",
                "designModule/studyType",
                "designModule/enrollmentInfo",
                "statusModule/overallStatus",
                "statusModule/startDateStruct",
                "statusModule/studyFirstPostDateStruct",
                "sponsorCollaboratorsModule/leadSponsor",
                "conditionsModule/conditions",
            ]:
                path = "/protocolSection/" + relative
                try:
                    pointer(raw, path)
                    required.add(path)
                except (KeyError, IndexError):
                    absent_fields.add(path)
            p = raw["protocolSection"]
            for index, item in enumerate(p.get("armsInterventionsModule", {}).get("interventions", [])):
                for key in ("name", "type", "otherNames"):
                    if key in item:
                        required.add(f"/protocolSection/armsInterventionsModule/interventions/{index}/{key}")
            for index, item in enumerate(p.get("contactsLocationsModule", {}).get("locations", [])):
                if "country" in item:
                    required.add(f"/protocolSection/contactsLocationsModule/locations/{index}/country")
            assert required <= set(paths)
            assert set(citation.get("missing_fields", [])) == absent_fields
            for field in citation["fields"]:
                assert pointer(raw, field["path"]) == field["value"]
                checked_fields += 1
    used = set()

    def check_evidence(evidence_id, ids, label, rule):
        used.add(evidence_id)
        entry = evidence[evidence_id]
        assert {(c["dataset_id"], c["nct_id"]) for c in entry["citations"]} == {
            (dataset_by_label[label], nct) for nct in ids
        }
        expected_rule = {
            "cohort_id": next(c["id"] for c in plan["cohorts"] if c["label"] == label),
            "dataset_id": dataset_by_label[label],
            **rule,
        }
        assert entry["rule"] == expected_rule
        assert (
            evidence_id
            == "ev_"
            + content_hash(
                {"run_id": response.run_id, "rule": entry["rule"], "citations": entry["citations"]}
            )[:32]
        )

    visual = response.visualization
    if plan["operation"] == "histogram" and visual is not None:
        # This invariant uses the OUTPUT intervals and raw values directly. It
        # remains effective even if both production and expected_table share a bug.
        for label, records in records_by_cohort.items():
            values = {
                r["protocolSection"]["identificationModule"]["nctId"]: r["protocolSection"]
                .get("designModule", {})
                .get("enrollmentInfo", {})
                .get("count")
                for r in records
            }
            expected_ids = Counter({key: 1 for key, value in values.items() if value is not None})
            seen = Counter()
            rows = [row for row in visual.data if row.cohort == label]
            assert len(rows) == plan["histogram_bins"], "Histogram interval count mismatch"
            for index, row in enumerate(rows):
                assert (
                    math.isfinite(row.bin_start)
                    and math.isfinite(row.bin_end)
                    and row.bin_start < row.bin_end
                ), "Invalid histogram interval"
                if index:
                    assert rows[index - 1].bin_end == row.bin_start, "Discontinuous histogram interval"
                for citation in evidence[row.evidence_set_id]["citations"]:
                    assert citation["dataset_id"] == dataset_by_label[label]
                    value = values[citation["nct_id"]]
                    assert (
                        value is not None
                        and row.bin_start <= value
                        and (value < row.bin_end or (index == len(rows) - 1 and value <= row.bin_end))
                    ), "Contributor outside declared histogram interval"
                    seen[citation["nct_id"]] += 1
            assert seen == expected_ids, "Histogram interval contributors must appear exactly once"
    if visual is not None:
        if plan["operation"] == "network":
            channels = {
                "node_id": ("id", "identifier"),
                "node_label": ("label", "nominal"),
                "node_type": ("entity_type", "nominal"),
                "source": ("source", "identifier"),
                "target": ("target", "identifier"),
                "weight": ("weight", "quantitative"),
            }
        elif plan["operation"] == "histogram":
            channels = {
                "x": ("bin_start", "quantitative"),
                "x2": ("bin_end", "quantitative"),
                "y": ("trial_count", "quantitative"),
                "series": ("cohort", "nominal"),
            }
        else:
            channels = {
                "x": ("year" if plan["operation"] == "time_series" else "category", "ordinal"),
                "y": ("trial_count", "quantitative"),
                "series": ("cohort", "nominal"),
            }
        assert {key: (value.field, value.type) for key, value in visual.encoding.items()} == channels
    if plan["operation"] != "network":
        expected, clipping, missing = expected_table(plan, records_by_cohort)
        has_data = any(item["ids"] for item in expected)
        assert response.status == ("ok" if has_data else "no_data")
        if has_data:
            kind = (
                "time_series"
                if plan["operation"] == "time_series"
                else "histogram"
                if plan["operation"] == "histogram"
                else "grouped_bar"
                if len(plan["cohorts"]) > 1
                else "bar_chart"
            )
            assert visual is not None and visual.type == kind and visual.title == plan["title"]
            assert len(visual.data) == len(expected), "Missing or extra chart rows"
            for row, item in zip(visual.data, expected, strict=True):
                assert {k: getattr(row, k) for k in item if k != "ids"} == {
                    k: v for k, v in item.items() if k != "ids"
                }
                assert row.trial_count == row.contributor_count == len(item["ids"])
                if plan["operation"] == "histogram":
                    rule = {
                        "operation": "histogram",
                        "field": "enrollment",
                        "bin_start": row.bin_start,
                        "bin_end": row.bin_end,
                        "end_inclusive": row.id.endswith(f":{plan['histogram_bins'] - 1}"),
                    }
                else:
                    rule = {
                        "operation": plan["operation"],
                        "dimension": plan["group_by"]
                        if plan["operation"] == "category"
                        else plan["time_field"],
                        "category": row.category if plan["operation"] == "category" else row.year,
                    }
                check_evidence(row.evidence_set_id, item["ids"], row.cohort, rule)
        else:
            assert visual is None
        assert response.meta["excluded_missing_values"] == missing
    else:
        limit = response.meta.get("network_edge_limit", 100)
        assert 1 <= limit <= 1000
        expected, nodes, entities, clipping = expected_network(
            plan, records_by_cohort, min(plan["top_n"], limit)
        )
        assert response.status == ("ok" if expected else "no_data")
        if expected:
            assert visual is not None and visual.type == "network_graph" and visual.title == plan["title"]
            assert len(visual.data.edges) == len(expected), "Missing or extra network edges"
            assert [n.id for n in visual.data.nodes] == sorted(nodes), (
                "Missing, duplicate or fabricated network nodes"
            )
            labels = {n.id: n.label for n in visual.data.nodes}
            cohort_label = plan["cohorts"][0]["label"]
            for node in visual.data.nodes:
                assert node.entity_type == entities[node.id][1]
                assert (
                    " ".join(node.label.split()).casefold()
                    == " ".join(entities[node.id][0].split()).casefold()
                )
                assert node.trial_count == len(nodes[node.id])
                check_evidence(
                    node.evidence_set_id,
                    nodes[node.id],
                    cohort_label,
                    {"operation": "network_node", "node": node.label, "scope": "displayed_edges"},
                )
            for edge, (pair, expected_edge) in zip(visual.data.edges, expected, strict=True):
                assert (edge.source, edge.target) == pair
                assert edge.id == "edge_" + content_hash(list(pair))[:24]
                assert edge.weight == expected_edge["weight"]
                assert edge.contributor_count == len(expected_edge["ids"])
                assert edge.weight_metric == (
                    "shared_intervention_count"
                    if plan["relationship"] == "sponsor_shared_drug"
                    else "distinct_trial_count"
                )
                assert sorted(" ".join(n.split()).casefold() for n in edge.shared_interventions) == sorted(
                    " ".join(entities[k][0].split()).casefold() for k in expected_edge["shared"]
                )
                check_evidence(
                    edge.evidence_set_id,
                    expected_edge["ids"],
                    cohort_label,
                    {
                        "operation": "network",
                        "relationship": plan["relationship"],
                        "source": labels[pair[0]],
                        "target": labels[pair[1]],
                    },
                )
        else:
            assert visual is None
    assert response.meta["presentation_clipping"] == clipping
    assert used == set(evidence), "Unreferenced or missing evidence sets"
    return {
        "example": folder.name,
        "status": "verified",
        "exact_fields_checked": checked_fields,
        "evidence_sets": len(evidence),
        "source_records": len(raw_index),
        "complete_item_sets_checked": True,
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
