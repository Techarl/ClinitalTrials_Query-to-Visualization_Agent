"""Frozen-transfer Harness study with public-only selection and USD-matched retries."""

import argparse
import hashlib
import json
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from app.config import Settings
from app.sources import content_hash
from app.v2.skills import BUILTINS
from evaluation.contracts import Episode
from evaluation.protocol import execution_identity, freeze
from evaluation.runtime import PersistentBudget, run_episode, write
from experiments.harness_v2 import ExperienceBank, ppc, quality


def selection(cases):
    """Select by public split/entity/family only, before observing any result."""
    development = [e for e in cases if e.split == "development"]
    families = list(dict.fromkeys(e.family for e in development))
    groups = list(dict.fromkeys(e.entity_group for e in development))
    dev = [
        next(e for e in development if e.family == f and e.entity_group == groups[i % len(groups)])
        for i, f in enumerate(families)
    ][:20]
    held = [e for e in cases if e.split == "heldout"]
    held_groups = list(dict.fromkeys(e.entity_group for e in held))
    held_families = list(dict.fromkeys(e.family for e in held))
    formal = [
        next(
            e
            for e in held
            if e.entity_group == group and e.family == held_families[(i * 4 + j) % len(held_families)]
        )
        for i, group in enumerate(held_groups)
        for j in range(4)
    ]
    assert len(dev) == 20 and len({e.id for e in formal}) == 40
    return dev, formal


def event_cost(event):
    return event.get("estimated_cost_usd", event["reserved_upper_estimate_usd"])


def metrics(events):
    return {
        "calls": len(events),
        "usd": sum(event_cost(e) for e in events),
        "input_tokens": sum(
            (e.get("metadata", {}).get("usage") or {}).get("input_tokens", 0) for e in events
        ),
        "output_tokens": sum(
            (e.get("metadata", {}).get("usage") or {}).get("output_tokens", 0) for e in events
        ),
        "unknown_usage_calls": sum(e["status"] != "completed" for e in events),
        "measured_api_seconds": sum(e.get("duration_seconds", 0) for e in events),
        "calls_without_latency": sum("duration_seconds" not in e for e in events),
    }


class StudyBudget(PersistentBudget):
    """One persistent ledger with tagged arms and independently bounded USD slices."""

    def __init__(self, path, max_usd):
        super().__init__(path, max_usd=max_usd, max_calls=20000)
        self.scope_name, self.scope_limit = "unassigned", None

    def scope_events(self, name=None):
        return [e for e in self.events if e.get("study_scope") == (name or self.scope_name)]

    @contextmanager
    def scope(self, name, limit=None):
        previous = self.scope_name, self.scope_limit
        self.scope_name, self.scope_limit = name, limit
        try:
            yield
        finally:
            self.scope_name, self.scope_limit = previous

    def before(self, context_bytes, output_tokens=4096):
        upper = (context_bytes * 2.5 + output_tokens * 10) / 1_000_000
        with self.lock:
            if (
                self.scope_limit is not None
                and metrics(self.scope_events())["usd"] + upper > self.scope_limit
            ):
                raise RuntimeError("USD-matched retry allocation exhausted before request")
            event = super().before(context_bytes, output_tokens)
            event["study_scope"] = self.scope_name
            event["started_at_epoch"] = time.time()
            write(self.path, self.report())
            return event

    def after(self, event, metadata):
        with self.lock:
            super().after(event, metadata)
            event["finished_at_epoch"] = time.time()
            event["duration_seconds"] = event["finished_at_epoch"] - event["started_at_epoch"]
            write(self.path, self.report())


def copy_bank(entries):
    bank = ExperienceBank()
    bank.entries = json.loads(json.dumps(entries))
    return bank


def mechanism_counts(path, entries):
    learned, builtin = set(), set()
    for item in entries.values():
        data = {**item, "procedure": item.get("procedure", item.get("component", {}).get("skill", ""))}
        learned.add("skill_" + content_hash(data)[:24])
    for item in BUILTINS:
        builtin.add("skill_" + content_hash(item)[:24])
    counts = Counter()
    ids = {"read": [], "applied": []}
    for turn in sorted(Path(path).glob("turn-*.json")):
        if turn.name.endswith("-run.json"):
            continue
        data = json.loads(turn.read_text(encoding="utf-8"))
        for observation in data["state"]["observations"]:
            tool, identity = observation.get("tool"), observation.get("skill_id")
            if observation.get("status") != "ok":
                continue
            if tool == "search_experiences":
                counts["search_calls"] += 1
            if tool in ("read_skill", "apply_skill"):
                action = "read" if tool == "read_skill" else "applied"
                source = (
                    "learned"
                    if identity in learned
                    else "engineering_builtin"
                    if identity in builtin
                    else "other"
                )
                counts[f"{source}_{action}"] += 1
                ids[action].append(identity)
    return {
        "counts": dict(counts),
        "ids": ids,
        "limit": "Read/activation events establish mechanism use, not causal improvement.",
    }


