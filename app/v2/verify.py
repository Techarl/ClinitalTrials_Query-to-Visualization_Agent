"""Independent raw-field oracle for the new date and scatter contracts."""

from collections import Counter, defaultdict
from datetime import date


def verify_extended(artifact, scopes, evidence):
    args = artifact.metadata["analysis_parameters"]
    rows = artifact.visualization["data"]
    expected = {}
    missing = {}
    date_fields = {
        "start": "startDateStruct",
        "primary_completion": "primaryCompletionDateStruct",
        "completion": "completionDateStruct",
        "first_posted": "studyFirstPostDateStruct",
    }

    def number(protocol, axis):
        if axis == "enrollment":
            value = protocol.get("designModule", {}).get("enrollmentInfo", {}).get("count")
            return value if type(value) is int and value >= 0 else None
        status = protocol.get("statusModule", {})
        if axis.endswith("_year"):
            key = "startDateStruct" if axis == "start_year" else "completionDateStruct"
            value = status.get(key, {}).get("date", "")
            try:
                return date.fromisoformat(
                    value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else "")
                ).year
            except ValueError:
                return None
        start, end = (
            status.get("startDateStruct", {}).get("date", ""),
            status.get("completionDateStruct", {}).get("date", ""),
        )
        if len(start) != 10 or len(end) != 10:
            return None
        try:
            value = (date.fromisoformat(end) - date.fromisoformat(start)).days
            return value if value >= 0 else None
        except ValueError:
            return None

    for index, (scope, _, records) in enumerate(scopes):
        label = args["labels"][index] if args["labels"] else scope.get("label", f"Cohort {index + 1}")
        dropped = 0
        groups = defaultdict(set)
        for raw in records:
            p = raw["protocolSection"]
            nct = p["identificationModule"]["nctId"]
            if args["operation"] == "scatter":
                x, y = number(p, args["x"]), number(p, args["y"])
                if x is None or y is None:
                    dropped += 1
                else:
                    expected[label, nct] = (x, y)
            else:
                value = p.get("statusModule", {}).get(date_fields[args["time_field"]], {}).get("date", "")
                try:
                    if len(value) not in (4, 7, 10) or (args["time_grain"] == "month" and len(value) < 7):
                        raise ValueError()
                    date.fromisoformat(
                        value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else "")
                    )
                    groups[value[: 4 if args["time_grain"] == "year" else 7]].add(nct)
                except ValueError:
                    dropped += 1
        if args["operation"] == "time_series":
            expected.update({(label, key): ids for key, ids in groups.items()})
        missing[label] = dropped
    seen = Counter()
    periods = sorted({key[1] for key in expected}) if args["operation"] == "time_series" else []
    if args["operation"] == "time_series":
        for scope, _, _ in scopes:
            bounds = scope.get("date_range") or {}
            if bounds.get("field") == args["time_field"]:
                for name in ("lower", "upper"):
                    if bounds.get(name):
                        d = date.fromisoformat(str(bounds[name]))
                        periods.append(str(d.year) if args["time_grain"] == "year" else d.strftime("%Y-%m"))
            if args["time_field"] == "start":
                for name, month in (("start_year", 1), ("end_year", 12)):
                    year = scope.get("filters", {}).get(name)
                    if year is not None:
                        periods.append(
                            str(year) if args["time_grain"] == "year" else f"{year:04d}-{month:02d}"
                        )
        periods = sorted(set(periods))
    if periods:
        if args["time_grain"] == "year":
            periods = [str(year) for year in range(int(periods[0]), int(periods[-1]) + 1)]
        else:
            lo_y, lo_m = map(int, periods[0].split("-"))
            hi_y, hi_m = map(int, periods[-1].split("-"))
            periods = [
                f"{n // 12:04d}-{n % 12 + 1:02d}" for n in range(lo_y * 12 + lo_m - 1, hi_y * 12 + hi_m)
            ]
        for label in missing:
            for period in periods:
                expected.setdefault((label, period), set())
    for row in rows:
        key = row["cohort"], row["nct_id"] if args["operation"] == "scatter" else row["category"]
        seen[key] += 1
        if key not in expected:
            raise ValueError("Unexpected result point")
        if args["operation"] == "scatter":
            if (row["x"], row["y"]) != expected[key] or evidence[row["evidence_set_id"]] != {row["nct_id"]}:
                raise ValueError("Scatter point or source mismatch")
        elif row["trial_count"] != len(expected[key]) or evidence[row["evidence_set_id"]] != expected[key]:
            raise ValueError("Time period count or contributor mismatch")
    if seen != Counter({key: 1 for key in expected}):
        raise ValueError("Missing or duplicated result points")
    for cohort in artifact.metadata["cohorts"]:
        if cohort["excluded_count"] != missing[cohort["label"]]:
            raise ValueError("Excluded count mismatch")
