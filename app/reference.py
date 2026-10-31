"""Independent raw-JSON oracle for exported results (no graph/engine imports).

This intentionally rederives every displayed item, including empty buckets and
top-N ties. Entity hashes implement the documented output identity contract only.
"""

from collections import defaultdict
from itertools import combinations
from math import floor

from app.sources import content_hash

PHASES = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not applicable",
}


def identity(kind, name):
    return kind + ":" + content_hash(" ".join(name.split()).casefold())[:24]


def categories(protocol, dimension, filters):
    design = protocol.get("designModule", {})
    status = protocol.get("statusModule", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    if dimension == "phase":
        phases = set(design.get("phases", []))
        return [
            " / ".join(PHASES.get(p, p) for p in sorted(phases, key=lambda p: list(PHASES).index(p)))
            if phases
            else "Not reported"
        ]
    if dimension == "country":
        values = {
            v["country"]
            for v in protocol.get("contactsLocationsModule", {}).get("locations", [])
            if v.get("country")
        }
        if filters["countries"]:
            return sorted(values & set(filters["countries"]))
        return sorted(values) or ["Not reported"]
    if dimension == "intervention_type":
        return sorted(
            {
                v.get("type", "UNKNOWN")
                for v in protocol.get("armsInterventionsModule", {}).get("interventions", [])
                if v.get("name")
            }
        ) or ["Not reported"]
    if dimension in ("start_year", "first_posted_year"):
        field = "startDateStruct" if dimension == "start_year" else "studyFirstPostDateStruct"
        date = status.get(field, {}).get("date", "")
        return [int(date[:4]) if date else None]
    return [
        {
            "status": status.get("overallStatus", "Not reported"),
            "sponsor": sponsor.get("name", "Not reported"),
            "sponsor_class": sponsor.get("class", "Not reported"),
            "study_type": design.get("studyType", "Not reported"),
        }[dimension]
    ]


def expected_table(plan, records):
    operation = plan["operation"]
    rows, groups, totals = [], {}, defaultdict(int)
    missing = 0
    clipped = 0
    if operation in ("category", "time_series"):
        dimension = plan["group_by"] if operation == "category" else plan["time_field"]
        for cohort in plan["cohorts"]:
            grouped = defaultdict(set)
            for raw in records[cohort["label"]]:
                p = raw["protocolSection"]
                for category in categories(p, dimension, cohort["filters"]):
                    grouped[category].add(p["identificationModule"]["nctId"])
            missing += len(grouped.pop(None, set()))
            groups[cohort["id"]] = grouped
            for category, ids in grouped.items():
                totals[category] += len(ids)
        if operation == "time_series":
            order = sorted(totals)
            if order:
                lo, hi = min(order), max(order)
                if dimension == "start_year":
                    lo = min(
                        c["filters"]["start_year"] if c["filters"]["start_year"] is not None else lo
                        for c in plan["cohorts"]
                    )
                    hi = max(
                        c["filters"]["end_year"] if c["filters"]["end_year"] is not None else hi
                        for c in plan["cohorts"]
                    )
                order = list(range(lo, hi + 1))
        elif dimension == "phase":
            order = sorted(
                totals,
                key=lambda p: (
                    0 if p.startswith("Early") else int(p[6]) if p.startswith("Phase ") else 99,
                    p,
                ),
            )
        else:
            order = sorted(totals, key=lambda p: (-totals[p], str(p)))
            clipped = max(0, len(order) - plan["top_n"])
            order = order[: plan["top_n"]]
        for c in plan["cohorts"]:
            for category in order:
                rows.append(
                    {
                        "id": f"{c['id']}:{category}",
                        "cohort": c["label"],
                        "category": str(category) if operation == "category" else None,
                        "year": category if operation == "time_series" else None,
                        "bin_start": None,
                        "bin_end": None,
                        "ids": groups[c["id"]].get(category, set()),
                    }
                )
    else:
        values = [
            r["protocolSection"].get("designModule", {}).get("enrollmentInfo", {}).get("count")
            for rs in records.values()
            for r in rs
        ]
        missing = sum(v is None for v in values)
        values = [v for v in values if v is not None]
        if values:
            lo, hi = min(values), max(values)
            width = max(1.0, (hi - lo) / plan["histogram_bins"])
            for c in plan["cohorts"]:
                buckets = defaultdict(set)
                for raw in records[c["label"]]:
                    p = raw["protocolSection"]
                    value = p.get("designModule", {}).get("enrollmentInfo", {}).get("count")
                    if value is not None:
                        buckets[min(plan["histogram_bins"] - 1, floor((value - lo) / width))].add(
                            p["identificationModule"]["nctId"]
                        )
                for index in range(plan["histogram_bins"]):
                    rows.append(
                        {
                            "id": f"{c['id']}:bin:{index}",
                            "cohort": c["label"],
                            "category": None,
                            "year": None,
                            "bin_start": lo + index * width,
                            "bin_end": lo + (index + 1) * width,
                            "ids": buckets[index],
                        }
                    )
    return rows, {"applied": bool(clipped), "omitted_items": clipped}, missing


def expected_network(plan, records, limit):
    relationship = plan["relationship"]
    entities, edges, sponsors = {}, {}, defaultdict(lambda: defaultdict(set))
    for raw in next(iter(records.values())):
        p = raw["protocolSection"]
        nct = p["identificationModule"]["nctId"]
        name = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name")
        sponsor = identity("sponsor", name) if name else None
        if sponsor:
            entities[sponsor] = (name, "sponsor")
        drugs = set()
        for intervention in p.get("armsInterventionsModule", {}).get("interventions", []):
            if intervention.get("type") not in ("DRUG", "BIOLOGICAL") or not intervention.get("name"):
                continue
            key = identity("intervention:" + intervention["type"], intervention["name"])
            entities[key] = (intervention["name"], "intervention")
            drugs.add(key)
            if sponsor:
                sponsors[sponsor][key].add(nct)
        pairs = (
            [(sponsor, d) for d in drugs]
            if relationship == "sponsor_drug" and sponsor
            else list(combinations(sorted(drugs), 2))
            if relationship == "drug_cooccurrence"
            else []
        )
        for pair in pairs:
            if pair not in edges:
                edges[pair] = {"ids": set(), "left": set(), "right": set(), "shared": set()}
            for role in ("ids", "left", "right"):
                edges[pair][role].add(nct)
    if relationship == "sponsor_shared_drug":
        for left, right in combinations(sorted(sponsors), 2):
            common = set(sponsors[left]) & set(sponsors[right])
            if common:
                a = set().union(*(sponsors[left][k] for k in common))
                b = set().union(*(sponsors[right][k] for k in common))
                edges[left, right] = {"ids": a | b, "left": a, "right": b, "shared": common}
    for edge in edges.values():
        edge["weight"] = len(edge["shared"] if relationship == "sponsor_shared_drug" else edge["ids"])
    ordered = sorted(edges, key=lambda pair: (-edges[pair]["weight"], *pair))
    clipped = max(0, len(ordered) - limit)
    ordered = ordered[:limit]
    nodes = defaultdict(set)
    for pair in ordered:
        nodes[pair[0]].update(edges[pair]["left"])
        nodes[pair[1]].update(edges[pair]["right"])
    return (
        [(pair, edges[pair]) for pair in ordered],
        nodes,
        entities,
        {"applied": bool(clipped), "omitted_items": clipped},
    )
