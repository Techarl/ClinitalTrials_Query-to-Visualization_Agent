"""Build a submission archive from an allowlist, with local credential exclusion."""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

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


def main():
    candidates = [ROOT / name for name in TOP_LEVEL_FILES]
    for directory in TOP_LEVEL_DIRS:
        candidates.extend(path for path in (ROOT / directory).rglob("*") if path.is_file())
    files = []
    for path in sorted(set(candidates)):
        relative = path.relative_to(ROOT)
        if not path.is_file() or set(relative.parts) & SKIP_PARTS:
            continue
        if path.is_symlink():
            raise RuntimeError(f"Refusing to package a symlink: {relative}")
        if path.name.startswith(".env") and path.name != ".env.example":
            raise RuntimeError("A private environment file reached the package allowlist")
        files.append(path)
    credentials = dotenv_values(ROOT / ".env")
    secrets = [
        credentials[key].encode("utf-8")
        for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "NEO4J_PASSWORD", "HF_TOKEN")
        if credentials.get(key)
    ]
    hashes = {}
    for path in files:
        contents = path.read_bytes()
        if any(secret in contents for secret in secrets):
            raise RuntimeError(f"Credential found in package candidate: {path.relative_to(ROOT)}")
        hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(contents).hexdigest()
    output = ROOT / "dist" / "cheiron-clinicaltrials-agent.zip"
    output.parent.mkdir(exist_ok=True)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "credentials_included": False,
        "file_sha256": hashes,
    }
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr("BUILD-MANIFEST.json", json.dumps(manifest, indent=2))
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        if any(Path(name).name == ".env" for name in archive.namelist()):
            raise RuntimeError("Private .env present in ZIP")
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
