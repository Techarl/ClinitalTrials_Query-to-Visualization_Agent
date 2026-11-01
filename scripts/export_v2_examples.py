"""Export closed live-model V2 recordings, preserving original bytes and full evidence."""

import argparse
import gzip
import hashlib
import io
import json
import sqlite3
import subprocess
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.audit_core300_selection import dependency_failures
from scripts.verify_v2_examples import ArchiveSnapshots, read, sha, verify, verify_example

SELECTION = [
    ("01_phase", "ep_cc08257acaf2ab", "Phase distribution with complete contributor evidence."),
    (
        "02_followup",
        "ep_29ecf95b351f79",
        "Two turns in one session: change the annual date axis while retaining the population.",
    ),
    ("03_comparison", "ep_f2b353bd6b6774", "Asthma drug comparison with independently preserved cohorts."),
    ("04_scatter", "ep_cc386097b10b14", "Enrollment versus duration, with exact-date exclusions."),
    (
        "diagnostics/dependent",
        "ep_be43ca02cfbaea",
        "Historical dependency example with known missing terminal zero buckets; not a passing demo.",
    ),
]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(gzip.compress(raw, mtime=0) if path.suffix == ".gz" else raw)


def source_identity(environment, output, ref):
    commit = subprocess.check_output(["git", "rev-parse", ref], text=True).strip()
    archive = subprocess.check_output(["git", "archive", commit])
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        files = {m.name: source.extractfile(m).read() for m in source.getmembers() if m.isfile()}
    matches = {}
    with ZipFile(output / "recorded-source.zip", "x", ZIP_DEFLATED) as result:
        for name, expected in environment["source_code_sha256"].items():
            raw = files[name]
            mode = "exact_git_blob"
            if hashlib.sha256(raw).hexdigest() != expected:
                raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                mode = "git_blob_with_explicit_CRLF_checkout_conversion"
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(f"Cannot reconstruct recorded source bytes: {name}")
            result.writestr(name, raw)
            matches[name] = mode
        dependency = files["requirements.lock"]
        dependency_mode = "exact_git_blob"
        if hashlib.sha256(dependency).hexdigest() != environment["dependency_sha256"]:
            dependency = dependency.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            dependency_mode = "git_blob_with_explicit_CRLF_checkout_conversion"
        if hashlib.sha256(dependency).hexdigest() != environment["dependency_sha256"]:
            raise ValueError("Recorded dependency lock does not match source candidate")
        result.writestr("requirements.lock", dependency)
    return {
        "recorded_run_commit": None,
        "reconstruction_candidate_commit": commit,
        "file_matches": matches,
        "dependency_match": dependency_mode,
        "meaning": "Every recorded source hash matches these reconstructed bytes. This establishes file-content identity, not the original working-tree commit or cleanliness.",
    }


