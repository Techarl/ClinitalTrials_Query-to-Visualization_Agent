"""Archive closed Core300 traces with an exact allowlist, hashes and recursive credential scan."""

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from dotenv import dotenv_values

from scripts.package_submission import ArchiveCredentialScanner
from scripts.report_public_nli import digest, read

MANIFEST = "CORE300-EXECUTION-MANIFEST.json"
ROOT_FILES = ("environment.json", "ledger.json", "report.json", "study.json", "study.source.zip")
CREDENTIAL_KEYS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN", "NEO4J_PASSWORD", "NCBI_API_KEY")
OMISSIONS = [
    "SQLite/checkpoint databases are omitted; this is an execution-evidence archive, not a live-session restore image.",
    "Duplicate turn-N-run.json run-ID pointers and per-episode execution.source.zip copies are omitted.",
    "Repeated runtime snapshots are omitted; public frozen CTgov records and own template cases are in core300-sources.zip.",
    "No NLI4CT or gated TrialQA input, answer key, trace, or database is selected.",
]


def credentials(repository, environment=None):
    environment = os.environ if environment is None else environment
    file_values = dotenv_values(repository / ".env")
    values = [
        value for key in CREDENTIAL_KEYS for value in (file_values.get(key), environment.get(key)) if value
    ]
    # Include JSON escaping because traces are JSON; never print either representation.
    return list(
        {
            representation.encode("utf-8")
            for value in values
            for representation in (value, json.dumps(value, ensure_ascii=False)[1:-1])
        }
    )


def bounded_file(repository, path):
    if path.is_symlink() or not path.resolve().is_relative_to(repository.resolve()) or not path.is_file():
        raise ValueError("Execution archive source must be a regular in-repository file")
    return path


def select_files(repository, study, *, expected_episodes=300, expected_turns=420):
    if not (study / "report.json").is_file():
        raise ValueError("Core producer is not closed; refusing to archive partial execution")
    for name in ROOT_FILES:
        bounded_file(repository, study / name)
    protocol, environment, report, ledger = (
        read(study / name) for name in ("study.json", "environment.json", "report.json", "ledger.json")
    )
    for identity in (protocol, environment):
        if digest(identity["identity"]) != identity["sha256"]:
            raise ValueError("Frozen metadata identity mismatch")
    if ledger != report["budget"]:
        raise ValueError("Closed report and ledger differ")
    episodes = protocol["identity"]["parameters"]["episodes"]
    planned = {episode["id"]: episode for episode in episodes}
    outcomes = {episode["episode_id"]: episode for episode in report["episodes"]}
    if (
        len(planned) != expected_episodes
        or len(episodes) != len(planned)
        or len(outcomes) != len(report["episodes"])
        or set(outcomes) != set(planned)
    ):
        raise ValueError("Expected exact closed episode population")
    if sum(len(episode["turns"]) for episode in episodes) != expected_turns:
        raise ValueError("Unexpected planned turn count")
    files = [study / name for name in ROOT_FILES]
    turns = set()
    summaries = set()
    for identity, episode in planned.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identity):
            raise ValueError("Unsafe episode identifier")
        folder = study / identity
        summary = bounded_file(repository, folder / "summary.json")
        execution = bounded_file(repository, folder / "execution.json")
        if read(summary) != outcomes[identity]:
            raise ValueError("Episode summary differs from final report")
        frozen = read(execution)
        if digest(frozen["identity"]) != frozen["sha256"]:
            raise ValueError("Episode execution identity mismatch")
        files.extend((summary, execution))
        summaries.add(summary)
        for number in range(1, len(episode["turns"]) + 1):
            path = bounded_file(repository, folder / f"turn-{number}.json")
            files.append(path)
            turns.add(path)
    discovered_turns = {
        path for path in study.rglob("turn-*.json") if re.fullmatch(r"turn-\d+\.json", path.name)
    }
    if discovered_turns != turns or set(study.rglob("summary.json")) != summaries:
        raise ValueError("Unexpected or missing canonical turn/summary files")
    with ZipFile(study / "study.source.zip") as archive:
        hashes = protocol["identity"]["code"]
        if set(archive.namelist()) != set(hashes) or len(archive.namelist()) != len(hashes):
            raise ValueError("Frozen source archive membership differs")
        for name, expected in hashes.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Frozen source archive hash differs")
    return sorted(files), protocol["sha256"]


