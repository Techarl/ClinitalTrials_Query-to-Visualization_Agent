"""Audited exhaustive partitions for wide source scopes, including unknown study types."""

import time
from uuid import uuid4

from app.sources import Snapshot, content_hash, utc_now


def fetch_partitioned(client, query, deadline, threshold=5000, cancelled=lambda: False):
    before = client.get("/version", None, deadline)
    root = client.get("/studies", {**query, "countTotal": "true", "pageSize": 1, "format": "json"}, deadline)
    expected = root.get("totalCount")
    if type(expected) is not int or expected < 0:
        raise ValueError("Source population count unavailable")
    if expected <= threshold:
        snapshot, records = client.fetch(query, deadline)
        return (
            snapshot,
            records,
            {"strategy": "complete_pagination", "root_count": expected, "source_version": before},
        )
    if expected > client.settings.max_source_records:
        raise ValueError(
            f"Population {expected} exceeds full-record budget {client.settings.max_source_records}; narrow scope or explicitly select official-count overview"
        )
    started = utc_now()
    clauses = [
        "AREA[StudyType]INTERVENTIONAL",
        "AREA[StudyType]OBSERVATIONAL",
        "AREA[StudyType]EXPANDED_ACCESS",
    ]
    clauses.append("NOT (" + " OR ".join(clauses) + ")")
    records, partitions, pages = {}, [], 0
    for predicate in clauses:
        if cancelled() or time.monotonic() >= deadline:
            raise TimeoutError("Partition acquisition cancelled or deadline exhausted")
        partition_query = {
            **query,
            "filter.advanced": " AND ".join(filter(None, [query.get("filter.advanced"), predicate])),
        }
        snapshot, raw = client.fetch(partition_query, deadline)
        partitions.append(
            {"query": partition_query, "manifest": snapshot.model_dump(), "predicate": predicate}
        )
        if (
            not snapshot.complete
            or snapshot.source_version_before.get("dataTimestamp") != before.get("dataTimestamp")
            or snapshot.source_version_after.get("dataTimestamp") != before.get("dataTimestamp")
        ):
            raise ValueError("Partition incomplete or source version changed; no aggregate published")
        for record in raw:
            nct = record["protocolSection"]["identificationModule"]["nctId"]
            if nct in records:
                raise ValueError("Disjoint partition overlap or conflicting record detected")
            records[nct] = record
        pages += snapshot.pages
    after = client.get("/version", None, deadline)
    if (
        not before.get("dataTimestamp")
        or before["dataTimestamp"] != after.get("dataTimestamp")
        or len(records) != expected
    ):
        raise ValueError("Partition union does not cover one stable official population")
    raw = [records[nct] for nct in sorted(records)]
    snapshot = Snapshot(
        dataset_id="ds_" + uuid4().hex,
        query=query,
        query_hash=content_hash(query),
        started_at=started,
        finished_at=utc_now(),
        source_version_before=before,
        source_version_after=after,
        expected_count=expected,
        retrieved_unique_count=len(raw),
        complete=True,
        pages=pages,
        raw_hash=content_hash(raw),
    )
    return (
        snapshot,
        raw,
        {
            "strategy": "disjoint_study_type_partition",
            "root_count": expected,
            "root_receipt": root,
            "partitions": partitions,
            "union_count": len(raw),
            "unknown_values_in_complement": True,
            "source_version_before": before,
            "source_version_after": after,
        },
    )
