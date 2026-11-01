"""Explicit official-count overview, with sample evidence labelled separately."""

from typing import get_args

from app.models import Status
from app.sources import ClinicalTrialsClient, source_query, utc_now
from app.v2.charts import full_citation
from app.v2.contracts import Artifact, Coverage, OverviewInput


def advanced_filters(filters):
    clauses = []
    for field, area in [("phases", "Phase"), ("countries", "LocationCountry")]:
        values = getattr(filters, field)
        if values:
            if any('"' in value or "\\" in value for value in values):
                raise ValueError("Unsupported source-filter syntax")
            clauses.append("(" + " OR ".join(f'AREA[{area}]"{value}"' for value in values) + ")")
    if filters.start_year is not None or filters.end_year is not None:
        lower = str(filters.start_year) + "-01-01" if filters.start_year else "MIN"
        upper = str(filters.end_year) + "-12-31" if filters.end_year else "MAX"
        clauses.append(f"AREA[StartDate]RANGE[{lower},{upper}]")
    if filters.enrollment_type:
        clauses.append(f"AREA[EnrollmentType]{filters.enrollment_type}")
    return clauses


def register_overview(registry):
    def execute(args, state):
        task = state["task"]
        if task["source_mode"] != "aggregate_overview":
            raise ValueError("Official-count mode requires explicit user selection")
        registry.goal(args.goal_id, state, operation="category")
        if task.get("date_range"):
            raise ValueError(
                "Overview supports legacy start-year bounds; precise date-interval semantics require full evidence"
            )
        if args.filters.model_dump() != task["filters"]:
            raise ValueError("Overview must preserve all task filters")
        query = source_query(args.filters)
        base_clauses = advanced_filters(args.filters)
        if args.filters.statuses:
            query["filter.overallStatus"] = ",".join(args.filters.statuses)
        area = "OverallStatus" if args.dimension == "status" else "StudyType"
        values = (
            list(get_args(Status))
            if args.dimension == "status"
            else ["INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS"]
        )
        predicates = [(value, f"AREA[{area}]{value}") for value in values]
        predicates.append(
            ("Not reported / other", "NOT (" + " OR ".join(predicate for _, predicate in predicates) + ")")
        )
        client = ClinicalTrialsClient(registry.settings)
        receipts, rows, evidence_ids = [], [], []
        try:
            before = client.get("/version", None, registry.deadline(state))
            root_query = {**query, "format": "json", "pageSize": 1, "countTotal": "true"}
            if base_clauses:
                root_query["filter.advanced"] = " AND ".join(base_clauses)
            root = client.get("/studies", root_query, registry.deadline(state))
            receipts.append({"query": root_query, "at": utc_now(), "raw": root, "kind": "root_count"})
            for category, predicate in predicates:
                params = {
                    **query,
                    "filter.advanced": " AND ".join([*base_clauses, predicate]),
                    "format": "json",
                    "pageSize": 3,
                    "countTotal": "true",
                }
                raw = client.get("/studies", params, registry.deadline(state))
                count = raw.get("totalCount")
                if type(count) is not int or count < 0:
                    raise ValueError("A required official bucket count failed")
                receipt_id = registry.store.asset(
                    state["session_id"],
                    state["run_id"],
                    "count",
                    {"query": params, "at": utc_now(), "raw": raw},
                )
                receipts.append({"receipt_id": receipt_id, "category": category, "count": count})
                evidence_id = registry.store.base.evidence(
                    state["run_id"],
                    [full_citation(receipt_id, record) for record in raw.get("studies", [])],
                    {"evidence_mode": "sample_only", "official_count_receipt": receipt_id},
                )
                evidence_ids.append(evidence_id)
                rows.append(
                    {
                        "category": category,
                        "trial_count": count,
                        "cohort": "Official overview",
                        "evidence_set_id": evidence_id,
                        "count_receipt_id": receipt_id,
                        "sample_count": len(raw.get("studies", [])),
                    }
                )
            after = client.get("/version", None, registry.deadline(state))
            if not before.get("dataTimestamp") or before["dataTimestamp"] != after.get("dataTimestamp"):
                raise ValueError("Source version changed across official bucket counts")
            if (
                type(root.get("totalCount")) is not int
                or sum(row["trial_count"] for row in rows) != root["totalCount"]
            ):
                raise ValueError("Mutually exclusive overview buckets do not cover the official population")
        finally:
            client.close()
        artifact = Artifact(
            artifact_id="",
            result_id="",
            goal_id=args.goal_id,
            source_type="clinicaltrials",
            title=f"Official counts by {args.dimension}",
            metric="Official totalCount per mutually exclusive bucket",
            coverage=Coverage(
                statistics_basis="official_counts",
                count_exactness="exact",
                population_coverage="count_only",
                evidence_coverage="sample_only",
                fetched_count=sum(row["sample_count"] for row in rows),
                matched_count=root["totalCount"],
            ),
            visualization={
                "type": "bar_chart",
                "title": f"Official counts by {args.dimension}",
                "data": rows,
                "encoding": {
                    "x": {"field": "category", "title": args.dimension},
                    "y": {"field": "trial_count", "title": "Official trial count"},
                },
            },
            evidence_ids=evidence_ids,
            metadata={
                "analysis_parameters": {"operation": "category", "group_by": args.dimension},
                "receipts": receipts,
                "source_version": after,
                "filters": args.filters.model_dump(),
                "category_policy": "Single-valued status/study-type buckets plus explicit complement; sum checked against root count.",
                "presentation_clipping": {"applied": False, "omitted_items": 0},
            },
            alternative_renderers=["bar", "table"],
        )
        return registry.publish(artifact, state, {"receipts": receipts})

    registry.register(
        "official_count_overview",
        "Explicit aggregate_overview mode only: exact official status/study-type counts with at most three SAMPLE citations per bucket. Never claim complete contributors.",
        OverviewInput,
        execute,
    )
