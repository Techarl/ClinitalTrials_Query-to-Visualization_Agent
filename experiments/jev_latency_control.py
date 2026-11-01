"""Small, opt-in transport latency control. No labels or quality selection are used."""

import argparse
import hashlib
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from openai import OpenAI

from app.config import Settings
from app.sources import content_hash, utc_now
from evaluation.protocol import freeze
from evaluation.runtime import PersistentBudget, write
from experiments.passage_selection import RELEVANCE, Relevance

SEED = 20261101


def choose_pairs(tasks, count=8):
    """One input-only hashed sentence per hashed dev question; never relevance labels."""
    selected = sorted(tasks, key=lambda t: content_hash(["latency-dev-v1", t["id"]]))[:count]
    pairs = []
    for task in selected:
        passage = min(task["passages"], key=lambda p: content_hash(["latency-passage-v1", p["id"]]))
        pairs.append(
            {
                "pair_id": content_hash([task["id"], passage["id"]])[:24],
                "case_id": task["id"],
                "passage_id": passage["id"],
                "state": {
                    "query": "Find evidence supporting OR contradicting this statement about the provided trial(s): "
                    + task["statement"],
                    "passage": passage,
                },
            }
        )
    return pairs


def create_client(provider, settings, concurrency):
    transport = httpx.Client(
        timeout=75,
        limits=httpx.Limits(
            max_connections=concurrency, max_keepalive_connections=concurrency, keepalive_expiry=120
        ),
        http2=False,
    )
    if provider == "jev":
        return transport
    return OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=str(settings.openai_base_url),
        max_retries=0,
        http_client=transport,
    )


