"""SQLite application state, explicit successful-session commits, and evidence paging."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.sources import canonical_json, content_hash, utc_now


class StateConflict(ValueError):
    pass


class StateStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "state.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, version INTEGER NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_id TEXT,
                    started_at TEXT NOT NULL, request TEXT NOT NULL, response TEXT, trace TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, contents TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_events (
                    run_id TEXT NOT NULL, call INTEGER NOT NULL, stage TEXT NOT NULL,
                    contents TEXT NOT NULL, PRIMARY KEY(run_id,call,stage));
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            if "session_state" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN session_state TEXT")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def session(self, session_id: str | None) -> tuple[str, int, dict]:
        session_id = session_id or "session_" + uuid4().hex
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row:
                return session_id, row["version"], json.loads(row["state"])
            conn.execute("INSERT OR IGNORE INTO sessions VALUES (?,0,'{}')", (session_id,))
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return session_id, row["version"], json.loads(row["state"])

    def start_run(self, session_id: str, parent_id: str | None, request: dict) -> str:
        with self.connect() as conn:
            if parent_id:
                parent = conn.execute("SELECT * FROM runs WHERE id=?", (parent_id,)).fetchone()
                if not parent or parent["session_id"] != session_id or not parent["response"]:
                    raise StateConflict("parent_run_id must identify a completed run in this session")
                if json.loads(parent["response"])["status"] not in ("ok", "no_data", "needs_clarification"):
                    raise StateConflict("Parent run must be a completed analysis or clarification")
            run_id = "run_" + uuid4().hex
            conn.execute(
                "INSERT INTO runs (id,session_id,parent_id,started_at,request,response,trace) VALUES (?,?,?,?,?,?,?)",
                (run_id, session_id, parent_id, utc_now(), canonical_json(request), None, "[]"),
            )
        return run_id

    def run_session(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT session_state FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row["session_state"]) if row and row["session_state"] else None

    def finalize_run(
        self,
        run_id: str,
        expected_version: int,
        state: dict,
        response: dict,
        trace: list[dict],
        *,
        advance_session: bool,
    ) -> dict:
        """Publish a result, its branch state, and session CAS in ONE durable transaction."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            if run["response"]:
                return json.loads(run["response"])
            if advance_session:
                changed = conn.execute(
                    "UPDATE sessions SET version=version+1,state=? WHERE id=? AND version=?",
                    (canonical_json(state), run["session_id"], expected_version),
                ).rowcount
                if changed != 1:
                    raise StateConflict("Session changed during the run; retry against the latest parent")
            conn.execute(
                "UPDATE runs SET response=?,trace=?,session_state=? WHERE id=?",
                (canonical_json(response), canonical_json(trace), canonical_json(state), run_id),
            )
        return response

    def finish_run(self, run_id: str, response: dict, trace: list[dict]):
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET response=?,trace=? WHERE id=?",
                (canonical_json(response), canonical_json(trace), run_id),
            )

    def record_trace(self, run_id: str, trace: list[dict]):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET trace=? WHERE id=?", (canonical_json(trace), run_id))

    def record_model_event(self, run_id: str, event: dict, trace: list[dict] | None = None):
        """Append an immutable request/outcome independently of checkpoint snapshots."""
        if event["stage"] not in ("planner_request", "planner_response", "planner_failure"):
            raise ValueError("Invalid model journal event")
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO model_events VALUES (?,?,?,?)",
                (run_id, event["call"], event["stage"], canonical_json(event)),
            )
            if trace is not None:
                conn.execute("UPDATE runs SET trace=? WHERE id=?", (canonical_json(trace), run_id))

    def model_journal(self, run_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT contents FROM model_events WHERE run_id=? ORDER BY call, CASE stage WHEN 'planner_request' THEN 0 ELSE 1 END",
                (run_id,),
            ).fetchall()
        return [json.loads(row["contents"]) for row in rows]

    def run(self, run_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {
            "run_id": row["id"],
            "session_id": row["session_id"],
            "parent_run_id": row["parent_id"],
            "started_at": row["started_at"],
            "request": json.loads(row["request"]),
            "response": json.loads(row["response"]) if row["response"] else None,
            "trace": json.loads(row["trace"]),
        }

    def commit_session(self, session_id: str, expected_version: int, state: dict):
        with self.connect() as conn:
            current = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if (
                current
                and json.loads(current["state"]).get("last_run_id") == state.get("last_run_id")
                and state.get("last_run_id")
            ):
                return
            changed = conn.execute(
                "UPDATE sessions SET version=version+1,state=? WHERE id=? AND version=?",
                (canonical_json(state), session_id, expected_version),
            ).rowcount
            if changed != 1:
                raise StateConflict("Session changed during the run; retry against the latest parent")

    def evidence(self, run_id: str, citations: list[dict], rule: dict) -> str:
        contents = {
            "rule": rule,
            "citations": sorted(citations, key=lambda c: (c["dataset_id"], c["nct_id"])),
        }
        evidence_id = "ev_" + content_hash({"run_id": run_id, **contents})[:32]
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO evidence VALUES (?,?,?)",
                (evidence_id, run_id, canonical_json(contents)),
            )
        return evidence_id

    def evidence_page(self, evidence_id: str, offset: int = 0, limit: int = 50) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            return None
        contents = json.loads(row["contents"])
        citations = contents["citations"]
        end = min(offset + limit, len(citations))
        return {
            "evidence_set_id": evidence_id,
            "run_id": row["run_id"],
            "rule": contents["rule"],
            "total_contributors": len(citations),
            "offset": offset,
            "limit": limit,
            "citations": citations[offset:end],
            "next_offset": end if end < len(citations) else None,
        }
