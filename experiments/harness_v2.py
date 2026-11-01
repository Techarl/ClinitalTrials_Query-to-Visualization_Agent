"""Execution-based PPC study: real tools, immutable data, bounded component edits."""

import argparse
import json
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from app.config import Settings
from app.sources import content_hash
from evaluation.contracts import Episode
from evaluation.runtime import PersistentBudget, run_episode, write

MANUAL_SKILL = (
    "The analyze tool already verifies values and complete contributor evidence; finalization verifies again. "
    "Do not call validate_result solely to repeat those checks. Use profile_dataset only when missingness or available fields affects the next action. "
    "Use result-dependent scope handles for rankings, preserving the original root population."
)


class Component(BaseModel):
    skill: str = Field(default="", max_length=1600)
    context_policy: Literal["relevant", "recent"] = "relevant"
    rationale: str = Field(max_length=700)


class Composition(BaseModel):
    component: Component
    observation: str = Field(max_length=700)


def safe_component(component):
    forbidden = [
        "pembrolizumab",
        "nivolumab",
        "omalizumab",
        "secukinumab",
        "adalimumab",
        "melanoma",
        "psoriasis",
        "asthma",
        "rheumatoid",
        "NCT0",
        "ep_",
        "Merck",
        "Yale",
    ]
    if any(word.casefold() in component.skill.casefold() for word in forbidden):
        raise ValueError("Reusable components cannot memorize entities, task IDs or answers")
    return component


def generate(settings, budget, schema, system, value):
    encoded = json.dumps(value, ensure_ascii=False)
    event = budget.before(len((system + encoded + json.dumps(schema.model_json_schema())).encode()), 2048)
    with OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=str(settings.openai_base_url),
        max_retries=0,
    ) as client:
        response = client.responses.parse(
            model=settings.openai_model,
            reasoning={"effort": settings.openai_reasoning_effort},
            store=False,
            max_output_tokens=2048,
            timeout=90,
            text_format=schema,
            input=[{"role": "system", "content": system}, {"role": "user", "content": encoded}],
        )
        budget.after(
            event,
            {
                "provider": "openai",
                "reported_model": response.model,
                "response_id": response.id,
                "usage": response.usage.model_dump() if response.usage else None,
            },
        )
        if response.output_parsed is None:
            raise ValueError("Editor returned no valid component")
        return response.output_parsed


def public_probe(directory):
    # Only the first/current turn is visible to editors. Never reveal future dialogue answers or hidden labels.
    turn = json.loads((Path(directory) / "turn-1.json").read_text(encoding="utf-8"))
    response, state = turn["response"], turn["state"]
    return {
        "status": response["status"],
        "task": response["task"],
        "questions": response["questions"],
        "unmet_goals": response["unmet_goals"],
        "observations": [
            {k: v for k, v in o.items() if k not in ("raw", "citations")} for o in state["observations"]
        ],
        "artifacts": [
            {
                "goal_id": a["goal_id"],
                "metric": a["metric"],
                "coverage": a["coverage"],
                "warnings": a["warnings"],
            }
            for a in response["artifacts"]
        ],
        "model_calls": response["usage"]["model_calls"],
        "estimated_or_reserved_usd": response["usage"]["estimated_or_reserved_usd"],
        "context_packing": [
            e["data"]["context_packing"] for e in turn["events"] if e["type"] == "action_selected"
        ],
        "trace": str(Path(directory) / "turn-1.json"),
    }


def quality(observation):
    return (
        observation["status"] == "completed",
        -len(observation["unmet_goals"]),
        -sum(o.get("status") == "error" for o in observation["observations"]),
        -observation["model_calls"],
    )


