"""Bounded acquisition of immutable ClinicalTrials.gov snapshots."""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import Field

from app.config import Settings
from app.models import Contract, Filters


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def source_query(filters: Filters) -> dict[str, str]:
    """Only source-search fields participate in caching; remaining filters are local.

    Names are literal Essie phrases, never model-generated source query expressions.
    The resulting universe uses official search semantics, including source synonyms.
    """

    def phrase(value: str) -> str:
        if '"' in value or "\\" in value:
            raise ValueError("Source search names cannot contain quotes or backslashes")
        if value.upper().startswith("NCT") and value[3:].isdigit():
            raise ValueError(
                "NCT-only source searches are not supported; use a condition or intervention name"
            )
        return f'"{value}"'

    params = {}
    if filters.condition:
        params["query.cond"] = phrase(filters.condition)
    if filters.interventions:
        joiner = " OR " if filters.intervention_match == "any" else " AND "
        params["query.intr"] = "(" + joiner.join(phrase(v) for v in sorted(set(filters.interventions))) + ")"
    if filters.sponsor:
        params["query.lead"] = phrase(filters.sponsor)
    if not params:
        raise ValueError("Unbounded full-site retrieval is disabled")
    return params


class Snapshot(Contract):
    dataset_id: str
    query: dict[str, str]
    query_hash: str
    started_at: str
    finished_at: str
    source_version_before: dict = Field(default_factory=dict)
    source_version_after: dict = Field(default_factory=dict)
    expected_count: int | None = None
    retrieved_unique_count: int = 0
    pages: int = 0
    duplicate_count: int = 0
    complete: bool = False
    reason: str | None = None
    raw_hash: str
    schema_version: str = "snapshot-v1"


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = root / "snapshots"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: Snapshot, studies: list[dict]) -> None:
        folder = self.root / snapshot.dataset_id
        folder.mkdir(exist_ok=False)
        # Exclusive writes: an existing snapshot is never overwritten.
        with (folder / "studies.json").open("x", encoding="utf-8") as handle:
            handle.write(canonical_json(studies))
        with (folder / "manifest.json").open("x", encoding="utf-8") as handle:
            handle.write(snapshot.model_dump_json(indent=2))

    def load(self, dataset_id: str) -> tuple[Snapshot, list[dict]]:
        if not dataset_id.startswith("ds_") or not dataset_id[3:].isalnum():
            raise ValueError("Invalid dataset ID")
        folder = self.root / dataset_id
        snapshot = Snapshot.model_validate_json((folder / "manifest.json").read_text(encoding="utf-8"))
        studies = json.loads((folder / "studies.json").read_text(encoding="utf-8"))
        if snapshot.dataset_id != dataset_id or content_hash(studies) != snapshot.raw_hash:
            raise ValueError("Snapshot integrity verification failed")
        if len(studies) != snapshot.retrieved_unique_count:
            raise ValueError("Snapshot record count mismatch")
        return snapshot, studies

    def cached(self, query: dict[str, str], ttl: int) -> tuple[Snapshot, list[dict]] | None:
        if ttl <= 0:
            return None
        now = datetime.now(timezone.utc).timestamp()
        candidates = []
        for path in self.root.glob("ds_*/manifest.json"):
            manifest = Snapshot.model_validate_json(path.read_text(encoding="utf-8"))
            if (
                manifest.query_hash == content_hash(query)
                and manifest.complete
                and now - datetime.fromisoformat(manifest.finished_at).timestamp() <= ttl
            ):
                candidates.append(manifest)
        if candidates:
            newest = max(candidates, key=lambda item: item.finished_at)
            return self.load(newest.dataset_id)
        return None


class ClinicalTrialsClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.source_timeout_seconds)
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def get(self, path: str, params: dict | None, deadline: float) -> dict:
        last_error = None
        for attempt in range(self.settings.source_retries + 1):
            if time.monotonic() >= deadline:
                raise TimeoutError("Source acquisition exceeded the run deadline")
            try:
                remaining = max(0.1, deadline - time.monotonic())
                response = self.client.get(
                    str(self.settings.ctgov_base_url).rstrip("/") + path,
                    params=params,
                    timeout=min(self.settings.source_timeout_seconds, remaining),
                )
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict):
                    raise ValueError("Unexpected source response")
                return value
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as error:
                last_error = error
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in (
                    429,
                    500,
                    502,
                    503,
                    504,
                ):
                    raise
                if attempt == self.settings.source_retries:
                    raise
                delay = min(2**attempt, max(0, deadline - time.monotonic()))
                time.sleep(delay)
        raise RuntimeError("Source retry budget exhausted") from last_error

    def fetch(self, query: dict[str, str], deadline: float) -> tuple[Snapshot, list[dict]]:
        started = utc_now()
        before: dict = {}
        after: dict = {}
        records: dict[str, dict] = {}
        expected = None
        pages = duplicates = 0
        reason = None
        exhausted = False
        changed_record = False
        token = None
        seen_tokens = set()
        try:
            before = self.get("/version", None, deadline)
            for _ in range(self.settings.max_source_pages):
                params = {**query, "format": "json", "pageSize": self.settings.ctgov_page_size}
                if token:
                    params["pageToken"] = token
                else:
                    params["countTotal"] = "true"
                page = self.get("/studies", params, deadline)
                pages += 1
                if expected is None:
                    expected = page.get("totalCount")
                    if type(expected) is not int or expected < 0:
                        raise ValueError("Source response lacks a valid totalCount")
                    if expected > self.settings.max_source_records:
                        reason = "record_budget_exceeded; narrow the source search"
                        break
                studies = page.get("studies")
                if not isinstance(studies, list):
                    raise ValueError("Source response lacks studies array")
                for study in studies:
                    nct_id = study.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
                    if (
                        not isinstance(nct_id, str)
                        or len(nct_id) != 11
                        or not nct_id.startswith("NCT")
                        or not nct_id[3:].isdigit()
                    ):
                        raise ValueError("Invalid or missing NCT ID in source record")
                    if nct_id in records:
                        duplicates += 1
                        changed_record |= records[nct_id] != study
                    else:
                        records[nct_id] = study
                    if len(records) > self.settings.max_source_records:
                        reason = "record_budget_exceeded"
                        break
                if reason:
                    break
                token = page.get("nextPageToken")
                if not token:
                    exhausted = True
                    break
                if token in seen_tokens:
                    reason = "repeated_page_token"
                    break
                seen_tokens.add(token)
            else:
                reason = "page_budget_exceeded"
            after = self.get("/version", None, deadline)
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, AttributeError) as error:
            reason = f"source_error:{type(error).__name__}"
        if reason is None:
            if not exhausted:
                reason = "pagination_not_exhausted"
            elif not before.get("dataTimestamp") or before.get("dataTimestamp") != after.get("dataTimestamp"):
                reason = "source_version_changed_or_unavailable"
            elif changed_record:
                reason = "record_changed_during_fetch"
            elif len(records) != expected:
                reason = "unique_count_mismatch"
        studies = [records[key] for key in sorted(records)]
        snapshot = Snapshot(
            dataset_id="ds_" + uuid4().hex,
            query=query,
            query_hash=content_hash(query),
            started_at=started,
            finished_at=utc_now(),
            source_version_before=before,
            source_version_after=after,
            expected_count=expected,
            retrieved_unique_count=len(studies),
            pages=pages,
            duplicate_count=duplicates,
            complete=reason is None,
            reason=reason,
            raw_hash=content_hash(studies),
        )
        return snapshot, studies
