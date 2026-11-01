"""Offline regrade of preserved traces; no paid calls, source requests or graph mutations."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.models import Filters
from app.sources import SnapshotStore
from app.storage import StateStore
from app.v2.contracts import RunResult
from app.v2.store import V2Store
from app.v2.tools import ToolRegistry
from evaluation.runtime import write
from evaluation.scorers import SCORER_VERSION, grade


def rescore(root):
    corpus = {
        e["id"]: e
        for e in json.loads(Path("evaluation/cases_v2.json").read_text(encoding="utf-8"))["episodes"]
    }
    rows = []
    for path in sorted(Path(root).rglob("summary.json")):
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("episode_id") not in corpus:
            continue
        episode = corpus[old["episode_id"]]
        base = StateStore(path.parent / "runtime")
        registry = ToolRegistry(
            Settings(_env_file=None, app_data_dir=path.parent / "runtime"),
            SimpleNamespace(snapshots=SnapshotStore(path.parent / "runtime")),
            V2Store(base),
        )
        turns = []
        for index, spec in enumerate(episode["turns"][: old["turns"]]):
            trace = json.loads((path.parent / f"turn-{index + 1}.json").read_text(encoding="utf-8"))
            response = deepcopy(trace["response"])
            state = deepcopy(trace["state"])
            ignored = []
            if response.get("task"):
                allowed = set(Filters.model_fields) | {"date_range", "intervention_scope"}
                ignored = [k for k in response["task"].get("clear_fields", []) if k not in allowed]
                response["task"]["clear_fields"] = [
                    k for k in response["task"].get("clear_fields", []) if k in allowed
                ]
                state["task"]["clear_fields"] = response["task"]["clear_fields"]
            turns.append(grade(RunResult.model_validate(response), spec["expected"], registry, state))
            turns[-1]["legacy_ignored_clear_field_names"] = ignored
        rows.append(
            {
                **old,
                "trace": path.parent.relative_to(root).as_posix(),
                "original_correct": old["correct"],
                "correct": all(t["correct"] for t in turns),
                "failures": [t["failures"] for t in turns],
                "grades": turns,
            }
        )
    report = {
        "scorer_version": SCORER_VERSION,
        "scorer_sha256": hashlib.sha256(Path("evaluation/scorers.py").read_bytes()).hexdigest(),
        "network_calls": 0,
        "model_calls": 0,
        "rows": rows,
        "summary": {
            "episodes": len(rows),
            "correct": sum(r["correct"] for r in rows),
            "changed": sum(r["correct"] != r["original_correct"] for r in rows),
        },
        "note": "Original execution traces and grades are retained; this report checks unrequested restrictions too.",
    }
    write(Path(root) / "rescored.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    result = rescore(args.root)
    print(json.dumps(result["summary"]))
