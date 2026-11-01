"""Freeze the secondary rule control before any heldout query has run."""

import argparse
import json
from pathlib import Path

from app.sources import content_hash
from evaluation.runtime import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("data/passage-selection-v1"))
    args = parser.parse_args()
    value = {
        "declared_before_heldout": True,
        "policy": "Filter the identical candidate pool to literal NCT identifiers in query; preserve hybrid order. No model ranker, no replacement candidates, same answer model and cap.",
        "script_hash": content_hash(Path("experiments/passage_scope_control.py").read_text(encoding="utf-8")),
    }
    path = args.directory / "scope-control-protocol.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError("Existing scope-control protocol differs")
        print("Existing scope-control protocol matches")
        return
    corpus = json.loads(Path("evaluation/passage_selection.json").read_text(encoding="utf-8"))
    if any((args.directory / c["id"]).exists() for c in corpus["cases"] if c["split"] == "heldout"):
        raise ValueError("Cannot preregister after heldout execution begins")
    write(path, value)
    print("Scope-control policy frozen before heldout")


if __name__ == "__main__":
    main()
