"""Archive only the independently frozen public CTgov core benchmark inputs."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import dotenv_values


def main():
    root = Path("data/core300-frozen")
    output = Path("docs/public-evaluation/core300-sources.zip")
    files = sorted(root.rglob("*.json"))
    if not files or not (root / "cases.json").exists():
        raise ValueError("Complete frozen core inputs are required")
    secrets = [
        value.encode()
        for key, value in dotenv_values(".env").items()
        if value and key in {"OPENAI_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN", "NEO4J_PASSWORD"}
    ]
    manifest = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".pending")
    with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Only files within the frozen public core dataset may be archived")
            data = path.read_bytes()
            if any(secret in data for secret in secrets):
                raise ValueError("Credential detected in public data archive")
            name = path.as_posix()
            manifest[name] = hashlib.sha256(data).hexdigest()
            archive.writestr(name, data)
        archive.writestr("SHA256-MANIFEST.json", json.dumps(manifest, indent=2))
    with ZipFile(temporary) as archive:
        if archive.testzip():
            raise ValueError("Archive integrity check failed")
    temporary.replace(output)
    receipt = {
        "path": output.name,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "bytes": output.stat().st_size,
        "files": len(files),
        "credential_scan": "passed",
        "contents": "Public ClinicalTrials.gov raw snapshots, manifests and authored template tasks only. No NLI4CT or gated TrialQA dataset.",
        "restore": "Extract into the repository root; verify every SHA256-MANIFEST entry before evaluation.",
    }
    output.with_name("core300-source-archive.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
