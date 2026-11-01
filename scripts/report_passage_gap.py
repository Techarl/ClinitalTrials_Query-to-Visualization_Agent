"""Export the separate post-hoc recovery probe without relabeling the primary study."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import dotenv_values

from app.v2.evidence_selection import Passage
from evaluation.runtime import write
from experiments.passage_selection import metrics


def main():
    root, output = Path("data/passage-gap-recovery"), Path("docs/passage-selection")
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    corpus = json.loads(Path("evaluation/passage_selection.json").read_text(encoding="utf-8"))
    case = next(c for c in corpus["cases"] if c["id"] == result["case"])
    state = result["state"]
    recomputed = metrics(
        case,
        [Passage.model_validate(p) for p in state["pool"]],
        state["ranking"],
        state["packing"],
        state["result"],
    )
    if recomputed != result["final_metrics"]:
        raise ValueError("Recovery metrics differ on recomputation")
    payloads = {p.name: p.read_bytes() for p in root.glob("*.json") if p.name != "embeddings.json"}
    payloads["source/experiments/passage_gap_recovery.py"] = Path(
        "experiments/passage_gap_recovery.py"
    ).read_bytes()
    payloads["primary-frozen-protocol.json"] = Path(
        "data/passage-selection-v1/frozen-protocol.json"
    ).read_bytes()
    secrets = [
        v.encode()
        for k, v in dotenv_values(".env").items()
        if v and k in {"OPENAI_API_KEY", "OPENROUTER_API_KEY", "NEO4J_PASSWORD"}
    ]
    if any(secret in content for content in payloads.values() for secret in secrets):
        raise ValueError("Credential found in diagnostic export")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "gap-recovery.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for name, contents in payloads.items():
            bundle.writestr(name, contents)
        bundle.writestr(
            "SHA256-MANIFEST.json",
            json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in payloads.items()}, indent=2),
        )
    summary = {k: v for k, v in result.items() if k not in {"state", "budget"}}
    summary.update(
        attempts=result["budget"]["attempts"],
        estimated_or_reserved_usd=result["budget"]["estimated_or_reserved_usd"],
        unknown_attempts=sum(e["status"] != "completed" for e in result["budget"]["events"]),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        credential_scan="passed",
    )
    write(output / "gap-recovery.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
