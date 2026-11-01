"""Build a submission archive from an allowlist, with local credential exclusion."""

import hashlib
import io
import json
import os
import subprocess
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, is_zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL_FILES = (
    "README.md",
    "pyproject.toml",
    "requirements.lock",
    "Dockerfile",
    "compose.yaml",
    ".dockerignore",
    ".env.example",
    ".gitignore",
    ".gitattributes",
)
TOP_LEVEL_DIRS = (
    "app",
    "tests",
    "docs",
    "examples",
    "question",
    "scripts",
    "experiments",
    "evaluation",
    "frontend",
    ".github",
)
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git", ".venv", "dist", "node_modules"}


class ArchiveCredentialScanner:
    """Inspect bytes only; bounded ZIP/gzip recursion never extracts member paths.

    Limits count work across the entire package, including reads at each nesting
    level and gzip output writes. Unknown compression is not a credential scan.
    """

    CHUNK = 64 * 1024

    def __init__(self, secrets, *, max_depth=8, max_members=100000,
                 max_total_bytes=4 * 1024**3, max_member_bytes=512 * 1024**2):
        self.secrets = tuple(set(secret for secret in secrets if secret))
        self.overlap = max((len(secret) for secret in self.secrets), default=1) - 1
        self.max_depth, self.max_members = max_depth, max_members
        self.max_total_bytes, self.max_member_bytes = max_total_bytes, max_member_bytes
        self.total_bytes = self.members = self.archives = 0

    def _check(self, content):
        if any(secret in content for secret in self.secrets):
            # Neither credential values nor attacker-controlled member names are logged.
            raise RuntimeError("Credential found in package candidate or nested archive content")

    def _name(self, name):
        self._check(name.encode("utf-8", errors="replace"))
        parts = name.replace("\\", "/").rstrip("/").split("/")
        if not name or any(part in ("", ".", "..") or ":" in part or "\x00" in part for part in parts):
            raise RuntimeError("Unsafe member path in packaged archive")
        if any(part.rstrip(" .").casefold().startswith(".env") and part != ".env.example" for part in parts):
            raise RuntimeError("Private environment file in packaged archive")

    def _consume(self, count):
        self.total_bytes += count
        if self.total_bytes > self.max_total_bytes:
            raise RuntimeError("Archive credential scan exceeded its total byte budget")

    def _member(self):
        self.members += 1
        if self.members > self.max_members:
            raise RuntimeError("Archive credential scan exceeded its member budget")

    def _gzip_header(self, source):
        header = source.read(10)
        if len(header) != 10 or header[:3] != b"\x1f\x8b\x08" or header[3] & 0xE0:
            raise RuntimeError("Invalid gzip header in package")
        flags = header[3]
        if flags & 4:
            size = source.read(2)
            if len(size) != 2 or len(source.read(int.from_bytes(size, "little"))) != int.from_bytes(size, "little"):
                raise RuntimeError("Truncated gzip extra field")
        for flag in (8, 16):
            if flags & flag:
                field = bytearray()
                while True:
                    char = source.read(1)
                    if not char:
                        raise RuntimeError("Truncated gzip metadata")
                    if char == b"\x00":
                        break
                    field.extend(char)
                    if len(field) > self.CHUNK:
                        raise RuntimeError("Oversized gzip metadata")
                if flag == 8:
                    self._name(field.decode("latin-1"))
        if flags & 2 and len(source.read(2)) != 2:
            raise RuntimeError("Truncated gzip header checksum")

    def _gzip(self, payload, output):
        size = 0
        while True:
            # RFC gzip concatenation and zero padding; examine every member's name.
            chunk = payload.read(self.CHUNK)
            while chunk and not chunk.strip(b"\x00"):
                chunk = payload.read(self.CHUNK)
            if not chunk:
                break
            tail = chunk.lstrip(b"\x00")
            payload.seek(-len(tail), 1)
            start = payload.tell()
            self._member()
            self._gzip_header(payload)
            payload.seek(start)
            decoder = zlib.decompressobj(31)  # Includes each member's CRC/size validation.
            while not decoder.eof:
                compressed = payload.read(self.CHUNK)
                if not compressed:
                    raise RuntimeError("Truncated gzip payload")
                while compressed:
                    decoded = decoder.decompress(compressed, self.CHUNK)
                    size += len(decoded)
                    self._consume(len(decoded))
                    if size > self.max_member_bytes:
                        raise RuntimeError("Expanded gzip exceeds the member byte budget")
                    output.write(decoded)
                    if decoder.eof:
                        payload.seek(-len(decoder.unused_data), 1)
                        break
                    compressed = decoder.unconsumed_tail

    def scan(self, contents, name):
        self._stream(io.BytesIO(contents), name, 0)

    def _stream(self, source, name, depth):
        self._name(name)
        size, tail = 0, b""
        with tempfile.SpooledTemporaryFile(max_size=8 * self.CHUNK) as payload:
            while block := source.read(self.CHUNK):
                size += len(block)
                self._consume(len(block))
                if size > self.max_member_bytes:
                    raise RuntimeError("Archive member exceeds the scan byte budget")
                self._check(tail + block)
                tail = (tail + block)[-self.overlap:] if self.overlap else b""
                payload.write(block)
            payload.seek(0)
            prefix = payload.read(8)
            payload.seek(0)
            suffix = name.casefold()
            zipped = is_zipfile(payload)
            gzipped = prefix.startswith(b"\x1f\x8b")
            if zipped or gzipped or suffix.endswith((".zip", ".gz", ".gzip", ".tgz")):
                if depth >= self.max_depth:
                    raise RuntimeError("Archive credential scan exceeded its nesting budget")
                self.archives += 1
            payload.seek(0)
            try:
                if zipped:
                    with ZipFile(payload) as archive:
                        # ZIP comments and extra/name metadata were covered by the raw scan.
                        for member in archive.infolist():
                            self._member()
                            self._name(member.orig_filename)
                            if member.orig_filename != member.filename or member.flag_bits & 1:
                                raise RuntimeError("Invalid or encrypted nested ZIP member")
                            if member.file_size > self.max_member_bytes:
                                raise RuntimeError("ZIP member exceeds the scan byte budget")
                            if not member.is_dir():
                                with archive.open(member) as child:
                                    self._stream(child, member.filename, depth + 1)
                elif gzipped:
                    with tempfile.SpooledTemporaryFile(max_size=8 * self.CHUNK) as expanded:
                        self._gzip(payload, expanded)
                        expanded.seek(0)
                        child_name = name[:-4] + ".tar" if suffix.endswith(".tgz") else name.rsplit(".", 1)[0]
                        self._stream(expanded, child_name or "gzip-payload", depth + 1)
                elif suffix.endswith((".zip", ".gz", ".gzip", ".tgz")):
                    raise RuntimeError("Named archive could not be inspected")
                else:
                    payload.seek(257)
                    tar = payload.read(5) == b"ustar"
                    if tar or prefix.startswith((b"7z\xbc\xaf\x27\x1c", b"Rar!", b"BZh", b"\xfd7zXZ", b"\x28\xb5\x2f\xfd")) or suffix.endswith((".7z", ".rar", ".bz2", ".xz", ".zst", ".tar")):
                        raise RuntimeError("Unsupported archive compression; credential scan cannot certify it")
            except (BadZipFile, EOFError, OSError, zlib.error, NotImplementedError):
                raise RuntimeError("Unreadable archive in package credential scan") from None

    def report(self):
        return {"status": "passed", "formats": ["ZIP", "gzip including concatenated members"],
                "archives_checked": self.archives, "members_checked": self.members,
                "work_bytes_checked": self.total_bytes, "max_depth": self.max_depth,
                "max_members": self.max_members, "max_work_bytes": self.max_total_bytes,
                "max_member_bytes": self.max_member_bytes,
                "scope": "Configured literal credentials and private environment filenames; not generic secret discovery"}