def export(study, output, source_ref="aa18319"):
    if output.exists():
        raise ValueError("Use a new export directory; never overwrite a recorded delivery")
    output.mkdir(parents=True)
    environment = read(study / "environment.json")
    (output / "recorded-environment.json").write_bytes((study / "environment.json").read_bytes())
    reconstruction = source_identity(environment, output, source_ref)
    write(output / "source-identity.json", reconstruction)
    entries, source_files, provenance_rows = [], {}, []
    for name, episode_id, description in SELECTION:
        original, folder = study / episode_id, output / name
        folder.mkdir(parents=True)
        database = original / "runtime/state.sqlite3"
        with sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            assets = [dict(r) for r in conn.execute("SELECT * FROM v2_assets ORDER BY id")]
            evidence = [dict(r) for r in conn.execute("SELECT * FROM evidence ORDER BY id")]
        for row in assets:
            row["data"] = json.loads(row["data"])
        for row in evidence:
            row["contents"] = json.loads(row["contents"])
        write(folder / "assets.json.gz", assets)
        write(folder / "evidence.json.gz", evidence)
        turns, original_hashes, calls, models = [], {}, 0, set()
        for number, path in enumerate(sorted(original.glob("turn-*.json")), 1):
            row = read(path)
            directory = f"turns/{number:02d}"
            turns.append(
                {
                    "directory": directory,
                    "run_id": row["response"]["run_id"],
                    "parent_run_id": row["state"]["parent_run_id"],
                }
            )
            for key, value in {
                "request": row["state"]["request"],
                "response": row["response"],
                "state": row["state"],
                "trace": {"events": row["events"], "journal": row["journal"]},
            }.items():
                write(folder / directory / f"{key}.json", value)
            # Exact original recording retained, including the historical grader result.
            (folder / directory / "original-recording.json.gz").write_bytes(
                gzip.compress(path.read_bytes(), mtime=0)
            )
            original_hashes[path.name] = sha(path)
            calls += row["response"]["usage"]["model_calls"]
            models.update(
                r.get("reported_model")
                for r in row["response"]["usage"]["receipts"]
                if r.get("reported_model")
            )
        for snapshot_file in sorted((original / "runtime/snapshots").glob("ds_*/*.json")):
            relative = snapshot_file.relative_to(original / "runtime/snapshots").as_posix()
            raw = snapshot_file.read_bytes()
            if relative in source_files and source_files[relative] != raw:
                raise ValueError("Conflicting immutable snapshot identity")
            source_files[relative] = raw
        provenance = {
            "episode_id": episode_id,
            "recording_type": "live model on frozen ClinicalTrials.gov records / recorded V2",
            "fresh_model_execution": False,
            "original_study": study.as_posix(),
            "original_recording_sha256": original_hashes,
            "original_database_sha256": sha(database),
            "environment_sha256": sha(study / "environment.json"),
            "recorded_model": environment["model"],
            "recorded_reasoning": environment["reasoning"],
            "receipt_reported_models": sorted(models),
            "model_calls": calls,
            "source_identity": "../../source-identity.json"
            if name.startswith("diagnostics/")
            else "../source-identity.json",
            "limits": "Recorded historical execution, not generated by the current source. Original outputs are not repaired during export. All contributor evidence is included, not only previews.",
        }
        write(folder / "provenance.json", provenance)
        provenance_rows.append(provenance)
        entry = {"directory": name, "episode_id": episode_id, "description": description, "turns": turns}
        if name.startswith("diagnostics/"):
            entry["expected_dependency"] = {
                "dependent_selection": True,
                "anchor_sponsor": "Merck Sharp & Dohme LLC",
                "selection_top_n": 3,
            }
        entries.append(entry)
    with ZipFile(output / "sources.zip", "x", ZIP_DEFLATED) as archive:
        for name, raw in sorted(source_files.items()):
            archive.writestr(name, raw)
    source_index = {}
    for name, raw in source_files.items():
        if name.endswith("manifest.json"):
            manifest = json.loads(raw)
            source_index[manifest["dataset_id"]] = {
                "started_at": manifest["started_at"],
                "finished_at": manifest["finished_at"],
                "raw_hash": manifest["raw_hash"],
                "records": manifest["retrieved_unique_count"],
                "manifest_file_sha256": hashlib.sha256(raw).hexdigest(),
            }
    write(output / "sources-index.json", source_index)
    with ZipFile(output / "sources.zip") as archive:
        snapshots = ArchiveSnapshots(archive)
        for entry in entries[:-1]:
            verify_example(output, entry, snapshots)
        diagnostic = entries[-1]
        diagnostic_turn = output / diagnostic["directory"] / diagnostic["turns"][0]["directory"]
        linkage = dependency_failures(
            diagnostic["expected_dependency"],
            read(diagnostic_turn / "response.json")["artifacts"],
            read(diagnostic_turn / "trace.json")["journal"],
        )
        diagnostic["selection_linkage_failures"] = linkage
        if linkage:
            raise ValueError("Historical selection linkage changed; review diagnostic explicitly")
        try:
            verify_example(output, diagnostic, snapshots)
        except ValueError as error:
            diagnostic["validation_error"] = str(error)
        else:
            raise ValueError("Expected historical diagnostic no longer reproduces; review it explicitly")
    write(
        output / "diagnostics/dependent/diagnostic.json",
        {
            **diagnostic,
            "qualified_demo": False,
            "finding": "Annual trend ends at 2024 despite an explicit inclusive 2015–2025 request; three 2025 zero buckets are absent. Original output is preserved unchanged.",
            "selection_linkage": "Requested Merck Sharp & Dohme LLC / top three linkage passes the separately recorded audit.",
        },
    )
    write(
        output / "index.json",
        {
            "version": "recorded-v2-examples-v1",
            "examples": entries[:-1],
            "diagnostics": [diagnostic],
            "recording_type": provenance_rows[0]["recording_type"],
        },
    )
    (output / "README.md").write_text(
        "# Recorded V2 examples\n\n"
        "These are live model executions on frozen ClinicalTrials.gov records, recorded before the current delivery changes. "
        "No model was called to export or verify them. They are separate from the historical V1 examples one directory above.\n\n"
        "| Example | Capability | Request and complete response |\n|---|---|---|\n"
        + "\n".join(
            f"| {entry['directory']} | {entry['description']} | "
            + " · ".join(
                f"[Turn {i}]({entry['directory']}/{turn['directory']}/request.json) / [result]({entry['directory']}/{turn['directory']}/response.json)"
                for i, turn in enumerate(entry["turns"], 1)
            )
            + " |"
            for entry in entries[:-1]
        )
        + "\n\nRun `python -m scripts.verify_v2_examples` from the repository root after installing Python dependencies. "
        "Verification needs no API key, model, Neo4j service, SQLite database or network connection. "
        "It checks delivery hashes, response/store binding, complete chart points and contributor sets, raw source fields, and follow-up lineage.\n\n"
        "Each turn includes its actual request, full RunResult, state and public event/tool journal. "
        "The original recording is preserved byte-for-byte inside original-recording.json.gz. "
        "Each example includes complete assets.json.gz and evidence.json.gz; Python's gzip module or an archive utility can open them. "
        "The shared sources.zip deduplicates immutable snapshots; sources-index.json lists acquisition timestamps, raw hashes and record counts.\n\n"
        "The original environment contains file hashes but no reliable run commit ID. source-identity.json records the exact Git blob/EOL reconstruction, "
        "and recorded-source.zip matches all 47 recorded code hashes and the dependency lock hash. "
        "This proves file contents, not a clean working-tree commit. verification.json records the current verifier file hashes separately.\n\n"
        "The [dependent diagnostic](diagnostics/dependent/diagnostic.json) is excluded from qualified examples: "
        "Merck/top-three selection linkage passes, but the original annual trend omits the three 2025 zero buckets. "
        "Its complete original trace, evidence and sources remain available; the chart was not patched after execution.\n",
        encoding="utf-8",
    )
    write(
        output / "manifest.json",
        {
            "version": 1,
            "files": {
                p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()
            },
        },
    )
    report = verify(output)
    report["verification_source_sha256"] = {
        p.as_posix(): sha(p) for base in ("app", "scripts") for p in Path(base).rglob("*.py")
    }
    write(output / "verification.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=Path("data/vnext-eval-v2-final"))
    parser.add_argument("--output", type=Path, default=Path("examples/v2"))
    parser.add_argument("--source-ref", default="aa18319")
    args = parser.parse_args()
    result = export(args.study, args.output, args.source_ref)
    print(json.dumps({k: result[k] for k in ("status", "examples", "turns", "exact_fields_checked")}))


if __name__ == "__main__":
    main()
