"""Snapshot-scoped Neo4j imports and parameterized, allowlisted queries."""

import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from neo4j import GraphDatabase, Query

from app.config import Settings
from app.models import Filters
from app.sources import Snapshot, content_hash

_deadline: ContextVar[float | None] = ContextVar("graph_deadline", default=None)

PHASE_LABELS = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not applicable",
}
PHASE_ORDER = {name: rank for rank, name in enumerate(PHASE_LABELS)}


def entity_key(kind: str, name: str) -> str:
    normalized = " ".join(name.split()).casefold()
    return kind + ":" + content_hash(normalized)[:24]


def year_of(value: str | None) -> int | None:
    return int(value[:4]) if value and re.match(r"^\d{4}(?:-|$)", value) else None


def normalize(study: dict, dataset_id: str) -> dict:
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    design = protocol.get("designModule", {})
    status = protocol.get("statusModule", {})
    enrollment = design.get("enrollmentInfo", {})
    sponsor = protocol.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
    phases = sorted(set(design.get("phases", [])), key=lambda p: PHASE_ORDER.get(p, 99))
    interventions = []
    for item in protocol.get("armsInterventionsModule", {}).get("interventions", []):
        name, kind = item.get("name"), item.get("type", "UNKNOWN")
        if name:
            interventions.append(
                {"key": entity_key("intervention:" + kind, name), "name": name, "kind": kind}
            )
    countries = sorted(
        {
            loc["country"]
            for loc in protocol.get("contactsLocationsModule", {}).get("locations", [])
            if loc.get("country")
        }
    )
    conditions = protocol.get("conditionsModule", {}).get("conditions", [])
    nct_id = identification["nctId"]
    return {
        "props": {
            "key": dataset_id + ":" + nct_id,
            "dataset_id": dataset_id,
            "nct_id": nct_id,
            "raw_hash": content_hash(study),
            "title": identification.get("briefTitle", nct_id),
            "phases": phases,
            "phase_label": " / ".join(PHASE_LABELS.get(p, p) for p in phases) if phases else "Not reported",
            "status": status.get("overallStatus", "Not reported"),
            "study_type": design.get("studyType", "Not reported"),
            "start_year": year_of(status.get("startDateStruct", {}).get("date")),
            "start_date_type": status.get("startDateStruct", {}).get("type"),
            "first_posted_year": year_of(status.get("studyFirstPostDateStruct", {}).get("date")),
            "enrollment": enrollment.get("count"),
            "enrollment_type": enrollment.get("type"),
            "sponsor_name": sponsor.get("name", "Not reported"),
            "sponsor_class": sponsor.get("class", "Not reported"),
        },
        "sponsors": [{"key": entity_key("sponsor", sponsor["name"]), "name": sponsor["name"]}]
        if sponsor.get("name")
        else [],
        "interventions": interventions,
        "countries": [{"key": entity_key("country", name), "name": name} for name in countries],
        "conditions": [
            {"key": entity_key("condition", name), "name": name} for name in sorted(set(conditions))
        ],
    }


def record_predicate(alias: str, filters: Filters, prefix: str = "") -> tuple[str, dict]:
    # alias and prefix are compiler-owned constants, never accepted from request text.
    clauses = [f"{alias}.dataset_id = $dataset_id"]
    params: dict[str, Any] = {}
    for field in ("phases", "statuses", "countries"):
        values = getattr(filters, field)
        if not values:
            continue
        param = prefix + field
        params[param] = values
        if field == "phases":
            clauses.append(f"any(p IN {alias}.phases WHERE p IN ${param})")
        elif field == "statuses":
            clauses.append(f"{alias}.status IN ${param}")
        else:
            clauses.append(
                f"EXISTS {{ MATCH ({alias})-[:HAS_COUNTRY]->(fc:Country) WHERE fc.name IN ${param} }}"
            )
    for field, comparator in (("start_year", ">="), ("end_year", "<=")):
        value = getattr(filters, field)
        if value is not None:
            param = prefix + field
            params[param] = value
            clauses.append(f"{alias}.start_year {comparator} ${param}")
    if filters.enrollment_type:
        params[prefix + "enrollment_type"] = filters.enrollment_type
        clauses.append(f"{alias}.enrollment_type = ${prefix}enrollment_type")
    return " AND ".join(clauses), params


