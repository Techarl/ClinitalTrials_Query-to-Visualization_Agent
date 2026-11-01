"""V2 temporal/scatter calculations and public quality advisories."""

from collections import Counter, defaultdict

from app.analysis import record_citation
from app.sources import content_hash
from app.v2.contracts import Advisory
from app.v2.dates import FIELDS, numeric_value, source_date


def full_citation(dataset_id, raw):
    citation = record_citation(dataset_id, raw)
    known = {f["path"] for f in citation["fields"]}
    status = raw.get("protocolSection", {}).get("statusModule", {})
    for key in FIELDS.values():
        path = "/protocolSection/statusModule/" + key
        if key in status and path not in known:
            citation["fields"].append({"path": path, "value": status[key]})
        elif key not in status and path not in citation["missing_fields"]:
            citation["missing_fields"].append(path)
    return citation


def temporal_or_scatter(args, scopes, store, run_id):
    rows, evidence_ids, cohort_meta = [], [], []
    for index, (scope, snapshot, raw_records) in enumerate(scopes):
        label = args.labels[index] if args.labels else scope.get("label", f"Cohort {index + 1}")
        groups = defaultdict(list)
        excluded = Counter()
        types = Counter()

        def evidence(raws, rule):
            evidence_id = store.evidence(
                run_id, [full_citation(snapshot.dataset_id, raw) for raw in raws], rule
            )
            evidence_ids.append(evidence_id)
            return evidence_id

        for raw in raw_records:
            nct = raw["protocolSection"]["identificationModule"]["nctId"]
            if args.operation == "scatter":
                x, xtype, xerror = numeric_value(raw, args.x)
                y, ytype, yerror = numeric_value(raw, args.y)
                if xerror or yerror:
                    excluded[xerror or yerror] += 1
                    continue
                types[f"{xtype}/{ytype}"] += 1
                rows.append(
                    {
                        "item_id": content_hash([index, nct])[:20],
                        "nct_id": nct,
                        "cohort": label,
                        "x": x,
                        "y": y,
                        "x_type": xtype,
                        "y_type": ytype,
                        "evidence_set_id": evidence(
                            [raw],
                            {
                                "operation": "scatter",
                                "x": args.x,
                                "y": args.y,
                                "duration_unit": "days",
                                "duration_policy": "exact_days_only",
                            },
                        ),
                    }
                )
            else:
                value = source_date(raw, args.time_field)
                if value["lower"] is None or (args.time_grain == "month" and value["precision"] == "year"):
                    excluded["missing_or_insufficient_date_precision"] += 1
                    continue
                key = value["raw"][: 4 if args.time_grain == "year" else 7]
                groups[key].append(raw)
                types[value["type"] or "UNSPECIFIED"] += 1
        if args.operation == "time_series":
            for key, values in sorted(groups.items()):
                rows.append(
                    {
                        "item_id": content_hash([index, key])[:20],
                        "category": key,
                        "cohort": label,
                        "trial_count": len(values),
                        "evidence_set_id": evidence(
                            values,
                            {
                                "operation": "time_series",
                                "field": args.time_field,
                                "grain": args.time_grain,
                                "period": key,
                            },
                        ),
                    }
                )
        cohort_meta.append(
            {
                "label": label,
                "selected_trial_count": len(raw_records),
                "valid_count": len(raw_records) - sum(excluded.values()),
                "excluded_count": sum(excluded.values()),
                "excluded_reasons": dict(excluded),
                "value_types": dict(types),
            }
        )
    if args.operation == "time_series":
        # Explicit zero buckets and a common x axis make group comparisons honest.
        keys = sorted({row["category"] for row in rows})
        if keys:
            if args.time_grain == "year":
                keys = [str(year) for year in range(int(keys[0]), int(keys[-1]) + 1)]
            else:
                lo, hi = keys[0].split("-"), keys[-1].split("-")
                lower, upper = int(lo[0]) * 12 + int(lo[1]) - 1, int(hi[0]) * 12 + int(hi[1]) - 1
                if upper - lower > 4800:
                    raise ValueError("Monthly chart exceeds the supported date span")
                keys = [f"{n // 12:04d}-{n % 12 + 1:02d}" for n in range(lower, upper + 1)]
        present = {(row["cohort"], row["category"]) for row in rows}
        for cohort in cohort_meta:
            for key in keys:
                if (cohort["label"], key) not in present:
                    ev = store.evidence(
                        run_id,
                        [],
                        {
                            "operation": "time_series",
                            "field": args.time_field,
                            "grain": args.time_grain,
                            "period": key,
                            "cohort": cohort["label"],
                        },
                    )
                    evidence_ids.append(ev)
                    rows.append(
                        {
                            "item_id": content_hash([cohort["label"], key])[:20],
                            "category": key,
                            "cohort": cohort["label"],
                            "trial_count": 0,
                            "evidence_set_id": ev,
                        }
                    )
        rows.sort(key=lambda row: (row["cohort"], row["category"]))
    visual = {
        "type": "scatter" if args.operation == "scatter" else "time_series",
        "title": args.title,
        "data": rows,
        "encoding": {
            "x": {
                "field": "x" if args.operation == "scatter" else "category",
                "title": args.x
                if args.operation == "scatter"
                else args.time_field + " (" + args.time_grain + ")",
            },
            "y": {
                "field": "y" if args.operation == "scatter" else "trial_count",
                "title": args.y if args.operation == "scatter" else "Distinct trials",
            },
            "series": {"field": "cohort", "title": "Cohort"},
        },
    }
    meta = {
        "cohorts": cohort_meta,
        "time_field": args.time_field,
        "time_granularity": args.time_grain,
        "duration_policy": "Exact calendar days from start to completion; negative or imprecise dates excluded.",
        "date_filter_policy": "A source date interval must be fully contained in the requested range.",
        "presentation_clipping": {"applied": False, "omitted_items": 0},
        "metric_definition": "One point per trial with valid x/y values"
        if args.operation == "scatter"
        else "Distinct trials by source date period",
        "units": {"trial_duration": "days", "enrollment": "participants"},
        "analysis_parameters": args.model_dump(),
    }
    return visual, meta, evidence_ids


