"""Independent SQLite checks over frozen raw records; imports no product aggregation code."""

import calendar
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date


def identity(kind, name):
    value = json.dumps(" ".join(name.split()).casefold(), ensure_ascii=False, separators=(",", ":"))
    return kind + ":" + hashlib.sha256(value.encode()).hexdigest()[:24]


def database(records):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE trial(nct TEXT PRIMARY KEY, raw TEXT)")
    db.execute("CREATE TABLE exposure(nct TEXT, sponsor TEXT, drug TEXT, PRIMARY KEY(nct,sponsor,drug))")
    for raw in records:
        protocol = raw["protocolSection"]
        nct = protocol["identificationModule"]["nctId"]
        db.execute("INSERT INTO trial VALUES(?,?)", (nct, json.dumps(protocol)))
        name = protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name")
        if name:
            sponsor = identity("sponsor", name)
            for drug in protocol.get("armsInterventionsModule", {}).get("interventions", []):
                if drug.get("type") in ("DRUG", "BIOLOGICAL") and drug.get("name"):
                    db.execute(
                        "INSERT OR IGNORE INTO exposure VALUES(?,?,?)",
                        (nct, sponsor, identity("intervention:" + drug["type"], drug["name"])),
                    )
    return db


def category_counts(records, group, countries=None):
    db = database(records)
    try:
        paths = {
            "status": "$.statusModule.overallStatus",
            "study_type": "$.designModule.studyType",
            "sponsor": "$.sponsorCollaboratorsModule.leadSponsor.name",
        }
        if group in paths:
            return dict(
                db.execute(
                    "SELECT coalesce(json_extract(raw,?), 'Not reported'),count(*) FROM trial GROUP BY 1",
                    (paths[group],),
                )
            )
        if group == "country":
            values = dict(
                db.execute("""SELECT category,count(DISTINCT nct) FROM (
                SELECT DISTINCT nct,json_extract(value,'$.country') category
                FROM trial,json_each(raw,'$.contactsLocationsModule.locations')
                WHERE coalesce(json_extract(value,'$.country'),'')<>''
                UNION
                SELECT nct,'Not reported' FROM trial t WHERE NOT EXISTS (
                    SELECT 1 FROM json_each(t.raw,'$.contactsLocationsModule.locations')
                    WHERE coalesce(json_extract(value,'$.country'),'')<>''
                )
            ) GROUP BY category""")
            )
            return {k: v for k, v in values.items() if not countries or k in countries}
        if group == "phase":
            mapping = {
                "EARLY_PHASE1": "Early Phase 1",
                "PHASE1": "Phase 1",
                "PHASE2": "Phase 2",
                "PHASE3": "Phase 3",
                "PHASE4": "Phase 4",
                "NA": "Not applicable",
            }
            # Phase arrays represent one combined category, not multiple study counts.
            counts = Counter()
            for (value,) in db.execute("SELECT json_extract(raw,'$.designModule.phases') FROM trial"):
                phases = set(json.loads(value)) if value else set()
                label = " / ".join(mapping[p] for p in mapping if p in phases) or "Not reported"
                counts[label] += 1
            return dict(counts)
        raise ValueError("SQL oracle does not cover this category")
    finally:
        db.close()


def sponsor_edges(records):
    db = database(records)
    try:
        return {
            (a, b): n
            for a, b, n in db.execute("""
            SELECT a.sponsor,b.sponsor,count(DISTINCT a.drug)
            FROM exposure a JOIN exposure b ON a.drug=b.drug AND a.sponsor<b.sponsor
            GROUP BY a.sponsor,b.sponsor ORDER BY 3 DESC,1,2
        """)
        }
    finally:
        db.close()


