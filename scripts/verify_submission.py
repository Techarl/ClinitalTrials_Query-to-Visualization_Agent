"""Independently verify a release ZIP and optionally extract it into a new directory.

This verifies integrity and declared revision identity, not Git authenticity, absence
of arbitrary embedded secrets, dependency installation, or application behavior.
"""

import argparse
import hashlib
import json
import re
import stat
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zipfile import BadZipFile, ZipFile

MANIFEST = "BUILD-MANIFEST.json"
FORBIDDEN_PARTS = {"node_modules", ".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache"}
WINDOWS_DEVICES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {
    prefix + digit for prefix in ("com", "lpt") for digit in "123456789¹²³"
}
CHUNK = 1024 * 1024


class VerificationError(ValueError):
    """The archive does not satisfy the submission contract."""


def safe_path(name, directory=False):
    if not isinstance(name, str) or not name or "\\" in name:
        raise VerificationError("Archive paths must be nonempty relative POSIX paths")
    path = name[:-1] if directory and name.endswith("/") else name
    parts = path.split("/")
    for part in parts:
        if (
            part in ("", ".", "..")
            or part.rstrip(" .") != part
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or part.split(".")[0].rstrip(" ").casefold() in WINDOWS_DEVICES
        ):
            raise VerificationError(f"Unsafe archive path: {name!r}")
        if part.casefold() in FORBIDDEN_PARTS or (
            part.casefold().startswith(".env") and part != ".env.example"
        ):
            raise VerificationError(f"Excluded environment/dependency path: {name!r}")
    return path


