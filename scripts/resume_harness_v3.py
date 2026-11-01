"""Desktop-crash recovery amendment; leave the archived inference source unchanged.

Completed task/retry records are opaque caches. Incomplete retry loops reconstruct
every saved candidate before making any new reservation. Original deadlines,
twenty-attempt cap, USD ceiling, public selection and unknown usage stay intact.
"""

import argparse
import functools
import json
import os
import sys
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resumed_retry(api, episode, settings, root, budget, frozen, baseline, allocation):
    api.freeze(
        root / "retry-protocol.json",
        {
            "allocation_usd": allocation,
            "baseline": str(baseline),
            "selection": "max public quality; no hidden labels",
        },
    )
    if (root / "result.json").exists():
        return read(root / "result.json")
    existing = sorted(int(p.name.split("-")[1]) for p in root.glob("attempt-*") if p.is_dir())
    if existing and existing != list(range(existing[-1] + 1)):
        raise ValueError("Retry cache has missing intermediate attempts")
    if existing and existing[-1] >= 20:
        raise ValueError("Retry cache exceeds frozen twenty-attempt cap")
    paths, attempts = [baseline], []
    scope = str(root)
    with budget.scope(scope, allocation):
        for index in range(20):
            path = root / f"attempt-{index}"
            cached = (path / "summary.json").exists()
            pending = any(path.glob("turn-*-run.json"))
            # Reconstruct all past candidates even when their cumulative spend
            # already reached the cap. Never start a new attempt beyond the cap.
            if not cached and not pending and api.metrics(budget.scope_events())["usd"] >= allocation:
                break
            previous_calls = len(budget.events)
            try:
                summary = api.run_episode(episode, settings, path, budget, frozen)
            except RuntimeError as error:
                attempts.append({"error": str(error), "trace": str(path)})
                break
            if (path / "turn-1.json").exists():
                paths.append(path)
            attempts.append({"summary": summary, "trace": str(path)})
            if cached:
                if summary.get("model_calls", 0) == 0:
                    if any(i > index for i in existing):
                        raise ValueError("Retry cache continues beyond a recorded zero-call stop")
                    break
                continue
            if len(budget.events) == previous_calls or summary.get("model_calls", 0) == 0:
                break
            if api.metrics(budget.scope_events())["usd"] >= allocation:
                break
    selected = max(range(len(paths)), key=lambda i: api.quality(api.public_episode_probe(paths[i])))
    result = {
        "extra_allocation_usd": allocation,
        "actual": api.metrics(budget.scope_events(scope)),
        "attempts": attempts,
        "selected_trace": str(paths[selected]),
        "selected_summary": read(paths[selected] / "summary.json"),
        "selection_rule": "Public completion, errors, calls, tokens, cost; grades report-only",
        "limits": "Conservative pre-request reservations can leave an unusable residual; at most 20 retries.",
    }
    api.write(root / "result.json", result)
    return result


def cached_task(original, api, episode, settings, path, shared, frozen, bank):
    saved = path / "result.json"
    if not saved.exists():
        return original(episode, settings, path, shared, frozen, bank)
    row = read(saved)
    if row["episode_id"] != episode.id or row["incoming_bank_sha256"] != api.content_hash(bank):
        raise ValueError("Completed task cache identity mismatch")
    row.setdefault("infrastructure_failure_receipts", api.infrastructure_failures(path))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capsule", type=Path, required=True)
    args, rest = parser.parse_known_args()
    capsule = args.capsule.resolve()
    os.chdir(capsule)
    sys.path.insert(0, str(capsule))
    from experiments import harness_v3 as api
    from experiments import harness_v3_parallel as driver

    api.retry_selection = functools.partial(resumed_retry, api)
    cache_api = type(
        "CacheAPI",
        (),
        {
            "content_hash": staticmethod(api.content_hash),
            "infrastructure_failures": staticmethod(driver.infrastructure_failures),
        },
    )
    driver.run_one = functools.partial(cached_task, driver.run_one, cache_api)
    sys.argv = [str(capsule / "experiments/harness_v3_parallel.py"), *rest]
    driver.main()


if __name__ == "__main__":
    main()
