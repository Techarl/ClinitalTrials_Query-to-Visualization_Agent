"""Secondary deterministic ID-scope control, declared before heldout execution."""

import argparse
import json
import re
import time
from pathlib import Path

from openai import OpenAI

from app.config import Settings
from app.sources import content_hash
from app.v2.evidence_selection import Passage, pack_context
from evaluation.runtime import PersistentBudget, write
from experiments.passage_selection import answer, metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/passage-selection-v1"))
    args = parser.parse_args()
    import tiktoken

    tokenizer = tiktoken.get_encoding("cl100k_base")
    protocol = json.loads((args.input / "scope-control-protocol.json").read_text(encoding="utf-8"))
    if protocol["script_hash"] != content_hash(Path(__file__).read_text(encoding="utf-8")):
        raise ValueError("Secondary control changed after preregistration")
    if not (args.input / "heldout-complete.json").exists():
        raise ValueError("Finish the four primary arms before this secondary diagnostic")
    settings = Settings()
    corpus = json.loads(Path("evaluation/passage_selection.json").read_text(encoding="utf-8"))
    budget = PersistentBudget(args.input / "budget.json", max_usd=6, max_calls=600)
    with OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=str(settings.openai_base_url),
        max_retries=0,
    ) as client:
        for case in corpus["cases"]:
            directory = args.input / case["id"]
            output = directory / "scoped_hybrid.json"
            if output.exists():
                continue
            receipt = json.loads((directory / "pool.json").read_text(encoding="utf-8"))
            pool = [Passage.model_validate(p) for p in receipt["passages"]]
            started = time.monotonic()
            requested = set(re.findall(r"\bNCT\d{8}\b", case["query"]))
            ids = [p.id for p in pool if not requested or p.nct_id in requested]
            rank_seconds = time.monotonic() - started
            packed = pack_context(
                pool,
                ids,
                corpus["protocol"]["context_tokens"],
                lambda text: len(tokenizer.encode(text, disallowed_special=())),
            )
            response = answer(client, settings, case["query"], packed, budget)
            row = {
                "case_id": case["id"],
                "split": case["split"],
                "arm": "scoped_hybrid",
                "requested_trial_ids": sorted(requested),
                "ranking": ids,
                "rank_seconds": rank_seconds,
                "packing": packed,
                **response,
                "metrics": metrics(case, pool, ids, packed, response),
            }
            write(output, row)
            print(
                json.dumps({"case": case["id"], "arm": "scoped_hybrid", "metrics": row["metrics"]}),
                flush=True,
            )
    write(args.input / "scope-control-complete.json", {"budget": budget.report(), "protocol": protocol})


if __name__ == "__main__":
    main()