def verify_archive(path, manifest):
    expected = manifest["file_sha256"]
    with ZipFile(path) as archive:
        names = archive.namelist()
        if set(names) != {*expected, MANIFEST} or len(names) != len(expected) + 1:
            raise ValueError("Execution ZIP member set mismatch")
        if archive.testzip() is not None or json.loads(archive.read(MANIFEST)) != manifest:
            raise ValueError("Execution ZIP manifest or CRC mismatch")
        for name, checksum in expected.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != checksum:
                raise ValueError("Execution ZIP member hash mismatch")


def build(
    repository,
    study,
    output,
    *,
    expected_episodes=300,
    expected_turns=420,
    environment=None,
    max_bytes=100_000_000,
):
    repository, study, output = Path(repository).resolve(), Path(study).resolve(), Path(output).absolute()
    if (
        not study.is_relative_to(repository)
        or not output.resolve().is_relative_to(repository)
        or output.is_symlink()
    ):
        raise ValueError("Study and output must remain inside the repository")
    files, study_hash = select_files(
        repository, study, expected_episodes=expected_episodes, expected_turns=expected_turns
    )
    scanner = ArchiveCredentialScanner(credentials(repository, environment))
    payloads = {}
    for path in files:
        name, payload = path.relative_to(repository).as_posix(), path.read_bytes()
        scanner.scan(payload, name)
        payloads[name] = payload
    manifest = {
        "version": "core300-execution-evidence-v1",
        "study_sha256": study_hash,
        "counts": {
            "episodes": expected_episodes,
            "turns": expected_turns,
            "summaries": expected_episodes,
            "episode_execution_identities": expected_episodes,
            "root_metadata_files": len(ROOT_FILES),
        },
        "file_sha256": {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()},
        "omissions": OMISSIONS,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = repository / "data/archive-temporary"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=temporary_root, prefix="core-execution-", suffix=".pending", delete=False
    ) as pending:
        temporary = Path(pending.name)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=9) as archive:
            members = {
                **payloads,
                MANIFEST: json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
            }
            for name, payload in sorted(members.items()):
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system, info.external_attr = 3, 0o100644 << 16
                archive.writestr(info, payload, compress_type=ZIP_DEFLATED, compresslevel=9)
        if temporary.stat().st_size >= max_bytes:
            raise ValueError("Execution archive exceeds the configured size cap")
        verify_archive(temporary, manifest)
        archive_bytes = temporary.read_bytes()
        scanner.scan(archive_bytes, output.name)
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        if output.exists():
            if hashlib.sha256(output.read_bytes()).hexdigest() != checksum:
                raise ValueError("Existing execution archive differs; choose a new output path")
        else:
            temporary.replace(output)
        receipt = {
            "path": output.name,
            "sha256": checksum,
            "bytes": len(archive_bytes),
            "files": len(payloads),
            "zip_members": len(payloads) + 1,
            **manifest["counts"],
            "raw_payload_bytes": sum(map(len, payloads.values())),
            "study_sha256": study_hash,
            "manifest_sha256": hashlib.sha256(members[MANIFEST]).hexdigest(),
            "exact_member_and_hash_verification": "passed",
            "credential_scan": scanner.report(),
            "credential_sources": "Configured credentials from both .env and process environment, including JSON-escaped values",
            "omissions": OMISSIONS,
            "restore": "Verify every manifest hash before extracting paths into a separate repository checkout; supply core300-sources.zip separately. SQLite/checkpoint state is not restored.",
        }
        receipt_path = output.with_name(output.stem + "-archive.json")
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        return receipt
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--study", type=Path, default=Path("data/core300-formal-v1"))
    parser.add_argument("--output", type=Path, default=Path("docs/public-evaluation/core300-execution.zip"))
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.repository, args.repository / args.study, args.repository / args.output), indent=2
        )
    )


if __name__ == "__main__":
    main()