def public_episode_probe(path):
    """Selection checks all already-executed turns, without reading any grade."""
    probes = []
    for saved in sorted(Path(path).glob("turn-*.json")):
        if saved.name.endswith("-run.json"):
            continue
        row = json.loads(saved.read_text(encoding="utf-8"))
        response = row["response"]
        usage = response["usage"]
        probes.append(
            {
                "status": response["status"],
                "unmet_goals": response["unmet_goals"],
                "observations": row["state"]["observations"],
                "model_calls": usage["model_calls"],
                "estimated_or_reserved_usd": usage["estimated_or_reserved_usd"],
                "input_tokens": sum(
                    (r.get("usage") or {}).get("input_tokens", 0) for r in usage.get("receipts", [])
                ),
                "output_tokens": sum(
                    (r.get("usage") or {}).get("output_tokens", 0) for r in usage.get("receipts", [])
                ),
            }
        )
    return {
        "status": "completed" if probes and all(p["status"] == "completed" for p in probes) else "partial",
        "unmet_goals": [g for p in probes for g in p["unmet_goals"]],
        "observations": [o for p in probes for o in p["observations"]],
        **{
            key: sum(p[key] for p in probes)
            for key in ("model_calls", "estimated_or_reserved_usd", "input_tokens", "output_tokens")
        },
    }


def retry_selection(episode, settings, root, budget, frozen, baseline, allocation):
    """Include H0 as first candidate, spend at most the comparator's extra USD."""
    freeze(
        root / "retry-protocol.json",
        {
            "allocation_usd": allocation,
            "baseline": str(baseline),
            "selection": "max public quality; no hidden labels",
        },
    )
    paths = [baseline]
    attempts = []
    scope = str(root)
    with budget.scope(scope, allocation):
        for index in range(20):
            path = root / f"attempt-{index}"
            was_cached = (path / "summary.json").exists()
            previous_calls = len(budget.events)
            try:
                summary = run_episode(episode, settings, path, budget, frozen)
            except RuntimeError as error:
                attempts.append({"error": str(error), "trace": str(path)})
                break
            if (path / "turn-1.json").exists():
                paths.append(path)
            attempts.append({"summary": summary, "trace": str(path)})
            # A denied reservation is not a model call. Do not emit endless empty attempts.
            if len(budget.events) == previous_calls and not was_cached:
                break
            if summary.get("model_calls", 0) == 0:
                break
            if metrics(budget.scope_events())["usd"] >= allocation:
                break
    selected = max(range(len(paths)), key=lambda i: quality(public_episode_probe(paths[i])))
    report = {
        "extra_allocation_usd": allocation,
        "actual": metrics(budget.scope_events(scope)),
        "attempts": attempts,
        "selected_trace": str(paths[selected]),
        "selected_summary": json.loads((paths[selected] / "summary.json").read_text(encoding="utf-8")),
        "selection_rule": "Public completion, errors, calls, tokens, cost; grades report-only",
        "limits": "Conservative pre-request reservations can leave an unusable residual; at most 20 retries.",
    }
    write(root / "result.json", report)
    return report


def task(episode, settings, root, budget, frozen, incoming, *, all_arms=True):
    root.mkdir(parents=True, exist_ok=True)
    freeze(root / "incoming-bank.json", {"entries": incoming, "episode_id": episode.id})
    with budget.scope(str(root / "h0")):
        baseline = run_episode(episode, settings, root / "h0", budget, frozen)
    bank = copy_bank(incoming)
    if all_arms:
        with budget.scope(str(root / "no-memory")):
            nomem = ppc(episode, settings, root / "no-memory", budget, root / "h0", frozen=frozen)
    with budget.scope(str(root / "memory")):
        memory = ppc(episode, settings, root / "memory", budget, root / "h0", bank, frozen=frozen)
    report = {
        "episode_id": episode.id,
        "entity_group": episode.entity_group,
        "family": episode.family,
        "h0": baseline,
        "ppc_memory": memory,
        "mechanism": mechanism_counts(root / "memory" / "final", incoming),
        "incoming_bank_sha256": content_hash(incoming),
    }
    if all_arms:
        allocated = metrics(budget.scope_events(str(root / "no-memory")))["usd"]
        retry = retry_selection(
            episode, settings, root / "usd-retries", budget, frozen, root / "h0", allocated
        )
        report.update(ppc_no_memory=nomem, usd_retries=retry)
    report["costs"] = {
        name: metrics(budget.scope_events(str(root / name)))
        for name in ("h0", "no-memory", "memory", "usd-retries")
    }
    report["cost_interpretation"] = (
        "Each PPC and retry arm includes the same H0 expense in its total; shared H0 is paid once and disclosed separately."
    )
    write(root / "result.json", report)
    return report, bank.entries