def unique_object(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise VerificationError(f"Duplicate manifest JSON key: {key!r}")
        output[key] = value
    return output


def stream_hash(stream):
    digest, size = hashlib.sha256(), 0
    while block := stream.read(CHUNK):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def inspect_archive(archive, max_uncompressed_bytes):
    files, known, aliases = {}, {}, {}
    declared_total = 0
    for info in archive.infolist():
        if info.orig_filename != info.filename:
            raise VerificationError("Truncated or NUL-containing ZIP member name")
        directory = info.is_dir()
        name = safe_path(info.filename, directory)
        if name in known:
            raise VerificationError(f"Duplicate ZIP member: {name!r}")
        known[name] = directory
        for index in range(1, len(name.split("/")) + 1):
            prefix = "/".join(name.split("/")[:index])
            key = unicodedata.normalize("NFC", prefix).casefold()
            if key in aliases and aliases[key] != prefix:
                raise VerificationError(f"Case/normalization-colliding ZIP path: {name!r}")
            aliases[key] = prefix
        mode = stat.S_IFMT(info.external_attr >> 16)
        if mode not in (0, stat.S_IFDIR if directory else stat.S_IFREG):
            raise VerificationError(f"Non-regular ZIP member: {name!r}")
        if info.flag_bits & 1:
            raise VerificationError("Encrypted ZIP members are not supported")
        if directory:
            if info.file_size:
                raise VerificationError("Directory entry contains payload")
        else:
            files[name] = info
        declared_total += info.file_size
        if declared_total > max_uncompressed_bytes:
            raise VerificationError("Archive exceeds the uncompressed-byte limit")
    for name in known:
        parts = name.split("/")
        if any("/".join(parts[:i]) in files for i in range(1, len(parts))):
            raise VerificationError(f"File used as parent directory: {name!r}")
    if MANIFEST not in files or files[MANIFEST].file_size > 16 * CHUNK:
        raise VerificationError("Missing or oversized BUILD-MANIFEST.json")
    try:
        manifest = json.loads(archive.read(files[MANIFEST]), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError("Invalid manifest JSON") from error
    if not isinstance(manifest, dict):
        raise VerificationError("Manifest must be a JSON object")
    commit = manifest.get("git_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise VerificationError("Manifest needs an explicit full Git commit SHA")
    if manifest.get("clean_working_tree") is not True or manifest.get("credentials_included") is not False:
        raise VerificationError("Manifest must explicitly declare a clean tree and excluded credentials")
    expected = manifest.get("file_sha256")
    if not isinstance(expected, dict):
        raise VerificationError("Manifest file_sha256 must be an object")
    for name, digest in expected.items():
        safe_path(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise VerificationError(f"Invalid file SHA256: {name!r}")
    if set(expected) != set(files) - {MANIFEST}:
        missing = sorted(set(expected) - (set(files) - {MANIFEST}))
        unlisted = sorted((set(files) - {MANIFEST}) - set(expected))
        raise VerificationError(f"Manifest/file mismatch: missing={missing!r}; unlisted={unlisted!r}")
    # Stream every file, including the manifest, forcing ZIP CRC checks as well.
    for name, info in files.items():
        with archive.open(info) as source:
            actual, size = stream_hash(source)
        if size != info.file_size or (name != MANIFEST and actual != expected[name]):
            raise VerificationError(f"File integrity mismatch: {name!r}")
    return manifest, files, declared_total


def extract_verified(archive, files, destination, expected):
    destination.mkdir(exist_ok=False)  # Parent must already exist; never overwrite.
    root = destination.resolve(strict=True)
    for name, info in files.items():
        target = root.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        for parent in (target.parent, *target.parent.parents):
            if parent == root:
                break
            if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                raise VerificationError("Extraction encountered a redirected directory")
        if not target.resolve().is_relative_to(root):
            raise VerificationError("Extraction target escaped the new workspace")
        digest, size = hashlib.sha256(), 0
        with archive.open(info) as source, target.open("xb") as output:
            while block := source.read(CHUNK):
                output.write(block)
                digest.update(block)
                size += len(block)
        if size != info.file_size or (name != MANIFEST and digest.hexdigest() != expected[name]):
            raise VerificationError("Archive changed during extraction; partial workspace retained")
    return str(root)


def verify_submission(zip_path, *, extract=None, receipt=None, expected_commit=None,
                      max_uncompressed_bytes=2 * 1024**3):
    """Validate before creating output; never replace existing workspace/receipt files."""
    zip_path = Path(zip_path).resolve(strict=True)
    receipt = Path(receipt) if receipt else zip_path.with_name(zip_path.name + ".verification.json")
    destination = Path(extract) if extract is not None else None
    if receipt.exists() or receipt.is_symlink():
        raise VerificationError("Receipt already exists; choose a new --receipt path")
    if not receipt.parent.is_dir():
        raise VerificationError("Receipt parent directory must already exist")
    if destination is not None:
        if destination.exists() or destination.is_symlink():
            raise VerificationError("Extraction destination already exists; choose a new directory")
        if not destination.parent.is_dir():
            raise VerificationError("Extraction parent directory must already exist")
        if receipt.resolve().is_relative_to(destination.resolve()):
            raise VerificationError("The verification receipt must be outside the extracted workspace")
    if max_uncompressed_bytes <= 0:
        raise VerificationError("Uncompressed-byte limit must be positive")
    try:
        with zip_path.open("rb") as source:
            zip_sha, zip_size = stream_hash(source)
            source.seek(0)
            with ZipFile(source) as archive:
                manifest, files, size = inspect_archive(archive, max_uncompressed_bytes)
                if expected_commit is not None and manifest["git_commit"] != expected_commit:
                    raise VerificationError("ZIP revision does not match --expected-commit")
                source.seek(0)
                if stream_hash(source)[0] != zip_sha:
                    raise VerificationError("ZIP changed during verification")
                extracted = extract_verified(archive, files, destination, manifest["file_sha256"]) if destination else None
            source.seek(0)
            if stream_hash(source)[0] != zip_sha:
                raise VerificationError("ZIP changed during verification/extraction")
    except (BadZipFile, RuntimeError, NotImplementedError) as error:
        raise VerificationError(f"ZIP integrity or encoding failure: {type(error).__name__}") from error
    result = {
        "schema": "submission-verification-v1", "status": "verified",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "archive": str(zip_path), "zip_sha256": zip_sha, "zip_bytes": zip_size,
        "git_commit": manifest["git_commit"], "expected_commit": expected_commit,
        "manifest_files_verified": len(manifest["file_sha256"]), "uncompressed_bytes": size,
        "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "manifest_hash_scope": "parsed_canonical_json",
        "extracted_to": extracted,
        "checks": ["all_member_crc_and_hashes", "exact_manifest_file_set", "unique_safe_paths",
                   "no_environment_or_dependency_directories", "explicit_clean_revision"],
        "limitations": "Verifies archive integrity and declared commit only; not commit authenticity, a content secret scan, installation, or application execution.",
    }
    with receipt.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path)
    parser.add_argument("--extract", type=Path, help="New workspace directory; parent must exist")
    parser.add_argument("--receipt", type=Path, help="New sidecar JSON; defaults to ZIP.verification.json")
    parser.add_argument("--expected-commit", help="Require the exact release commit SHA")
    parser.add_argument("--max-uncompressed-bytes", type=int, default=2 * 1024**3)
    args = parser.parse_args()
    try:
        result = verify_submission(args.zip, extract=args.extract, receipt=args.receipt,
                                   expected_commit=args.expected_commit,
                                   max_uncompressed_bytes=args.max_uncompressed_bytes)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Submission verification failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
