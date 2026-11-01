"""Date precision is source data: never invent a day or month."""

import calendar
import re
from datetime import date

FIELDS = {
    "start": "startDateStruct",
    "primary_completion": "primaryCompletionDateStruct",
    "completion": "completionDateStruct",
    "first_posted": "studyFirstPostDateStruct",
}


def source_date(raw, field):
    entry = raw.get("protocolSection", {}).get("statusModule", {}).get(FIELDS[field], {})
    value = entry.get("date")
    result = {"raw": value, "type": entry.get("type"), "precision": "missing", "lower": None, "upper": None}
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return result
    try:
        parts = list(map(int, value.split("-")))
        year = parts[0]
        if len(parts) == 1:
            lower, upper, precision = date(year, 1, 1), date(year, 12, 31), "year"
        elif len(parts) == 2:
            month = parts[1]
            lower, upper, precision = (
                date(year, month, 1),
                date(year, month, calendar.monthrange(year, month)[1]),
                "month",
            )
        else:
            lower = upper = date(*parts)
            precision = "day"
        result.update(lower=lower.isoformat(), upper=upper.isoformat(), precision=precision)
    except ValueError:
        result["precision"] = "invalid"
    return result


def in_range(raw, limits):
    if limits is None:
        return True
    value = source_date(raw, limits.field)
    return bool(
        value["lower"]
        and (limits.lower is None or value["lower"] >= limits.lower.isoformat())
        and (limits.upper is None or value["upper"] <= limits.upper.isoformat())
    )


def numeric_value(raw, metric):
    if metric == "enrollment":
        entry = raw.get("protocolSection", {}).get("designModule", {}).get("enrollmentInfo", {})
        value = entry.get("count")
        return (
            (value, entry.get("type"), None)
            if type(value) is int and value >= 0
            else (None, entry.get("type"), "missing_enrollment")
        )
    if metric in ("start_year", "completion_year"):
        entry = source_date(raw, metric.removesuffix("_year"))
        return (
            (int(entry["lower"][:4]), entry["type"], None)
            if entry["lower"]
            else (None, entry["type"], "missing_" + metric)
        )
    start, end = source_date(raw, "start"), source_date(raw, "completion")
    kind = "ACTUAL" if start["type"] == end["type"] == "ACTUAL" else "ESTIMATED_OR_UNSPECIFIED"
    if start["precision"] != "day" or end["precision"] != "day":
        return None, kind, "duration_requires_exact_start_and_completion_days"
    duration = (date.fromisoformat(end["lower"]) - date.fromisoformat(start["lower"])).days
    return (duration, kind, None) if duration >= 0 else (None, kind, "negative_duration")
