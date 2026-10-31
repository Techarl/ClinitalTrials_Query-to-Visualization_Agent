"""Deterministic graph analysis, chart construction, and exact source evidence."""

from collections import defaultdict
from math import floor

from app.config import Settings
from app.graphdb import GraphRepository
from app.models import (
    AnalysisPlan,
    ChartRow,
    Encoding,
    NetworkData,
    NetworkEdge,
    NetworkNode,
    NetworkVisualization,
    TabularVisualization,
)
from app.sources import Snapshot, content_hash
from app.storage import StateStore


def resolve_pointer(value, pointer: str):
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def record_citation(dataset_id: str, raw: dict) -> dict:
    protocol = raw["protocolSection"]
    paths = ["/protocolSection/identificationModule/nctId"]
    missing_fields = []
    optional = (
        "designModule/phases",
        "designModule/studyType",
        "designModule/enrollmentInfo",
        "statusModule/overallStatus",
        "statusModule/startDateStruct",
        "statusModule/studyFirstPostDateStruct",
        "sponsorCollaboratorsModule/leadSponsor",
        "conditionsModule/conditions",
    )
    for relative in optional:
        pointer = "/protocolSection/" + relative
        try:
            resolve_pointer(raw, pointer)
            paths.append(pointer)
        except (KeyError, IndexError):
            missing_fields.append(pointer)
            parent = pointer.rsplit("/", 1)[0]
            try:
                resolve_pointer(raw, parent)
                if parent not in paths:
                    paths.append(parent)
            except KeyError:
                pass
    for index, intervention in enumerate(
        protocol.get("armsInterventionsModule", {}).get("interventions", [])
    ):
        for field in ("name", "type", "otherNames"):
            if field in intervention:
                paths.append(f"/protocolSection/armsInterventionsModule/interventions/{index}/{field}")
    for index, location in enumerate(protocol.get("contactsLocationsModule", {}).get("locations", [])):
        if "country" in location:
            paths.append(f"/protocolSection/contactsLocationsModule/locations/{index}/country")
    nct_id = protocol["identificationModule"]["nctId"]
    return {
        "dataset_id": dataset_id,
        "nct_id": nct_id,
        "url": f"https://clinicaltrials.gov/study/{nct_id}",
        "fields": [{"path": pointer, "value": resolve_pointer(raw, pointer)} for pointer in paths],
        "missing_fields": missing_fields,
    }


def encode(field: str, kind: str, title: str) -> Encoding:
    return Encoding(field=field, type=kind, title=title)