def source_hashes(frozen):
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for root in (frozen, Path("evaluation/frozen"))
        for p in sorted(root.rglob("*.json"))
    }


def progress(rows, planned):
    by_id = {row["episode_id"]: row for row in rows}
    return {
        "planned_count": len(planned),
        "evaluated_count": len(by_id),
        "planned_ids": [e.id for e in planned],
        "unrun_ids": [e.id for e in planned if e.id not in by_id],
        "h0_correct": sum(row["h0"]["correct"] for row in by_id.values()),
        "memory_correct": sum(row["ppc_memory"]["final"]["correct"] for row in by_id.values()),
        "limit": "Failed or incorrect evaluations remain in evaluated_count; unrun tasks are explicit, not silently removed.",
    }


def protocol(settings, frozen, dev, formal, orders, max_usd):
    source_files = source_hashes(frozen)
    return execution_identity(
        settings,
        version="harness-v3-frozen-transfer",
        source_files=source_files,
        development_ids=[e.id for e in dev],
        formal_ids=[e.id for e in formal],
        orders=[[e.id for e in formal], [e.id for e in reversed(formal)]][:orders],
        development_groups=dict(Counter(e.entity_group for e in dev)),
        formal_groups=dict(Counter(e.entity_group for e in formal)),
        formal_families=dict(Counter(e.family for e in formal)),
        formal_bank="read-only development checkpoint copied per task",
        adoption="public observations only; hidden grades report-only",
        retries="same total USD allowance as PPC-no-memory including H0",
        production_seeds="Engineering BUILTINS are present in strong H0; not learned improvements",
        hard_total_usd=max_usd,
        pilot_hard_usd=1,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=["plan", "pilot", "pilot-mechanism", "development", "formal", "mechanism"],
        default="pilot",
    )
    parser.add_argument("--output", type=Path, default=Path("data/harness-v3"))
    parser.add_argument("--frozen", type=Path, default=Path("data/core300-frozen"))
    parser.add_argument("--orders", type=int, choices=[1, 2], default=2)
    parser.add_argument("--max-usd", type=float, default=40)
    parser.add_argument("--release-formal", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_usd <= 55:
        parser.error("Study ceiling must be between $1 and the allocated $55 maximum")
    if args.phase == "formal" and not args.release_formal:
        parser.error(
            "Formal phase is held pending aggregate budget review; pass --release-formal only after authorization"
        )
    settings = Settings()
    cases = [
        Episode.model_validate(e)
        for e in json.loads((args.frozen / "cases.json").read_text(encoding="utf-8"))["episodes"]
    ]
    dev, formal = selection(cases)
    if args.phase != "pilot-mechanism":
        freeze(
            args.output / "protocol.json",
            protocol(settings, args.frozen, dev, formal, args.orders, args.max_usd),
        )
    if args.phase == "plan":
        print(
            json.dumps(
                {
                    "development": 20,
                    "formal_unique": 40,
                    "orders": args.orders,
                    "ceiling_usd": args.max_usd,
                    "formal_released": False,
                }
            ),
            flush=True,
        )
        return
    pilot = args.phase in ("pilot", "pilot-mechanism")
    budget = StudyBudget(
        args.output / ("pilot-ledger.json" if pilot else "ledger.json"), 1 if pilot else args.max_usd
    )
    if args.phase == "pilot-mechanism":
        prior = json.loads((args.output / "pilot-report.json").read_text(encoding="utf-8"))
        bank = prior["public_bank"]
        freeze(
            args.output / "pilot-mechanism-protocol.json",
            execution_identity(
                settings,
                episode_id=dev[0].id,
                bank=bank,
                source_files=source_hashes(args.frozen),
                policies=["proactive", "disabled"],
                combined_pilot_cap_usd=1,
                limitation="Same development task; mechanism demonstration, not transfer efficacy",
            ),
        )
        rows = {}
        for policy in ("proactive", "disabled"):
            path = args.output / "pilot-mechanism" / policy
            with budget.scope(str(path)):
                summary = run_episode(
                    dev[0],
                    settings,
                    path,
                    budget,
                    args.frozen,
                    experiences=list(bank.values()),
                    experience_policy=policy,
                )
            rows[policy] = {
                "summary": summary,
                "mechanism": mechanism_counts(path, bank),
                "cost": metrics(budget.scope_events(str(path))),
            }
            write(
                args.output / "pilot-mechanism-report.json",
                {"arms": rows, "combined_pilot_budget": metrics(budget.events)},
            )
        print(
            json.dumps(
                {
                    "combined_pilot_budget": metrics(budget.events),
                    "mechanisms": {k: v["mechanism"] for k, v in rows.items()},
                }
            ),
            flush=True,
        )
    elif args.phase == "pilot":
        row, bank = task(dev[0], settings, args.output / "pilot" / dev[0].id, budget, args.frozen, {})
        costs = row["costs"]
        per_task = sum(costs[name]["usd"] for name in costs)
        forecast = {
            "pilot": row,
            "budget": metrics(budget.events),
            "forecast_20dev_40formal_2orders_usd": 20 * (costs["h0"]["usd"] + costs["memory"]["usd"])
            + 80 * per_task,
            "forecast_20dev_40formal_1order_usd": 20 * (costs["h0"]["usd"] + costs["memory"]["usd"])
            + 40 * per_task,
            "forecast_limit": "One single-step development task; multi-turn tasks and editor variance can cost more. Formal study not started.",
            "public_bank": bank,
        }
        write(args.output / "pilot-report.json", forecast)
        print(
            json.dumps({k: v for k, v in forecast.items() if k not in ("pilot", "public_bank")}), flush=True
        )
    elif args.phase == "development":
        bank = {}
        results = []
        write(
            args.output / "development-report.json",
            {"results": results, "progress": progress(results, dev), "budget": metrics(budget.events)},
        )
        for episode in dev:
            row, bank = task(
                episode,
                settings,
                args.output / "development" / episode.id,
                budget,
                args.frozen,
                bank,
                all_arms=False,
            )
            results.append(row)
            write(args.output / "development-bank.json", bank)
            write(
                args.output / "development-report.json",
                {"results": results, "progress": progress(results, dev), "budget": metrics(budget.events)},
            )
            print(
                json.dumps(
                    {
                        "development_done": len(results),
                        "planned": len(dev),
                        "bank_entries": len(bank),
                        "usd": budget.estimated_usd,
                    }
                ),
                flush=True,
            )
        freeze(args.output / "frozen-bank.json", {"entries": bank})
    elif args.phase == "formal":
        bank = json.loads((args.output / "frozen-bank.json").read_text(encoding="utf-8"))["identity"][
            "entries"
        ]
        results = []
        write(
            args.output / "formal-report.json",
            {
                "results": [],
                "planned_unique": len(formal),
                "planned_runs": len(formal) * args.orders,
                "unrun_ids_by_order": {str(i): [e.id for e in formal] for i in range(args.orders)},
                "budget": metrics(budget.events),
            },
        )
        for order in range(args.orders):
            for episode in formal if order == 0 else list(reversed(formal)):
                row, _ = task(
                    episode, settings, args.output / f"order-{order}" / episode.id, budget, args.frozen, bank
                )
                row["order"] = order
                results.append(row)
                write(
                    args.output / "formal-report.json",
                    {
                        "results": results,
                        "budget": metrics(budget.events),
                        "unique_tasks": len({r["episode_id"] for r in results}),
                        "planned_unique": len(formal),
                        "planned_runs": len(formal) * args.orders,
                        "progress_by_order": {
                            str(i): progress([r for r in results if r["order"] == i], formal)
                            for i in range(args.orders)
                        },
                    },
                )
    else:
        bank_path = args.output / "development-bank.json"
        bank = json.loads(bank_path.read_text(encoding="utf-8")) if bank_path.exists() else {}
        results = []
        for episode in dev[8:10]:
            root = args.output / "online-mechanism" / episode.id
            row, next_bank = task(episode, settings, root, budget, args.frozen, bank, all_arms=False)
            ablation = {}
            for policy in ("proactive", "disabled"):
                path = root / policy
                with budget.scope(str(path)):
                    summary = run_episode(
                        episode,
                        settings,
                        path,
                        budget,
                        args.frozen,
                        experiences=list(bank.values()),
                        experience_policy=policy,
                    )
                ablation[policy] = {"summary": summary, "mechanism": mechanism_counts(path, bank)}
            results.append({"row": row, "ablation": ablation})
            bank = next_bank
            write(
                args.output / "online-mechanism-report.json",
                {
                    "results": results,
                    "bank": bank,
                    "limit": "Separate diagnostic online stream, not frozen-transfer test results.",
                },
            )


if __name__ == "__main__":
    main()
