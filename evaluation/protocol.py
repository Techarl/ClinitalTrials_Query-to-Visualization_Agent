"""Immutable execution identity; never silently resume a different experiment."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.sources import content_hash


def execution_identity(settings, **parameters):
    names = [
        "openai_model",
        "openai_reasoning_effort",
        "openai_base_url",
        "openrouter_decision_model",
        "openrouter_decisions_url",
        "v2_max_model_calls",
        "v2_max_tool_calls",
        "v2_max_run_seconds",
        "v2_max_estimated_usd",
        "v2_context_chars",
    ]
    files = [p for folder in ("app", "evaluation", "experiments") for p in Path(folder).rglob("*.py")]
    files += [
        p for p in (Path("requirements.lock"), Path("experiments/requirements-rerank.txt")) if p.exists()
    ]
    return {
        "version": 1,
        "settings": {n: str(getattr(settings, n, None)) for n in names},
        "code": {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
        "parameters": parameters,
    }


def freeze(path, identity):
    path = Path(path)
    payload = {"sha256": content_hash(identity), "identity": identity}
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise ValueError(f"Frozen execution identity changed: {path}. Use a new study directory.")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
        if identity.get("code"):
            with ZipFile(path.with_suffix(".source.zip"), "x", ZIP_DEFLATED) as archive:
                for name, digest in identity["code"].items():
                    data = Path(name).read_bytes()
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise ValueError("Code changed during protocol freeze")
                    archive.writestr(name, data)
    return payload["sha256"]