def clean_revision():
    """A repository release must identify a committed, unmodified source tree."""
    if not (ROOT / ".git").exists():
        return None
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True
    )
    if status.strip():
        raise RuntimeError("Commit the complete release tree before packaging; working tree is dirty")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def main():
    commit = clean_revision()
    candidates = [ROOT / name for name in TOP_LEVEL_FILES]
    for directory in TOP_LEVEL_DIRS:
        candidates.extend(path for path in (ROOT / directory).rglob("*") if path.is_file())
    files = []
    for path in sorted(set(candidates)):
        relative = path.relative_to(ROOT)
        if not path.is_file() or set(relative.parts) & SKIP_PARTS:
            continue
        if path.is_symlink():
            raise RuntimeError("Refusing to package a symlink")
        if path.name.startswith(".env") and path.name != ".env.example":
            raise RuntimeError("A private environment file reached the package allowlist")
        files.append(path)
    credentials = dotenv_values(ROOT / ".env")
    keys = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "NEO4J_PASSWORD", "HF_TOKEN", "NCBI_API_KEY")
    secrets = [value.encode("utf-8") for key in keys
               for value in (credentials.get(key), os.environ.get(key)) if value]
    scanner = ArchiveCredentialScanner(secrets)
    hashes = {}
    for path in files:
        contents = path.read_bytes()
        scanner.scan(contents, path.relative_to(ROOT).as_posix())
        hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(contents).hexdigest()
    output = ROOT / "dist" / "cheiron-clinicaltrials-agent.zip"
    pending = output.with_suffix(".pending.zip")
    output.parent.mkdir(exist_ok=True)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "clean_working_tree": commit is not None,
        "credentials_included": False,
        "credential_scan": scanner.report(),
        "file_sha256": hashes,
    }
    with ZipFile(pending, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            name = path.relative_to(ROOT).as_posix()
            contents = path.read_bytes()
            if hashlib.sha256(contents).hexdigest() != hashes[name]:
                raise RuntimeError(f"Source changed while packaging: {name}")
            archive.writestr(name, contents)
        archive.writestr("BUILD-MANIFEST.json", json.dumps(manifest, indent=2))
    with ZipFile(pending) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        if any(Path(name).name == ".env" for name in archive.namelist()):
            raise RuntimeError("Private .env present in ZIP")
    if clean_revision() != commit:
        raise RuntimeError("Release revision changed while packaging")
    pending.replace(output)
    print(
        json.dumps(
            {
                "archive": str(output),
                "files": len(files),
                "bytes": output.stat().st_size,
                "credential_scan": "passed",
                "git_commit": commit,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