class ExperienceBank:
    def __init__(self):
        self.entries = {}

    def retrieve(self, family, observation):
        redundant = sum(o.get("tool") == "validate_result" for o in observation["observations"]) > 0
        return [
            entry
            for entry in self.entries.values()
            if entry["adopted"]
            and (
                family in entry["families"]
                or redundant
                and entry["applicability"] == "redundant_verification"
            )
        ][:3]

    def add(self, family, before, after, component, adopted):
        behavior = {
            "before_calls": before["model_calls"],
            "after_calls": after["model_calls"],
            "before_errors": sum(o.get("status") == "error" for o in before["observations"]),
            "after_errors": sum(o.get("status") == "error" for o in after["observations"]),
        }
        if not component.skill and component.context_policy == "relevant":
            return None  # No-change experiments stay in run logs, not duplicated retrievable lessons.
        applicability = (
            "redundant_verification"
            if any(o.get("tool") == "validate_result" for o in before["observations"])
            else family
        )
        key = content_hash([component.skill, component.context_policy, applicability, adopted])[:20]
        if key in self.entries:
            self.entries[key]["families"] = sorted(set(self.entries[key]["families"] + [family]))
            self.entries[key]["supporting_traces"].append(after["trace"])
        else:
            self.entries[key] = {
                "id": key,
                "applicability": applicability,
                "families": [family],
                "component": component.model_dump(),
                "adopted": adopted,
                "behavior": behavior,
                "supporting_traces": [before["trace"], after["trace"]],
                "limitations": "Observed on frozen task execution; retrieval does not imply reuse will help another task.",
            }
        return key