def invoke(provider, client, settings, state, account):
    if provider == "jev":
        response = client.post(
            str(settings.openrouter_decisions_url),
            headers={"Authorization": "Bearer " + settings.openrouter_api_key.get_secret_value()},
            json={"model": settings.openrouter_decision_model, "state": state, "questions": RELEVANCE},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Decision request HTTP {response.status_code}")
        payload = response.json()
        account(
            {
                "provider": "openrouter_decisions",
                "reported_model": payload.get("model"),
                "response_id": payload.get("id"),
                "usage": payload.get("usage"),
            }
        )
        return Relevance(relevant=payload["answers"]["relevant"]["noul"]).relevant
    response = client.responses.parse(
        model="gpt-6-luna",
        reasoning={"effort": "low"},
        store=False,
        max_output_tokens=512,
        timeout=75,
        text_format=Relevance,
        input=[
            {"role": "system", "content": json.dumps(RELEVANCE)},
            {"role": "user", "content": json.dumps(state)},
        ],
    )
    account(
        {
            "provider": "openai",
            "requested_model": "gpt-6-luna",
            "reported_model": response.model,
            "response_id": response.id,
            "usage": response.usage.model_dump() if response.usage else None,
        }
    )
    if response.output_parsed is None:
        raise ValueError("Missing relevance response")
    return response.output_parsed.relevant


def measured_call(provider, mode, concurrency, pair, stage, settings, budget, shared=None):
    encoded = json.dumps([pair["state"], RELEVANCE]).encode()
    with budget.lock:
        event = budget.before(len(encoded), 512)
        event.update(
            provider_arm=provider,
            client_mode=mode,
            concurrency=concurrency,
            pair_id=pair["pair_id"],
            stage=stage,
        )
        write(budget.path, budget.report())
    started = time.perf_counter()
    client = shared
    try:
        if client is None:
            client = create_client(provider, settings, concurrency)
        score = invoke(provider, client, settings, pair["state"], lambda meta: budget.after(event, meta))
        result = {"status": "completed", "score": score}
    except Exception as error:
        # Pending reservations survive every network/parser failure. Never print response bodies or keys.
        result = {"status": "failed", "error_type": type(error).__name__}
    finally:
        response_seconds = time.perf_counter() - started
        if shared is None and client is not None:
            client.close()
    return {
        **result,
        "pair_id": pair["pair_id"],
        "stage": stage,
        "response_seconds": response_seconds,
        "total_seconds": time.perf_counter() - started,
        "input_bytes": len(encoded),
        "usage_status": event["status"],
        "estimated_or_reserved_usd": event.get("estimated_cost_usd", event["reserved_upper_estimate_usd"]),
    }


def percentile(values, q):
    """Linear interpolation, stated explicitly for these small samples."""
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def summarize(rows, wall_seconds):
    measured = [r for r in rows if r["stage"] == "measured"]
    successful = [r for r in measured if r["status"] == "completed"]
    times = [r["response_seconds"] for r in successful]
    return {
        "attempts": len(measured),
        "successful": len(successful),
        "failed": len(measured) - len(successful),
        "p50_seconds": percentile(times, 0.5),
        "p95_seconds": percentile(times, 0.95),
        "batch_wall_seconds": wall_seconds,
        "warmup_attempts": sum(r["stage"] == "warmup" for r in rows),
        "warmup_estimated_or_reserved_usd": sum(
            r["estimated_or_reserved_usd"] for r in rows if r["stage"] == "warmup"
        ),
        "measured_estimated_or_reserved_usd": sum(r["estimated_or_reserved_usd"] for r in measured),
        "measured_input_bytes": sum(r["input_bytes"] for r in measured),
    }


def run(args):
    if not 0 < args.max_usd <= 1:
        raise ValueError("This diagnostic is authorized for at most $1")
    if not 4 <= args.pairs <= 16:
        raise ValueError("Use 4–16 development pairs")
    source = args.data / "nli4ct-dev-inputs.json"
    tasks = json.loads(source.read_text(encoding="utf-8"))
    pairs = choose_pairs(tasks, args.pairs)
    settings = Settings()
    cells = [
        (p, mode, c) for p in ("jev", "luna") for mode in ("new_per_request", "persistent") for c in (1, 4)
    ]
    random.Random(SEED).shuffle(cells)
    source_files = [
        "experiments/jev_latency_control.py",
        "experiments/passage_selection.py",
        "evaluation/protocol.py",
        "evaluation/runtime.py",
        "experiments/budget.py",
        "app/v2/costs.py",
        "requirements.lock",
    ]
    protocol_path = args.output / "protocol.json"
    registered = (
        json.loads(protocol_path.read_text(encoding="utf-8"))["identity"]["registered_at"]
        if protocol_path.exists()
        else utc_now()
    )
    identity = {
        "registered_at": registered,
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "pair_ids": [p["pair_id"] for p in pairs],
        "pair_payload_hashes": [content_hash(p["state"]) for p in pairs],
        "cell_order": [list(cell) for cell in cells],
        "seed": SEED,
        "max_usd": args.max_usd,
        "max_attempts": 160,
        "providers": {
            "jev": {
                "endpoint": str(settings.openrouter_decisions_url),
                "model": settings.openrouter_decision_model,
            },
            "luna": {"endpoint": str(settings.openai_base_url), "model": "gpt-6-luna", "reasoning": "low"},
        },
        "shared_rubric": RELEVANCE,
        "max_output_tokens_luna": 512,
        "http2": False,
        "keepalive_expiry_seconds": 120,
        "warmup": "One wave of c calls per cell. Persistent cells retain the same client; new-per-request cells discard each warmup client. All calls charged.",
        "timing": "Response wall time includes client construction only for new_per_request; client close is separately included in total_seconds. A new client does not imply a cold provider/model cache.",
        "code": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in source_files},
    }
    digest = freeze(protocol_path, identity)
    budget = PersistentBudget(args.output / "ledger.json", args.max_usd, max_calls=160)
    results = []
    for provider, mode, concurrency in cells:
        key = f"{provider}-{mode}-c{concurrency}"
        target = args.output / (key + ".json")
        if target.exists():
            results.append(json.loads(target.read_text(encoding="utf-8")))
            continue
        started = time.perf_counter()
        shared = create_client(provider, settings, concurrency) if mode == "persistent" else None
        setup_seconds = time.perf_counter() - started
        try:

            def call(item):
                pair, stage = item
                return measured_call(provider, mode, concurrency, pair, stage, settings, budget, shared)

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                warmup = list(executor.map(call, [(p, "warmup") for p in pairs[:concurrency]]))
                started = time.perf_counter()
                measured = list(executor.map(call, [(p, "measured") for p in pairs]))
                elapsed = time.perf_counter() - started
            rows = warmup + measured
        finally:
            if shared is not None:
                shared.close()
        result = {
            "cell": key,
            "provider": provider,
            "client_mode": mode,
            "concurrency": concurrency,
            "shared_client_setup_seconds": setup_seconds,
            "summary": summarize(rows, elapsed),
            "calls": rows,
        }
        write(target, result)
        results.append(result)
        print(json.dumps({"cell": key, **result["summary"]}), flush=True)
    report = {
        "protocol_sha256": digest,
        "registered_at": registered,
        "completed_at": utc_now(),
        "development_pairs": len(pairs),
        "pair_ids": [p["pair_id"] for p in pairs],
        "cells": results,
        "budget": budget.report(),
        "limits": [
            "Small single-host diagnostic; latency depends on network, proxy, provider load and routing.",
            "Provider-side prompt/cache state cannot be reset. Repeated pairs can benefit either provider.",
            "Concurrent formal studies may contend for provider/network resources; this is deployment observation, not isolated model speed.",
            "No gold labels and no relevance-quality scoring. Different provider models and server behavior remain different.",
            "p95 uses linear interpolation over a small successful-call sample; failed calls and retained costs are reported separately.",
        ],
    }
    write(args.output / "report.json", report)
    aggregate = {k: v for k, v in report.items() if k not in ("budget", "cells")}
    aggregate["cells"] = [{k: v for k, v in cell.items() if k != "calls"} for cell in results]
    aggregate["accounting"] = {
        "attempts": len(budget.events),
        "estimated_or_reserved_usd": budget.estimated_usd,
        "unknown_attempts": sum(e["status"] != "completed" for e in budget.events),
        "cap_usd": args.max_usd,
    }
    aggregate["protocol"] = identity
    write(Path("docs/public-evaluation/jev-latency-control.json"), aggregate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/jev-latency-v1"))
    parser.add_argument("--data", type=Path, default=Path("data/external-benchmarks"))
    parser.add_argument("--pairs", type=int, default=8)
    parser.add_argument("--max-usd", type=float, default=1)
    run(parser.parse_args())