class AnalysisEngine:
    def __init__(self, settings: Settings, graph: GraphRepository, store: StateStore):
        self.settings, self.graph, self.store = settings, graph, store

    def run(self, plan: AnalysisPlan, datasets: dict[str, tuple[Snapshot, list[dict]]], run_id: str):
        records_by_cohort = {}
        evidence_ids: set[str] = set()
        citations = {}
        cohort_meta = []
        for cohort in plan.cohorts:
            snapshot, raw_records = datasets[cohort.id]
            if not snapshot.complete:
                raise ValueError("Incomplete source dataset cannot be analyzed")
            raw_index = {raw["protocolSection"]["identificationModule"]["nctId"]: raw for raw in raw_records}
            records = [item["record"] for item in self.graph.records(snapshot.dataset_id, cohort.filters)]
            for record in records:
                if record["nct_id"] not in raw_index or record["raw_hash"] != content_hash(
                    raw_index[record["nct_id"]]
                ):
                    raise ValueError("Graph record does not match immutable source snapshot")
                key = (snapshot.dataset_id, record["nct_id"])
                citations[key] = record_citation(snapshot.dataset_id, raw_index[record["nct_id"]])
            records_by_cohort[cohort.id] = records
            cohort_meta.append(
                {
                    "id": cohort.id,
                    "label": cohort.label,
                    "dataset_id": snapshot.dataset_id,
                    "source_record_count": snapshot.retrieved_unique_count,
                    "selected_trial_count": len(records),
                    "filters": cohort.filters.model_dump(),
                    "missing_start_year": sum(r.get("start_year") is None for r in records),
                    "estimated_start_dates": sum(r.get("start_date_type") == "ESTIMATED" for r in records),
                    "missing_enrollment": sum(r.get("enrollment") is None for r in records),
                    "enrollment_types": {
                        kind: sum(r.get("enrollment_type") == kind for r in records)
                        for kind in ("ACTUAL", "ESTIMATED")
                    },
                }
            )

        def evidence(cohort_id: str, contributor_ids: list[str] | set[str], rule: dict) -> str:
            snapshot = datasets[cohort_id][0]
            ids = sorted(set(contributor_ids))
            try:
                refs = [citations[(snapshot.dataset_id, nct_id)] for nct_id in ids]
            except KeyError as error:
                raise ValueError("A result cites a record outside its cohort") from error
            evidence_id = self.store.evidence(
                run_id, refs, {"cohort_id": cohort_id, "dataset_id": snapshot.dataset_id, **rule}
            )
            evidence_ids.add(evidence_id)
            return evidence_id

        clipping = {"applied": False, "omitted_items": 0}
        omitted_missing = 0
        if plan.operation in ("category", "time_series"):
            dimension = plan.group_by if plan.operation == "category" else plan.time_field
            groups = {}
            totals: dict = defaultdict(int)
            for cohort in plan.cohorts:
                found = self.graph.groups(datasets[cohort.id][0].dataset_id, cohort.filters, dimension)
                groups[cohort.id] = {
                    item["category"]: sorted(set(item["contributors"]))
                    for item in found
                    if item["category"] is not None
                }
                omitted_missing += sum(
                    len(item["contributors"]) for item in found if item["category"] is None
                )
                for category, ids in groups[cohort.id].items():
                    totals[category] += len(ids)
            if plan.operation == "time_series":
                categories = sorted(totals)
                if categories:
                    lower, upper = min(categories), max(categories)
                    if plan.time_field == "start_year":
                        lower = min(
                            c.filters.start_year if c.filters.start_year is not None else lower
                            for c in plan.cohorts
                        )
                        upper = max(
                            c.filters.end_year if c.filters.end_year is not None else upper
                            for c in plan.cohorts
                        )
                    if upper - lower > 400:
                        raise ValueError("Time range exceeds supported chart budget")
                    categories = list(range(lower, upper + 1))
            elif dimension == "phase":

                def phase_sort(label):
                    if label.startswith("Early"):
                        return (0, label)
                    if label.startswith("Phase "):
                        return (int(label[6]), label)
                    return (99, label)

                categories = sorted(totals, key=phase_sort)
            else:
                categories = sorted(totals, key=lambda value: (-totals[value], str(value)))
                clipping = {
                    "applied": len(categories) > plan.top_n,
                    "omitted_items": max(0, len(categories) - plan.top_n),
                }
                categories = categories[: plan.top_n]
            rows = []
            for cohort in plan.cohorts:
                for category in categories:
                    ids = groups[cohort.id].get(category, [])
                    ev_id = evidence(
                        cohort.id,
                        ids,
                        {"operation": plan.operation, "dimension": dimension, "category": category},
                    )
                    rows.append(
                        ChartRow(
                            id=f"{cohort.id}:{category}",
                            cohort=cohort.label,
                            category=str(category) if plan.operation == "category" else None,
                            year=category if plan.operation == "time_series" else None,
                            trial_count=len(ids),
                            contributor_count=len(ids),
                            evidence_set_id=ev_id,
                        )
                    )
            chart_type = (
                "time_series"
                if plan.operation == "time_series"
                else ("grouped_bar" if len(plan.cohorts) > 1 else "bar_chart")
            )
            visual = TabularVisualization(
                type=chart_type,
                title=plan.title,
                data=rows,
                encoding={
                    "x": encode(
                        "year" if plan.operation == "time_series" else "category", "ordinal", dimension
                    ),
                    "y": encode("trial_count", "quantitative", "Distinct trials"),
                    "series": encode("cohort", "nominal", "Cohort"),
                },
            )
        elif plan.operation == "histogram":
            values = [
                r["enrollment"]
                for records in records_by_cohort.values()
                for r in records
                if r.get("enrollment") is not None
            ]
            rows = []
            if values:
                lower, upper = min(values), max(values)
                width = max(1.0, (upper - lower) / plan.histogram_bins)
                for cohort in plan.cohorts:
                    buckets: dict[int, list[str]] = defaultdict(list)
                    for record in records_by_cohort[cohort.id]:
                        if record.get("enrollment") is None:
                            omitted_missing += 1
                            continue
                        bucket = min(plan.histogram_bins - 1, floor((record["enrollment"] - lower) / width))
                        buckets[bucket].append(record["nct_id"])
                    for bucket in range(plan.histogram_bins):
                        ids = buckets[bucket]
                        start, end = lower + bucket * width, lower + (bucket + 1) * width
                        rule = {
                            "operation": "histogram",
                            "field": "enrollment",
                            "bin_start": start,
                            "bin_end": end,
                            "end_inclusive": bucket == plan.histogram_bins - 1,
                        }
                        ev_id = evidence(cohort.id, ids, rule)
                        rows.append(
                            ChartRow(
                                id=f"{cohort.id}:bin:{bucket}",
                                cohort=cohort.label,
                                bin_start=start,
                                bin_end=end,
                                trial_count=len(ids),
                                contributor_count=len(ids),
                                evidence_set_id=ev_id,
                            )
                        )
            visual = TabularVisualization(
                type="histogram",
                title=plan.title,
                data=rows,
                encoding={
                    "x": encode("bin_start", "quantitative", "Enrollment (participants)"),
                    "x2": encode("bin_end", "quantitative", "Enrollment bin end"),
                    "y": encode("trial_count", "quantitative", "Distinct trials"),
                    "series": encode("cohort", "nominal", "Cohort"),
                },
            )
        else:
            cohort = plan.cohorts[0]
            matches = self.graph.network(datasets[cohort.id][0].dataset_id, cohort.filters, plan.relationship)
            merged = {}
            for match in matches:
                key = (match["source"], match["target"])
                if key not in merged:
                    merged[key] = {
                        **match,
                        "ids": set(),
                        "shared": {},
                        "source_ids": set(),
                        "target_ids": set(),
                    }
                target = merged[key]
                if plan.relationship == "sponsor_shared_drug":
                    target["ids"].update(match["left_contributors"] + match["right_contributors"])
                    target["source_ids"].update(match["left_contributors"])
                    target["target_ids"].update(match["right_contributors"])
                    target["shared"][match["shared_id"]] = match["shared_name"]
                else:
                    target["ids"].update(match["contributors"])
                    target["source_ids"].update(match["contributors"])
                    target["target_ids"].update(match["contributors"])

            def weight(item):
                return len(item["shared"]) if plan.relationship == "sponsor_shared_drug" else len(item["ids"])

            ordered = sorted(
                merged.values(), key=lambda item: (-weight(item), item["source"], item["target"])
            )
            limit = min(plan.top_n, self.settings.network_max_edges)
            clipping = {"applied": len(ordered) > limit, "omitted_items": max(0, len(ordered) - limit)}
            nodes = {}
            edges = []
            for match in ordered[:limit]:
                rule = {
                    "operation": "network",
                    "relationship": plan.relationship,
                    "source": match["source_label"],
                    "target": match["target_label"],
                }
                ev_id = evidence(cohort.id, match["ids"], rule)
                edges.append(
                    NetworkEdge(
                        id="edge_" + content_hash([match["source"], match["target"]])[:24],
                        source=match["source"],
                        target=match["target"],
                        weight=weight(match),
                        weight_metric="shared_intervention_count"
                        if plan.relationship == "sponsor_shared_drug"
                        else "distinct_trial_count",
                        shared_interventions=sorted(match["shared"].values()),
                        evidence_set_id=ev_id,
                        contributor_count=len(match["ids"]),
                    )
                )
                for side in ("source", "target"):
                    node_id = match[side]
                    if node_id not in nodes:
                        nodes[node_id] = {
                            "label": match[side + "_label"],
                            "kind": match[side + "_type"],
                            "ids": set(),
                        }
                    nodes[node_id]["ids"].update(match[side + "_ids"])
            node_models = []
            for node_id, item in sorted(nodes.items()):
                ev_id = evidence(
                    cohort.id,
                    item["ids"],
                    {"operation": "network_node", "node": item["label"], "scope": "displayed_edges"},
                )
                node_models.append(
                    NetworkNode(
                        id=node_id,
                        label=item["label"],
                        entity_type=item["kind"],
                        trial_count=len(item["ids"]),
                        evidence_set_id=ev_id,
                    )
                )
            visual = NetworkVisualization(
                title=plan.title,
                data=NetworkData(nodes=node_models, edges=edges),
                encoding={
                    "node_id": encode("id", "identifier", "Node ID"),
                    "node_label": encode("label", "nominal", "Entity"),
                    "node_type": encode("entity_type", "nominal", "Entity type"),
                    "source": encode("source", "identifier", "Source node ID"),
                    "target": encode("target", "identifier", "Target node ID"),
                    "weight": encode(
                        "weight",
                        "quantitative",
                        "Shared interventions"
                        if plan.relationship == "sponsor_shared_drug"
                        else "Distinct trials",
                    ),
                },
            )

        overlap = {}
        for index, left in enumerate(plan.cohorts):
            for right in plan.cohorts[index + 1 :]:
                left_ids = {r["nct_id"] for r in records_by_cohort[left.id]}
                right_ids = {r["nct_id"] for r in records_by_cohort[right.id]}
                overlap[left.id + ":" + right.id] = len(left_ids & right_ids)
        meta = {
            "cohorts": cohort_meta,
            "cohort_overlap_counts": overlap,
            "completeness": "complete",
            "source_semantics": "Official ClinicalTrials.gov condition/intervention/lead-sponsor search; other filters use snapshot fields.",
            "metric_definition": "Distinct NCT IDs per category; multi-valued categories may overlap.",
            "country_policy": "A country count includes studies listing any site there. Study-level RECRUITING does not imply each site is recruiting.",
            "phase_policy": "Combined phases remain one combined category. Missing phases are Not reported.",
            "date_policy": "Year filters always use study start year; time-series x-axis uses the declared time_field. Actual and estimated start dates are included.",
            "enrollment_policy": "Missing enrollment excluded; actual/estimated values retained and optionally filtered. Histogram bins are left-closed/right-open; last bin is right-closed.",
            "relationship_definition": plan.relationship if plan.operation == "network" else None,
            "relationship_limits": "Drug co-occurrence means same study, not same arm. Shared-drug sponsor edges indicate shared interventions, not collaboration or ownership.",
            "node_count_scope": "Union of supporting trials on displayed edges"
            if plan.operation == "network"
            else None,
            "presentation_clipping": clipping,
            "source_truncated": False,
            "excluded_missing_values": omitted_missing,
            "units": {"trial_count": "studies", "enrollment": "participants"},
            "sorting": "Years ascending; phases in clinical order; other categories by descending total count then label; network edges by descending weight then IDs.",
            "time_granularity": "year" if plan.operation == "time_series" else None,
            "assumptions": plan.assumptions,
        }
        previews = {}
        for evidence_id in sorted(evidence_ids):
            page = self.store.evidence_page(evidence_id, limit=self.settings.evidence_preview_limit)
            previews[evidence_id] = {
                "contributor_count": page["total_contributors"],
                "citations": page["citations"],
                "preview_complete": page["next_offset"] is None,
                "url": f"/evidence/{evidence_id}",
            }
        manifests = {snapshot.dataset_id: snapshot.model_dump() for snapshot, _ in datasets.values()}
        provenance = {
            "source": "ClinicalTrials.gov Data API",
            "datasets": list(manifests.values()),
            "evidence": previews,
        }
        empty = (
            not visual.data.edges
            if isinstance(visual, NetworkVisualization)
            else not any(row.trial_count for row in visual.data)
        )
        return visual, meta, provenance, empty