IMPORT_QUERY = """
UNWIND $rows AS row
MERGE (r:TrialRecord {key: row.props.key})
SET r += row.props
WITH r, row
MATCH (d:DatasetSnapshot {dataset_id: $dataset_id})
MERGE (d)-[:CONTAINS]->(r)
FOREACH (item IN row.sponsors |
    MERGE (s:Sponsor {key: item.key}) ON CREATE SET s.name = item.name
    MERGE (s)-[:LEADS]->(r))
FOREACH (item IN row.interventions |
    MERGE (i:Intervention {key: item.key}) ON CREATE SET i.name = item.name, i.kind = item.kind
    MERGE (r)-[:HAS_INTERVENTION]->(i))
FOREACH (item IN row.conditions |
    MERGE (c:Condition {key: item.key}) ON CREATE SET c.name = item.name
    MERGE (r)-[:HAS_CONDITION]->(c))
FOREACH (item IN row.countries |
    MERGE (c:Country {key: item.key}) ON CREATE SET c.name = item.name
    MERGE (r)-[:HAS_COUNTRY]->(c))
"""


class GraphRepository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password.get_secret_value()),
            connection_timeout=10,
            max_transaction_retry_time=10,
        )

    def close(self):
        self.driver.close()

    @contextmanager
    def budget(self, deadline: float | None):
        previous = _deadline.get()
        effective = (
            previous if deadline is None else deadline if previous is None else min(previous, deadline)
        )
        token = _deadline.set(effective)
        try:
            yield
        finally:
            _deadline.reset(token)

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        remaining = 30 if _deadline.get() is None else _deadline.get() - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Graph execution exceeded the run deadline")
        # Auto-commit avoids managed retry loops spending another transaction budget.
        with self.driver.session(database=self.settings.neo4j_database) as session:
            result = session.run(Query(cypher, timeout=min(30, remaining)), parameters=params or {})
            records = [record.data() for record in result]
        if _deadline.get() is not None and time.monotonic() >= _deadline.get():
            raise TimeoutError("Graph execution exceeded the run deadline")
        return records

    def setup(self):
        for label, prop in (
            ("DatasetSnapshot", "dataset_id"),
            ("TrialRecord", "key"),
            ("Sponsor", "key"),
            ("Intervention", "key"),
            ("Condition", "key"),
            ("Country", "key"),
        ):
            self.query(
                f"CREATE CONSTRAINT {label.lower()}_identity IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
            )
        self.query("CREATE INDEX trial_dataset IF NOT EXISTS FOR (r:TrialRecord) ON (r.dataset_id)")

    def import_snapshot(self, snapshot: Snapshot, studies: list[dict], deadline: float | None = None):
        with self.budget(deadline):
            self._import_snapshot(snapshot, studies)

    def _import_snapshot(self, snapshot: Snapshot, studies: list[dict]):
        if not snapshot.complete or content_hash(studies) != snapshot.raw_hash:
            raise ValueError("Only verified complete snapshots can be imported")
        dataset_id = snapshot.dataset_id
        existing = self.query(
            "MATCH (d:DatasetSnapshot {dataset_id:$dataset_id}) RETURN d.raw_hash AS hash, d.ready AS ready",
            {"dataset_id": dataset_id},
        )
        if existing:
            if existing[0]["hash"] != snapshot.raw_hash:
                raise ValueError("A dataset ID cannot be reused for a different snapshot")
            if existing[0]["ready"]:
                return
        self.query(
            "MERGE (d:DatasetSnapshot {dataset_id:$dataset_id}) SET d.raw_hash=$hash, d.ready=false",
            {"dataset_id": dataset_id, "hash": snapshot.raw_hash},
        )
        for offset in range(0, len(studies), 200):
            rows = [normalize(study, dataset_id) for study in studies[offset : offset + 200]]
            self.query(IMPORT_QUERY, {"dataset_id": dataset_id, "rows": rows})
        count = self.query(
            "MATCH (r:TrialRecord {dataset_id:$dataset_id}) RETURN count(r) AS n", {"dataset_id": dataset_id}
        )[0]["n"]
        if count != snapshot.retrieved_unique_count:
            raise ValueError("Graph import count does not match snapshot")
        self.query(
            "MATCH (d:DatasetSnapshot {dataset_id:$dataset_id}) SET d.ready=true, d.record_count=$count",
            {"dataset_id": dataset_id, "count": count},
        )

    def ensure_ready(self, dataset_id: str):
        ready = self.query(
            "MATCH (d:DatasetSnapshot {dataset_id:$dataset_id, ready:true}) RETURN d.record_count AS n",
            {"dataset_id": dataset_id},
        )
        if not ready:
            raise ValueError("Dataset is not ready for analysis")

    def records(self, dataset_id: str, filters: Filters) -> list[dict]:
        self.ensure_ready(dataset_id)
        where, params = record_predicate("r", filters)
        return self.query(
            f"MATCH (r:TrialRecord) WHERE {where} RETURN r {{.*}} AS record ORDER BY r.nct_id",
            {"dataset_id": dataset_id, **params},
        )

    def groups(self, dataset_id: str, filters: Filters, dimension: str) -> list[dict]:
        self.ensure_ready(dataset_id)
        expressions = {
            "phase": ("", "r.phase_label"),
            "status": ("", "r.status"),
            "sponsor": ("", "r.sponsor_name"),
            "sponsor_class": ("", "r.sponsor_class"),
            "study_type": ("", "r.study_type"),
            "country": ("OPTIONAL MATCH (r)-[:HAS_COUNTRY]->(c:Country)", "coalesce(c.name, 'Not reported')"),
            "intervention_type": (
                "OPTIONAL MATCH (r)-[:HAS_INTERVENTION]->(i:Intervention)",
                "coalesce(i.kind, 'Not reported')",
            ),
            "start_year": ("", "r.start_year"),
            "first_posted_year": ("", "r.first_posted_year"),
        }
        if dimension not in expressions:
            raise ValueError("Unsupported grouping dimension")
        match, expression = expressions[dimension]
        if dimension == "country" and filters.countries:
            match = "MATCH (r)-[:HAS_COUNTRY]->(c:Country) WHERE c.name IN $countries"
        where, params = record_predicate("r", filters)
        return self.query(
            f"MATCH (r:TrialRecord) WHERE {where} {match} RETURN {expression} AS category, collect(DISTINCT r.nct_id) AS contributors",
            {"dataset_id": dataset_id, **params},
        )

    def network(self, dataset_id: str, filters: Filters, relationship: str) -> list[dict]:
        self.ensure_ready(dataset_id)
        where, params = record_predicate("r", filters)
        if relationship == "sponsor_drug":
            cypher = f"""MATCH (s:Sponsor)-[:LEADS]->(r:TrialRecord)-[:HAS_INTERVENTION]->(i:Intervention)
                WHERE {where} AND i.kind IN ['DRUG','BIOLOGICAL']
                RETURN s.key AS source, s.name AS source_label, 'sponsor' AS source_type,
                i.key AS target, i.name AS target_label, 'intervention' AS target_type,
                collect(DISTINCT r.nct_id) AS contributors"""
        elif relationship == "drug_cooccurrence":
            cypher = f"""MATCH (a:Intervention)<-[:HAS_INTERVENTION]-(r:TrialRecord)-[:HAS_INTERVENTION]->(b:Intervention)
                WHERE {where} AND a.kind IN ['DRUG','BIOLOGICAL'] AND b.kind IN ['DRUG','BIOLOGICAL'] AND a.key < b.key
                RETURN a.key AS source, a.name AS source_label, 'intervention' AS source_type,
                b.key AS target, b.name AS target_label, 'intervention' AS target_type,
                collect(DISTINCT r.nct_id) AS contributors"""
        elif relationship == "sponsor_shared_drug":
            where2, params2 = record_predicate("r2", filters, "second_")
            params.update(params2)
            cypher = f"""MATCH (a:Sponsor)-[:LEADS]->(r:TrialRecord)-[:HAS_INTERVENTION]->(i:Intervention)
                <-[:HAS_INTERVENTION]-(r2:TrialRecord)<-[:LEADS]-(b:Sponsor)
                WHERE {where} AND {where2} AND i.kind IN ['DRUG','BIOLOGICAL'] AND a.key < b.key
                RETURN a.key AS source, a.name AS source_label, 'sponsor' AS source_type,
                b.key AS target, b.name AS target_label, 'sponsor' AS target_type,
                i.key AS shared_id, i.name AS shared_name,
                collect(DISTINCT r.nct_id) AS left_contributors,
                collect(DISTINCT r2.nct_id) AS right_contributors"""
        else:
            raise ValueError("Unsupported relationship")
        return self.query(cypher, {"dataset_id": dataset_id, **params})
