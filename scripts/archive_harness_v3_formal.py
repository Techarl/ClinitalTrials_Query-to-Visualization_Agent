"""Publish a closed Harness formal record set, after independent reconciliation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from dotenv import dotenv_values

from scripts.package_submission import ArchiveCredentialScanner
from scripts.report_harness_v3 import report


def records(study):
    if not (study / "completed-or-stopped-ledger.json").exists():
        raise ValueError("Formal producer has not closed")
    selected = []
    for path in sorted(study.rglob("*.json")):
        relative = path.relative_to(study)
        if "runtime" in relative.parts or path.name == "ledger.json":
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(study.resolve()):
            raise ValueError("Record path escapes the closed study")
        selected.append(path)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=Path("data/harness-v3-formal05"))
    parser.add_argument("--cases", type=Path, default=Path("data/core300-frozen/cases.json"))
    parser.add_argument(
        "--destination", type=Path, default=Path("docs/public-evaluation/harness-v3-archives")
    )
    args = parser.parse_args()
    selected = records(args.study)
    result = report(args.study, args.cases)
    credentials = dotenv_values(".env")
    keys = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "NEO4J_PASSWORD", "HF_TOKEN", "NCBI_API_KEY")
    secrets = [
        value.encode() for key in keys for value in (credentials.get(key), os.environ.get(key)) if value
    ]
    scanner = ArchiveCredentialScanner(secrets)
    args.destination.mkdir(parents=True, exist_ok=True)
    output = args.destination / "formal-records.zip"
    pending = output.with_suffix(".pending.zip")
    manifest = {}
    with ZipFile(pending, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in selected:
            name = f"{args.study.name}/{path.relative_to(args.study).as_posix()}"
            contents = path.read_bytes()
            scanner.scan(contents, name)
            manifest[name] = hashlib.sha256(contents).hexdigest()
            info = ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, contents)
        info = ZipInfo("SHA256-MANIFEST.json", date_time=(2000, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, indent=2).encode())
    with ZipFile(pending) as archive:
        if archive.testzip():
            raise ValueError("Formal archive integrity check failed")
    scanner.scan(pending.read_bytes(), output.name)
    pending.replace(output)
    report_path = args.destination.parent / "harness-v3-formal-results.json"
    report_bytes = json.dumps(result, indent=2, ensure_ascii=False).encode()
    scanner.scan(report_bytes, report_path.name)
    report_path.write_bytes(report_bytes)
    index_path = args.destination / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["formal_status"] = (
        "closed complete" if result["complete"] else "closed incomplete; planned denominator retained"
    )
    index["formal_record_files"] = len(selected)
    index["formal_record_scope"] = (
        "All closed non-runtime JSON, including the full closed ledger, proposals, probes and selected/unselected traces. Active ledger alias and duplicated runtime SQLite/snapshots excluded."
    )
    index["files"][output.name] = {
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "bytes": output.stat().st_size,
    }
    index["formal_reporting"] = {
        "path": "../" + report_path.name,
        "sha256": hashlib.sha256(report_bytes).hexdigest(),
        "planned_tasks": 40,
        "observed_tasks": result["observed_complete_task_rows"],
        "credential_scan": scanner.report(),
    }
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(index["formal_reporting"]))


if __name__ == "__main__":
    main()