def advisories(visual, meta):
    found = []

    def add(code, message, threshold=None):
        found.append(Advisory(code=code, message=message, threshold=threshold))

    if not visual:
        add("empty", "No valid data remain for this chart.")
        return found
    data, kind = visual["data"], visual["type"]
    if isinstance(data, list):
        categories = {str(row.get("category")) for row in data}
        if kind in ("bar_chart", "grouped_bar") and len(categories) == 1:
            add(
                "single_category",
                "Only one category is present; a count may communicate this more clearly.",
                1,
            )
        if kind == "time_series" and len(categories) < 2:
            add("short_trend", "Fewer than two periods cannot establish a trend.", 2)
        if kind == "scatter" and len(data) < 5:
            add("few_points", "Fewer than five valid points; avoid drawing a correlation conclusion.", 5)
        counts = [row.get("trial_count", 0) for row in data]
        if counts and sum(counts) and max(counts) / sum(counts) >= 0.9:
            add("concentrated", "At least 90% of displayed category memberships are in one item.", 0.9)
    elif kind == "network_graph":
        edge_count, node_count = len(data["edges"]), len(data["nodes"])
        if edge_count < 2:
            add("sparse_network", "Fewer than two relationships in the displayed network.", 2)
        if node_count > 1 and edge_count / (node_count * (node_count - 1) / 2) > 0.7:
            add(
                "dense_network",
                "Many nodes are connected; inspect individual edges and their definitions.",
                0.7,
            )
    for cohort in meta.get("cohorts", []):
        total = cohort.get("selected_trial_count", 0)
        missing = cohort.get(
            "excluded_count", cohort.get("missing_enrollment", 0) if kind == "histogram" else 0
        )
        if total == 0:
            add("empty_cohort", f"The cohort {cohort.get('label', '')} has no records.")
        elif missing / total >= 0.3:
            add(
                "missing_values",
                f"The cohort {cohort.get('label', '')} excludes at least 30% of records for missing or incompatible values.",
                0.3,
            )
    return found