def ppc(episode, settings, root, budget, baseline, bank=None):
    if (root / "ppc.json").exists():
        return json.loads((root / "ppc.json").read_text(encoding="utf-8"))
    start_calls = len(budget.events)
    before = public_probe(baseline)
    retrieved = bank.retrieve(episode.family, before) if bank else []
    edit_system = (
        "Propose one small reusable analysis procedure or effective context selection policy. "
        "You cannot change model, budget, permissions, statistical kernels, evidence, schemas or verifiers. "
        "No task IDs, drug/disease/sponsor names, answers or future dialogue content in a component. "
        "Seek observable fewer invalid/redundant tool calls or better completion without losing any requested goal. "
        "An empty skill is valid if no evidence supports a change. You see only public observations; no hidden scoring labels."
    )
    base_input = {
        "current_request": episode.turns[0].query,
        "observed_execution": before,
        "retrieved_experience": retrieved,
    }
    probes, proposed = [], []
    editor_errors = []
    first_turn = episode.model_copy(update={"turns": episode.turns[:1]})
    for role in ["general", "domain"]:
        saved = root / (role + "-proposal.json")
        if saved.exists():
            component = Component.model_validate_json(saved.read_text(encoding="utf-8"))
        else:
            try:
                component = safe_component(
                    generate(
                        settings,
                        budget,
                        Component,
                        edit_system + " Your edit focus is " + role + ".",
                        base_input,
                    )
                )
            except Exception as error:
                editor_errors.append({"role": role, "error_type": type(error).__name__})
                component = Component(
                    rationale="Editor failed; unchanged baseline retained: " + type(error).__name__
                )
            write(saved, component.model_dump())
        proposed.append(component)
        path = root / ("probe-" + role)
        run_episode(
            first_turn,
            settings,
            path,
            budget,
            Path("examples"),
            skill=component.skill,
            policy=component.context_policy,
        )
        probes.append(public_probe(path))
    try:
        composed = generate(
            settings,
            budget,
            Composition,
            edit_system + " Combine only supported changes; explain observed behavior differences.",
            {**base_input, "proposals": [c.model_dump() for c in proposed], "probe_observations": probes},
        )
        component = safe_component(composed.component)
    except Exception as error:
        editor_errors.append({"role": "composer", "error_type": type(error).__name__})
        component = Component(rationale="Composer failed; retain baseline")
        composed = Composition(component=component, observation="Composition failed: " + type(error).__name__)
    combined_path = root / "combined-probe"
    run_episode(
        first_turn,
        settings,
        combined_path,
        budget,
        Path("examples"),
        skill=component.skill,
        policy=component.context_policy,
    )
    combined = public_probe(combined_path)
    # Selection uses public fixed checks. Hidden semantic scoring remains outside the editor.
    adopt = quality(combined) > quality(before) and combined["status"] == "completed"
    selected = component if adopt else Component(rationale="No observed improvement over strong baseline")
    final = run_episode(
        episode,
        settings,
        root / "final",
        budget,
        Path("examples"),
        skill=selected.skill,
        policy=selected.context_policy,
    )
    final_observation = public_probe(root / "final")
    # A failed semantic/evidence audit invalidates the claimed adoption, never becomes a positive lesson.
    verified_adoption = adopt and final["correct"]
    lesson = (
        bank.add(episode.family, before, final_observation, component, verified_adoption) if bank else None
    )
    applied = [
        e["id"] for e in retrieved if e["component"]["skill"] and e["component"]["skill"] in component.skill
    ]
    report = {
        "general": proposed[0].model_dump(),
        "domain": proposed[1].model_dump(),
        "composition": composed.model_dump(),
        "retrieved": [e["id"] for e in retrieved],
        "applied": applied,
        "adopted": verified_adoption,
        "adoption_rule": "public completion/error/call improvement, then independent semantic/evidence audit",
        "experience_id": lesson,
        "final": final,
        "probe_behavior": {"baseline": quality(before), "combined": quality(combined)},
        "context_policy_effective": any(p["omitted_steps"] for p in combined["context_packing"]),
        "editor_errors": editor_errors,
        "model_calls_including_editors": len(budget.events) - start_calls,
    }
    write(root / "ppc.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/harness-v2"))
    parser.add_argument("--streams", type=int, default=2)
    parser.add_argument("--tasks", type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    budget = PersistentBudget(args.output / "ledger.json", max_usd=10, max_calls=2000)
    settings = Settings()
    corpus = [
        Episode.model_validate(e)
        for e in json.loads(Path("evaluation/cases_v2.json").read_text(encoding="utf-8"))["episodes"]
    ]
    pool = [
        next(e for e in corpus if e.entity_group == entity and e.family == family)
        for entity, family in [
            ("asthma", "phase"),
            ("rheumatoid arthritis", "completion_month"),
            ("asthma", "axis_followup"),
        ]
    ][: args.tasks]
    results = []
    for stream in range(args.streams):
        bank = ExperienceBank()  # Independent online-learning stream, empty every time.
        order = pool if stream % 2 == 0 else list(reversed(pool))
        for index, episode in enumerate(order):
            root = args.output / f"stream-{stream}" / f"task-{index}"
            root.mkdir(exist_ok=True, parents=True)
            h0 = run_episode(episode, settings, root / "h0", budget, Path("examples"))
            nomem = ppc(episode, settings, root / "ppc-no-memory", budget, root / "h0")
            allocated_calls = nomem["model_calls_including_editors"]
            mem = ppc(episode, settings, root / "ppc-memory", budget, root / "h0", bank)
            manual = run_episode(
                episode, settings, root / "manual", budget, Path("examples"), skill=MANUAL_SKILL
            )
            retries = []
            remaining = allocated_calls
            attempt = 0
            while remaining >= 4 and attempt < 6:
                limited = settings.model_copy(
                    update={
                        "v2_max_model_calls": min(
                            settings.v2_max_model_calls, remaining // len(episode.turns)
                        )
                    }
                )
                retry = run_episode(episode, limited, root / f"retry-{attempt}", budget, Path("examples"))
                retries.append(retry)
                remaining -= retry["model_calls"]
                attempt += 1
            selected_retry = (
                max(range(len(retries)), key=lambda i: quality(public_probe(root / f"retry-{i}")))
                if retries
                else None
            )
            row = {
                "stream": stream,
                "order": index,
                "episode_id": episode.id,
                "h0": h0,
                "ppc_no_memory": nomem,
                "ppc_memory": mem,
                "manual": manual,
                "same_budget_retries": {
                    "allocated_model_calls": allocated_calls,
                    "remaining_model_calls": remaining,
                    "attempts": retries,
                    "selected": selected_retry,
                },
                "memory_size": len(bank.entries),
            }
            results.append(row)
            write(root / "result.json", row)
            write(args.output / f"experience-stream-{stream}.json", list(bank.entries.values()))
            write(
                args.output / "report.json",
                {
                    "version": "harness-v2-real-execution",
                    "protocol": "Independent empty-memory streams; online learning within each stream. Editors see only the current first-turn probe; runtime sees no future user answers. Same frozen CTgov/PubMed, Neo4j and verifiers in every arm.",
                    "results": results,
                    "budget": budget.report(),
                    "limits": [
                        "Small diagnostic execution study, not a broad self-improvement claim.",
                        "Fixed manual procedure is reported as a separate control.",
                        "Component retrieval, application and verified adoption are counted separately.",
                        "Same-budget retry allowance includes PPC editor and composer calls; unused residual calls are reported.",
                    ],
                },
            )
            print(
                json.dumps(
                    {
                        "stream": stream,
                        "task": index,
                        "h0": h0["correct"],
                        "adopted": mem["adopted"],
                        "memory_entries": len(bank.entries),
                        "estimated_usd": budget.estimated_usd,
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
