"""Durable V2 runs, immutable owned handles and replayable public events."""

import json
import secrets
import time
from uuid import uuid4

from app.sources import canonical_json, content_hash, utc_now
from app.storage import StateConflict, StateStore

TERMINAL = {"completed", "partial", "failed", "cancelled", "limited", "needs_clarification"}


class V2Store:
    def __init__(self, base: StateStore):
        self.base = base
        with base.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS v2_runs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_id TEXT,
                    created_at TEXT NOT NULL, status TEXT NOT NULL, state TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS v2_sessions (
                    id TEXT PRIMARY KEY, version INTEGER NOT NULL, head TEXT);
                CREATE TABLE IF NOT EXISTS v2_events (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(run_id,seq));
                CREATE TABLE IF NOT EXISTS v2_assets (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, hash TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS v2_journal (
                    run_id TEXT NOT NULL, signature TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, input TEXT NOT NULL, output TEXT, attempts INTEGER NOT NULL,
                    PRIMARY KEY(run_id,signature));
                CREATE TABLE IF NOT EXISTS v2_shares (
                    token TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0);
            """)

    def create(self, request, seconds: float) -> dict:
        sid = request.session_id or "vs_" + uuid4().hex
        rid = "vr_" + uuid4().hex
        with self.base.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO v2_sessions VALUES (?,0,NULL)", (sid,))
            session = conn.execute("SELECT * FROM v2_sessions WHERE id=?", (sid,)).fetchone()
            parent_id = request.parent_run_id or session["head"]
            prior = None
            if parent_id:
                parent = conn.execute("SELECT * FROM v2_runs WHERE id=?", (parent_id,)).fetchone()
                if not parent or parent["session_id"] != sid or parent["status"] not in TERMINAL:
                    raise StateConflict("Parent must be a completed run in this session")
                pstate = json.loads(parent["state"])
                prior = {
                    "original_request": pstate.get("pending_request", pstate["request"]),
                    "task": pstate.get("task"),
                    "request": pstate["request"],
                    "result_ids": pstate.get("result_ids", []),
                    "questions": pstate.get("questions", []),
                    "history": pstate.get("history", [])[-8:],
                }
                prior["artifacts"] = []
                for handle in prior["result_ids"]:
                    row = conn.execute(
                        "SELECT data FROM v2_assets WHERE id=? AND session_id=? AND kind='result'",
                        (handle, sid),
                    ).fetchone()
                    if row:
                        artifact = json.loads(row["data"])["artifact"]
                        prior["artifacts"].append(
                            {
                                "result_id": handle,
                                **{
                                    key: artifact[key]
                                    for key in ("goal_id", "title", "source_type", "dependencies")
                                },
                                "analysis_parameters": artifact["metadata"].get("analysis_parameters"),
                            }
                        )
            state = {
                "run_id": rid,
                "session_id": sid,
                "parent_run_id": parent_id,
                "request": request.model_dump(mode="json", exclude_unset=True),
                "prior": prior,
                "session_version": session["version"],
                "deadline_epoch": time.time() + seconds,
                "task": None,
                "constraints_version": 0,
                "observations": [],
                "result_ids": [],
                "questions": [],
                "status": "queued",
                "model_calls": 0,
                "history": (prior or {}).get("history", []) + [request.query],
                "pending_request": prior["original_request"]
                if prior and parent["status"] == "needs_clarification"
                else request.model_dump(mode="json", exclude_unset=True),
                "versions": {"schema": "v2.0", "prompt": "tool-loop-v2", "tools": "tools-v2"},
            }
            conn.execute(
                "INSERT INTO v2_runs VALUES (?,?,?,?,?,?,0,0)",
                (rid, sid, parent_id, utc_now(), "queued", canonical_json(state)),
            )
        self.event(rid, "run_created", {"query": request.query})
        return state

    def run(self, run_id: str) -> dict:
        with self.base.connect() as conn:
            row = conn.execute("SELECT state,cancelled FROM v2_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError("Run not found")
        return {**json.loads(row["state"]), "cancelled": bool(row["cancelled"])}

    def save(self, state: dict, finish=False):
        with self.base.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if finish:
                current = conn.execute("SELECT status FROM v2_runs WHERE id=?", (state["run_id"],)).fetchone()
                if current["status"] in TERMINAL:
                    return
                updated = conn.execute(
                    "UPDATE v2_sessions SET version=version+1,head=? WHERE id=? AND version=?",
                    (state["run_id"], state["session_id"], state["session_version"]),
                ).rowcount
                # Preserve a completed concurrent branch without silently replacing a newer head.
                state["session_head_updated"] = updated == 1
                if updated != 1:
                    state["session_note"] = (
                        "A newer run advanced the session; this result remains an explicit historical branch."
                    )
            conn.execute(
                "UPDATE v2_runs SET state=?,status=?,version=version+1 WHERE id=?",
                (canonical_json(state), state["status"], state["run_id"]),
            )
            if finish:
                self._event(
                    conn,
                    state["run_id"],
                    "clarification_required" if state["status"] == "needs_clarification" else "run_completed",
                    {
                        "status": state["status"],
                        "message": state.get("message", ""),
                        "unmet_goals": state.get("unmet_goals", []),
                        "questions": state.get("questions", []),
                    },
                )

    def _event(self, conn, run_id, kind, payload):
        seq = conn.execute(
            "SELECT coalesce(max(seq),0)+1 FROM v2_events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        event = {"run_id": run_id, "sequence": seq, "timestamp": utc_now(), "type": kind, "data": payload}
        conn.execute("INSERT INTO v2_events VALUES (?,?,?)", (run_id, seq, canonical_json(event)))
        return event

    def event(self, run_id: str, kind: str, payload: dict):
        with self.base.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = self._event(conn, run_id, kind, payload)
        return event

    def events(self, run_id, after=0, limit=200):
        with self.base.connect() as conn:
            rows = conn.execute(
                "SELECT data FROM v2_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def asset(self, session_id: str, run_id: str, kind: str, data: dict) -> str:
        digest = content_hash(data)
        handle = kind + "_" + content_hash([session_id, run_id, digest])[:32]
        with self.base.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO v2_assets VALUES (?,?,?,?,?,?)",
                (handle, session_id, run_id, kind, digest, canonical_json(data)),
            )
        return handle

    def get(self, handle: str, session_id: str, kind: str | None = None) -> dict:
        with self.base.connect() as conn:
            row = conn.execute(
                "SELECT * FROM v2_assets WHERE id=? AND session_id=?", (handle, session_id)
            ).fetchone()
        if not row or (kind and row["kind"] != kind):
            raise ValueError("Handle is missing, has the wrong kind, or belongs to another session")
        payload = json.loads(row["data"])
        if content_hash(payload) != row["hash"]:
            raise ValueError("Asset integrity verification failed")
        return payload

    def journal(self, rid, signature):
        with self.base.connect() as conn:
            row = conn.execute(
                "SELECT * FROM v2_journal WHERE run_id=? AND signature=?", (rid, signature)
            ).fetchone()
        return {**dict(row), "output": json.loads(row["output"]) if row["output"] else None} if row else None

    def begin(self, rid, signature, kind, arguments):
        with self.base.connect() as conn:
            conn.execute(
                "INSERT INTO v2_journal VALUES (?,?,?,'started',?,NULL,1) ON CONFLICT(run_id,signature) DO UPDATE SET attempts=attempts+1",
                (rid, signature, kind, canonical_json(arguments)),
            )

    def complete(self, rid, signature, output):
        with self.base.connect() as conn:
            conn.execute(
                "UPDATE v2_journal SET status='completed',output=? WHERE run_id=? AND signature=?",
                (canonical_json(output), rid, signature),
            )

    def journal_entries(self, rid):
        with self.base.connect() as conn:
            rows = conn.execute("SELECT * FROM v2_journal WHERE run_id=? ORDER BY rowid", (rid,)).fetchall()
        return [
            {
                **dict(row),
                "input": json.loads(row["input"]),
                "output": json.loads(row["output"]) if row["output"] else None,
            }
            for row in rows
        ]

    def cancel(self, rid):
        self.run(rid)
        with self.base.connect() as conn:
            conn.execute(
                "UPDATE v2_runs SET cancelled=1 WHERE id=? AND status NOT IN ('completed','partial','failed','cancelled','limited','needs_clarification')",
                (rid,),
            )
        self.event(rid, "cancel_requested", {})

    def history(self, session_id=None):
        with self.base.connect() as conn:
            rows = conn.execute(
                "SELECT id,session_id,parent_id,created_at,status,state FROM v2_runs ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return [
            {
                "run_id": row["id"],
                "session_id": row["session_id"],
                "parent_run_id": row["parent_id"],
                "created_at": row["created_at"],
                "status": row["status"],
                "query": json.loads(row["state"])["request"]["query"],
            }
            for row in rows
            if session_id is None or row["session_id"] == session_id
        ]

    def share(self, run_id, payload):
        if self.run(run_id)["status"] not in TERMINAL - {"needs_clarification"}:
            raise ValueError("Only completed result snapshots can be shared")
        token = secrets.token_urlsafe(24)
        with self.base.connect() as conn:
            conn.execute("INSERT INTO v2_shares VALUES (?,?,?,0)", (token, run_id, canonical_json(payload)))
        return token

    def shared(self, token):
        with self.base.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM v2_shares WHERE token=? AND revoked=0", (token,)
            ).fetchone()
        if not row:
            raise KeyError("Share not found or revoked")
        return json.loads(row[0])

    def revoke(self, run_id, token):
        with self.base.connect() as conn:
            changed = conn.execute(
                "UPDATE v2_shares SET revoked=1 WHERE token=? AND run_id=?", (token, run_id)
            ).rowcount
        if not changed:
            raise KeyError("Share not found")
