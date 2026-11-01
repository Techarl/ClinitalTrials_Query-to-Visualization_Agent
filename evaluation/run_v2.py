"""Explicit paid execution evaluation. Normal tests/CI never invoke this entrypoint."""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.config import Settings
from evaluation.contracts import Episode
from evaluation.protocol import execution_identity, freeze
from evaluation.runtime import PersistentBudget, run_episode, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", choices=["development", "calibration", "heldout", "all"], default="development"
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--family")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-usd", type=float, default=15)
    parser.add_argument("--output", type=Path, default=Path("data/vnext-eval-v1"))
    parser.add_argument("--cases", type=Path, default=Path("evaluation/cases_v2.json"))
    parser.add_argument("--frozen", type=Path, default=Path("examples"))
    args = parser.parse_args()
    corpus = json.loads(args.cases.read_text(encoding="utf-8"))
    episodes = [
        Episode.model_validate(e)
        for e in corpus["episodes"]
        if args.split == "all" or e["split"] == args.split
    ]
    if args.family:
        episodes = [e for e in episodes if e.family.startswith(args.family)]
    episodes = episodes[: args.limit] if args.limit else episodes
    args.output.mkdir(exist_ok=True, parents=True)
    budget = PersistentBudget(args.output / "ledger.json", args.max_usd)
    pending = episodes
    settings = Settings()
    freeze(
        args.output / "study.json",
        execution_identity(settings, episodes=[e.model_dump(mode="json") for e in episodes]),
    )
    freeze(
        args.output / "environment.json",
        {
            "model": settings.openai_model,
            "reasoning": settings.openai_reasoning_effort,
            "budgets": {
                k: getattr(settings, k)
                for k in [
                    "v2_max_model_calls",
                    "v2_max_tool_calls",
                    "v2_max_run_seconds",
                    "v2_max_estimated_usd",
                    "v2_context_chars",
                ]
            },
            "source_code_sha256": {
                p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in ["app", "evaluation"]
                for p in Path(folder).rglob("*.py")
            },
            "case_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "dependency_sha256": hashlib.sha256(Path("requirements.lock").read_bytes()).hexdigest(),
            "source_manifests_sha256": {
                p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in [str(args.frozen), "evaluation/frozen"]
                for p in Path(folder).rglob("manifest.json")
            },
        },
    )
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_episode, e, settings, args.output / e.id, budget, args.frozen): e
            for e in pending
        }
        for future in as_completed(futures):
            episode = futures[future]
            try:
                result = future.result()
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as error:
                write(
                    args.output / episode.id / "failure.json",
                    {
                        "episode_id": episode.id,
                        "split": episode.split,
                        "correct": False,
                        "runner_error": type(error).__name__,
                        "status": "runner_failed",
                    },
                )
                print(
                    json.dumps(
                        {
                            "episode_id": episode.id,
                            "runner_error": type(error).__name__,
                            "message": str(error)[:200],
                        }
                    ),
                    flush=True,
                )
    rows = []
    for episode in episodes:
        directory = args.output / episode.id
        path = directory / "summary.json"
        if not path.exists():
            path = directory / "failure.json"
        rows.append(
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {
                "episode_id": episode.id,
                "split": episode.split,
                "correct": False,
                "status": "not_run",
            }
        )
    write(
        args.output / "report.json",
        {
            "version": "vnext-execution-eval-v1",
            "corpus": corpus["split_audit"],
            "episodes": rows,
            "summary": {
                split: {
                    "episodes": sum(r["split"] == split for r in rows),
                    "correct": sum(r["correct"] and r["split"] == split for r in rows),
                }
                for split in ["development", "calibration", "heldout"]
            },
            "budget": budget.report(),
            "limits": [
                "Entity-disjoint; capability templates recur.",
                "Not a statistically representative benchmark.",
                "Automatic explanation/actionability checks require human spot review.",
            ],
        },
    )


if __name__ == "__main__":
    main()
