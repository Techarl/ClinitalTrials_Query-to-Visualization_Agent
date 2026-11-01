"""Verify recorded V2 exports offline: no model, network, SQLite or Neo4j required."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from app.config import Settings
from app.sources import Snapshot, content_hash
from app.v2.contracts import HandleInput, RunResult
from app.v2.tools import ToolRegistry
from scripts.audit_core300_selection import dependency_failures


def read(path):
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return json.loads(data)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExportStore:
    """Read-only equivalent of the owned asset and complete evidence stores."""

    def __init__(self, assets, evidence):
        self.assets = {row["id"]: row for row in assets}
        self.evidence = {row["id"]: row for row in evidence}
        if len(self.assets) != len(assets) or len(self.evidence) != len(evidence):
            raise ValueError("Duplicate exported store identity")
        self.base = self

    def get(self, handle, session_id, kind=None):
        row = self.assets.get(handle)
        if not row or row["session_id"] != session_id or (kind and row["kind"] != kind):
            raise ValueError("Missing or incorrectly owned asset")
        data = row["data"]
        if content_hash(data) != row["hash"]:
            raise ValueError("Asset hash mismatch")
        return data

    def evidence_page(self, evidence_id, offset=0, limit=50):
        row = self.evidence.get(evidence_id)
        if not row:
            return None
        contents = row["contents"]
        if "ev_" + content_hash({"run_id": row["run_id"], **contents})[:32] != evidence_id:
            raise ValueError("Evidence identity mismatch")
        citations = contents["citations"]
        if len({(c["dataset_id"], c["nct_id"]) for c in citations}) != len(citations):
            raise ValueError("Duplicate contributor citation")
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


class ArchiveSnapshots:
    def __init__(self, archive):
        self.archive = archive

    def load(self, dataset_id):
        snapshot = Snapshot.model_validate_json(self.archive.read(f"{dataset_id}/manifest.json"))
        records = json.loads(self.archive.read(f"{dataset_id}/studies.json"))
        if (
            snapshot.dataset_id != dataset_id
            or snapshot.raw_hash != content_hash(records)
            or snapshot.retrieved_unique_count != len(records)
            or not snapshot.complete
        ):
            raise ValueError("Source snapshot integrity mismatch")
        return snapshot, records


def verify_example(root, entry, snapshots):
    folder = root / entry["directory"]
    store = ExportStore(read(folder / "assets.json.gz"), read(folder / "evidence.json.gz"))
    registry = ToolRegistry(Settings(_env_file=None), SimpleNamespace(snapshots=snapshots), store)
    rows, prior = [], None
    for turn in entry["turns"]:
        path = folder / turn["directory"]
        response, state, trace, request = [
            read(path / f"{name}.json") for name in ("response", "state", "trace", "request")
        ]
        RunResult.model_validate(response)
        if request != state["request"] or response["run_id"] != state["run_id"]:
            raise ValueError("Exported request/result identity mismatch")
        if response["session_id"] != state["session_id"] or response["task"] != state["task"]:
            raise ValueError("Exported session/task mismatch")
        if prior and (
            state["parent_run_id"] != prior["run_id"] or state["session_id"] != prior["session_id"]
        ):
            raise ValueError("Follow-up parent/session mismatch")
        if response["status"] != "completed" or not response["artifacts"]:
            raise ValueError("Example does not contain a completed result")
        if response["usage"]["model_calls"] <= 0:
            raise ValueError("Recorded live-model example has no model call")
        results = []
        for artifact in response["artifacts"]:
            stored = registry.artifact(artifact["result_id"], state).model_dump(mode="json")
            # Bind the actual exported response to the artifact independently recomputed below.
            if stored != artifact:
                raise ValueError("Response artifact differs from immutable stored result")
            results.append(registry.validate(HandleInput(handle=artifact["result_id"]), state))
        failures = dependency_failures(
            entry.get("expected_dependency", {}), response["artifacts"], trace["journal"]
        )
        if failures:
            raise ValueError("; ".join(failures))
        rows.append({"run_id": response["run_id"], "results": results})
        prior = state
    return {"example": entry["directory"], "turns": rows, "validated": True}


def verify(root):
    root = Path(root)
    manifest = read(root / "manifest.json")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != expected:
            raise ValueError(f"Delivery file hash mismatch: {name}")
    index = read(root / "index.json")
    environment = read(root / "recorded-environment.json")
    with ZipFile(root / "recorded-source.zip") as archive:
        expected_source = {
            **environment["source_code_sha256"],
            "requirements.lock": environment["dependency_sha256"],
        }
        if set(archive.namelist()) != set(expected_source):
            raise ValueError("Recorded source archive membership mismatch")
        for name, expected in expected_source.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Recorded source archive hash mismatch")
    for entry in index["examples"] + index.get("diagnostics", []):
        folder = root / entry["directory"]
        provenance = read(folder / "provenance.json")
        if provenance["environment_sha256"] != sha(root / "recorded-environment.json"):
            raise ValueError("Recorded environment provenance mismatch")
        for number, turn in enumerate(entry["turns"], 1):
            path = folder / turn["directory"]
            raw = gzip.decompress((path / "original-recording.json.gz").read_bytes())
            if (
                hashlib.sha256(raw).hexdigest()
                != provenance["original_recording_sha256"][f"turn-{number}.json"]
            ):
                raise ValueError("Original recording hash mismatch")
            original = json.loads(raw)
            for name, expected in {
                "response": original["response"],
                "state": original["state"],
                "request": original["state"]["request"],
                "trace": {"events": original["events"], "journal": original["journal"]},
            }.items():
                if read(path / f"{name}.json") != expected:
                    raise ValueError("Export differs from preserved original recording")
    with ZipFile(root / "sources.zip") as archive:
        snapshots = ArchiveSnapshots(archive)
        results = [verify_example(root, entry, snapshots) for entry in index["examples"]]
    return {
        "status": "verified",
        "examples": len(results),
        "turns": sum(len(r["turns"]) for r in results),
        "exact_fields_checked": sum(
            v["exact_fields_checked"] for r in results for t in r["turns"] for v in t["results"]
        ),
        "results": results,
        "limits": "Checks original exported artifacts, complete contributor sets and raw fields. This is not a fresh model run or a broad natural-language benchmark.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, default=Path("examples/v2"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.examples)
    identity = {p.as_posix(): sha(p) for base in ("app", "scripts") for p in Path(base).rglob("*.py")}
    report["verification_source_sha256"] = identity
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("results", "verification_source_sha256")}))


if __name__ == "__main__":
    main()