def expected_scope_ids(scope, records):
    """Re-derive scope membership from the complete parent, not the selected output."""
    filters = scope["filters"]
    found = set()
    for raw in records:
        p = raw["protocolSection"]
        design, status = p.get("designModule", {}), p.get("statusModule", {})
        if filters.get("phases") and not set(filters["phases"]) & set(design.get("phases", [])):
            continue
        if filters.get("statuses") and status.get("overallStatus") not in filters["statuses"]:
            continue
        countries = {r.get("country") for r in p.get("contactsLocationsModule", {}).get("locations", [])}
        if filters.get("countries") and not set(filters["countries"]) & countries:
            continue
        if (
            filters.get("enrollment_type")
            and design.get("enrollmentInfo", {}).get("type") != filters["enrollment_type"]
        ):
            continue
        start = status.get("startDateStruct", {}).get("date", "")
        year = int(start[:4]) if len(start) >= 4 and start[:4].isdigit() else None
        if filters.get("start_year") is not None and (year is None or year < filters["start_year"]):
            continue
        if filters.get("end_year") is not None and (year is None or year > filters["end_year"]):
            continue
        bound = scope.get("date_range")
        if bound:
            key = {
                "start": "startDateStruct",
                "completion": "completionDateStruct",
                "primary_completion": "primaryCompletionDateStruct",
                "first_posted": "studyFirstPostDateStruct",
            }[bound["field"]]
            value = status.get(key, {}).get("date", "")
            try:
                pieces = [int(v) for v in value.split("-")]
                if len(pieces) not in (1, 2, 3):
                    continue
                y = pieces[0]
                m = pieces[1] if len(pieces) > 1 else 1
                low = date(y, m, pieces[2] if len(pieces) > 2 else 1)
                high = (
                    low
                    if len(pieces) == 3
                    else date(y, m, calendar.monthrange(y, m)[1])
                    if len(pieces) == 2
                    else date(y, 12, 31)
                )
            except (ValueError, IndexError):
                continue
            if bound.get("lower") and low < date.fromisoformat(str(bound["lower"])):
                continue
            if bound.get("upper") and high > date.fromisoformat(str(bound["upper"])):
                continue
        lineage = scope.get("lineage", [])
        name = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name")
        if any(item.get("selection") != "top_shared_drug_sponsors" for item in lineage):
            raise ValueError("Unrecognized scope lineage in independent oracle")
        if lineage and (not name or any(identity("sponsor", name) != item["entity_id"] for item in lineage)):
            continue
        found.add(p["identificationModule"]["nctId"])
    return found


def verify_sql_artifact(artifact, registry, state):
    params = artifact.metadata.get("analysis_parameters", {})
    operation = params.get("operation")
    scopes = [registry.scope(h, state) for h in artifact.metadata["root_scopes"]]
    for scope, _, _ in scopes:
        _, original = registry.service.snapshots.load(scope["dataset_id"])
        if set(scope["nct_ids"]) != expected_scope_ids(scope, original):
            raise ValueError("Independent raw parent scope membership differs from selected records")
    if operation not in ("category", "network"):
        return {
            "checked": True,
            "oracle": "independent_parent_scope_membership",
            "numeric_oracle": "Independent raw-date/scatter/interval verifier covers this operation",
        }
    if operation == "network":
        if params["relationship"] != "sponsor_shared_drug":
            return {"checked": False, "reason": "Not part of core300 SQL graph oracle"}
        expected = sponsor_edges(scopes[0][2])
        ordered = sorted(expected, key=lambda pair: (-expected[pair], *pair))[
            : min(params["top_n"], registry.settings.network_max_edges)
        ]
        actual = artifact.visualization["data"]["edges"]
        if [(row["source"], row["target"], row["weight"]) for row in actual] != [
            (a, b, expected[a, b]) for a, b in ordered
        ]:
            raise ValueError("Independent SQL shared-drug edge weights or complete edge set disagree")
    else:
        expected = {}
        totals = Counter()
        for index, (scope, _, raw) in enumerate(scopes):
            label = params["labels"][index] if params["labels"] else scope.get("label", f"Cohort {index + 1}")
            expected[label] = category_counts(raw, params["group_by"], scope["filters"].get("countries"))
            totals.update(expected[label])
        categories = (
            set(totals)
            if params["group_by"] == "phase"
            else set(sorted(totals, key=lambda k: (-totals[k], k))[: params["top_n"]])
        )
        target = {
            (label, category): counts.get(category, 0)
            for label, counts in expected.items()
            for category in categories
        }
        rows = artifact.visualization["data"]
        actual = {(r["cohort"], r["category"]): r["trial_count"] for r in rows}
        if len(actual) != len(rows) or actual != target:
            raise ValueError("Independent SQL category counts or complete item set disagree")
    return {"checked": True, "oracle": "independent_sqlite_raw_json", "operation": operation}
