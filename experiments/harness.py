"""Restricted Harness² PPC + experience pilot, with no gold in the runtime.

Only a planner instruction addendum and a stricter context cap may change. Raw
data, graph, aggregation, validation, schema, budgets and oracle remain frozen.
This planning-only pilot is a restricted port, not the original eight-component
professional-task benchmark. Default HTTP service never imports this module.
"""

import argparse
import json
import time
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, Field

from app.agent import AgentRunner
from app.config import PROJECT_ROOT, Settings
from app.context import PLANNER_PROMPT
from app.extensions import HarnessConfig
from app.models import AnalysisRequest, AnalysisResponse
from app.semantic_eval import PlanningOnlyService, grade
from app.sources import content_hash, utc_now
from experiments.budget import Budget, BudgetedGenerator
from experiments.retrieval import Embeddings, HybridIndex


class HarnessEdit(BaseModel):
    addendum: str = Field(max_length=1800)
    context_char_limit: int = Field(default=40000, ge=12000, le=40000)
    rationale: str = Field(max_length=500)


class Composition(BaseModel):
    adopt_edit: bool
    edit: HarnessEdit
    lesson: str = Field(max_length=500)
    evidence: str = Field(max_length=600)


class Selection(BaseModel):
    index: int = Field(ge=0, le=5)
    observation: str = Field(max_length=500)


class AdditiveHarness:
    def __init__(self, edit):
        self.edit = edit

    def load(self, version):
        digest = content_hash(self.edit.model_dump())[:12]
        return HarnessConfig(
            version="research-" + digest,
            prompt_version="planner-v2+" + digest,
            system_instructions=PLANNER_PROMPT
            + "\nOptional task guidance (fixed capabilities and hard validation still apply):\n"
            + self.edit.addendum,
            context_char_limit=self.edit.context_char_limit,
        )


def public_task(case):
    return {
        "id": case["id"],
        "requests": [t["request"] for t in case["turns"]],
        **{key: case[key] for key in ("seed_plan", "seed_pending") if key in case},
    }


def compact(run):
    return [
        {
            "status": t["response"]["status"],
            "questions": t["response"]["questions"],
            "message": t["response"]["message"],
            "plan": t["response"]["meta"].get("plan"),
            "fixed_validation_errors": [
                error
                for event in t["trace"]
                if event["stage"] == "planner_request"
                for error in json.loads(event["context"]["payload"])["validation_errors"]
            ],
        }
        for t in run["turns"]
    ]


def run_task(task, settings, directory, budget, edit=None):
    directory.mkdir(parents=True, exist_ok=False)
    settings = settings.model_copy(
        update={"app_data_dir": directory / "state", "openai_max_output_tokens": 4096}
    )
    service = PlanningOnlyService(settings)
    sid, version, state = service.store.session(None)
    if task.get("seed_plan"):
        state["last_successful_plan"] = task["seed_plan"]
    if task.get("seed_pending"):
        state["pending_clarification"] = task["seed_pending"]
    if state:
        state["last_run_id"] = "evaluation_seed"
        service.store.commit_session(sid, version, state)
    turns = []
    for request in task["requests"]:
        runner = AgentRunner(
            settings,
            service,
            BudgetedGenerator(settings, budget),
            harness_provider=AdditiveHarness(edit) if edit else None,
        )
        started = time.monotonic()
        try:
            response = runner.analyze(AnalysisRequest.model_validate({**request, "session_id": sid}))
            turns.append(
                {
                    "response": response.model_dump(mode="json"),
                    "trace": service.store.run(response.run_id)["trace"],
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
        finally:
            runner.close()
    result = {
        "task_id": task["id"],
        "runtime_harness_experiment": edit is not None,
        "baseline_api_flags_unchanged": True,
        "edit": edit.model_dump() if edit else None,
        "turns": turns,
    }
    (directory / "run.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def generate(settings, budget, instructions, payload, schema):
    text = json.dumps(payload, ensure_ascii=False)
    event = budget.before(len((instructions + text).encode("utf-8")))
    started = time.monotonic()
    with OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=str(settings.openai_base_url),
        max_retries=0,
    ) as client:
        response = client.responses.parse(
            model=settings.openai_model,
            reasoning={"effort": settings.openai_reasoning_effort},
            max_output_tokens=4096,
            timeout=90,
            store=False,
            input=[{"role": "system", "content": instructions}, {"role": "user", "content": text}],
            text_format=schema,
        )
        metadata = {
            "provider": "openai",
            "reported_model": response.model,
            "usage": response.usage.model_dump() if response.usage else None,
            "response_id": response.id,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        budget.after(event, metadata)
        if response.output_parsed is None:
            raise RuntimeError("Research editor did not return structured output")
        return response.output_parsed, metadata


EDITOR = """You edit a frozen clinical-study planner harness for the CURRENT task. No evaluation answers or scores are available. Propose only a short instruction addendum and optional smaller context cap. You cannot change fixed base instructions, validators, source search, data, aggregation, evidence, schema, budgets or model. Do not invent records or identities. Keep guidance general enough to transfer. No code or tools may be edited. Ground the proposal in the task, reference execution and observed issues; it is acceptable to propose no addendum. The immutable base instructions always remain in force."""
COMPOSER = """Compare the same task's H0 reference and the two actual candidate probes, with their harness edits. No gold answer or evaluation score is available. Prefer supported faithful behavior and retained user constraints, not more refusals or shorter output by itself. Compose the smallest helpful instruction addendum, or adopt_edit=false if no evidence supports change. A lesson must state what was OBSERVED and must not claim semantic correctness or higher accuracy from an unavailable oracle. Preserve immutable instructions, fixed checks, model and budgets. Do not encode task answers, drug names, benchmark IDs or exact questions into the reusable lesson. Return a brief observable contrast as evidence."""


def ppc(task, reference, settings, directory, budget, retrieved):
    directory.mkdir(parents=True, exist_ok=False)
    payload = {
        "task": task,
        "base_instructions": PLANNER_PROMPT,
        "reference": compact(reference),
        "past_experience_candidates": retrieved,
    }
    edits, probes, metadata = [], [], []
    for role in ("general process/constraint editor", "clinical field/relationship semantics editor"):
        edit, info = generate(settings, budget, EDITOR + "\nYour focus: " + role, payload, HarnessEdit)
        edits.append(edit)
        metadata.append(info)
        probes.append(run_task(task, settings, directory / ("probe_" + str(len(probes))), budget, edit))
    composed, info = generate(
        settings,
        budget,
        COMPOSER,
        {
            "task": task,
            "reference": compact(reference),
            "edits": [e.model_dump() for e in edits],
            "probes": [compact(p) for p in probes],
        },
        Composition,
    )
    metadata.append(info)
    final = run_task(
        task, settings, directory / "composed", budget, composed.edit if composed.adopt_edit else None
    )
    result = {
        "edits": [e.model_dump() for e in edits],
        "probes": probes,
        "composition": composed.model_dump(),
        "final": final,
        "editor_metadata": metadata,
        "retrieved_experience_ids": [r["id"] for r in retrieved],
    }
    (directory / "ppc.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def select_control(task, reference, settings, directory, budget):
    directory.mkdir(parents=True, exist_ok=False)
    # Six executions including the shared frozen reference, plus one selector:
    # seven logical calls for a single-turn task, matching PPC's 4+2+1.
    samples = [reference]
    for index in range(5):
        samples.append(run_task(task, settings, directory / ("sample_" + str(index)), budget))
    selection, metadata = generate(
        settings,
        budget,
        "Select the most faithful supported planner result for this task using the supplied base capabilities and observed outputs. No gold or scores are available. Do not favor refusals or verbosity on their own. Return an index only with a short observable justification.",
        {"task": task, "base_instructions": PLANNER_PROMPT, "samples": [compact(s) for s in samples]},
        Selection,
    )
    return {
        "samples": samples,
        "selection": selection.model_dump(),
        "selector_metadata": metadata,
        "final": samples[selection.index],
    }


def score_run(run, case):
    return [
        grade(AnalysisResponse.model_validate(turn["response"]), expected["expected"])
        for turn, expected in zip(run["turns"], case["turns"], strict=True)
    ]


def behavioral_signature(run):
    signatures = []
    for turn in run["turns"]:
        response = turn["response"]
        plan = response["meta"].get("plan")
        if plan:
            fields = {
                key: value for key, value in plan.items() if key not in ("title", "assumptions", "cohorts")
            }
            populations = []
            for cohort in plan["cohorts"]:
                filters = {
                    key: sorted(v.casefold() for v in value)
                    if isinstance(value, list)
                    else value.casefold()
                    if isinstance(value, str)
                    else value
                    for key, value in cohort["filters"].items()
                }
                if len(filters["interventions"]) < 2:
                    filters["intervention_match"] = "any"
                populations.append(filters)
            fields["populations"] = sorted(populations, key=content_hash)
        else:
            fields = None
        signatures.append({"status": response["status"], "plan": fields})
    return content_hash(signatures)


def experience_entry(task, reference, result, position):
    original = behavioral_signature(reference)
    final = behavioral_signature(result["final"])
    probes = [behavioral_signature(probe) for probe in result["probes"]]
    composition = result["composition"]
    changed = final != original
    return {
        "id": "experience_" + str(position),
        "text": composition["lesson"]
        if changed
        else "No observable action or population-plan change was found between this task's H0 reference and composed execution. No accuracy improvement is demonstrated. Retain H0 when no diagnosed problem justifies an edit.",
        "component": composition["edit"] if changed and composition["adopt_edit"] else None,
        "evidence_task_id": task["id"],
        "behavioral_evidence": {
            "reference_hash": original,
            "probe_hashes": probes,
            "composed_hash": final,
            "changed": changed,
        },
        "status": "unlabelled_observation_not_an_accuracy_claim",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=["development", "heldout"], default="heldout")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-estimated-usd", type=float, default=20)
    args = parser.parse_args()
    document = json.loads((PROJECT_ROOT / "docs/evals/semantic_cases.json").read_text(encoding="utf-8"))
    cases = [c for c in document["cases"] if c["split"] == args.split][: args.limit]
    if not args.live:
        print(
            json.dumps(
                {
                    "tasks": len(cases),
                    "variants": ["H0", "H0_multisample", "PPC_no_memory", "PPC_memory"],
                    "model_calls": 0,
                }
            )
        )
        return
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    references = {
        r["id"]: {
            "turns": [
                {"response": t["response"], "trace": t["trace"], "elapsed_seconds": t["elapsed_seconds"]}
                for t in r["turns"]
            ]
        }
        for r in baseline["results"]
    }
    tasks = [public_task(case) for case in cases]  # From here until scoring, no expected answers.
    args.output.mkdir(parents=True, exist_ok=False)
    settings, budget = Settings(), Budget(args.max_estimated_usd)
    embedder = Embeddings(settings, args.output / "experience_vectors.json")
    streams = {}
    experience = []
    try:
        for variant in ("H0_multisample", "PPC_no_memory", "PPC_memory"):
            stream = []
            for position, task in enumerate(tasks):
                target = args.output / variant / task["id"]
                if variant == "H0_multisample":
                    result = select_control(task, references[task["id"]], settings, target, budget)
                else:
                    retrieved = []
                    if variant == "PPC_memory" and experience:
                        query = " ".join(r["query"] for r in task["requests"])
                        retrieved = [
                            item["document"]
                            for item in HybridIndex(experience, embedder).search(query, 3)["hybrid"]
                        ]
                    result = ppc(task, references[task["id"]], settings, target, budget, retrieved)
                    # Store observable contrasts, including negative evidence. Identical
                    # outputs never become an invented "improvement" or adopted component.
                    if variant == "PPC_memory" and all(
                        t["response"]["status"] not in ("failed", "incomplete")
                        for t in result["final"]["turns"]
                    ):
                        experience.append(experience_entry(task, references[task["id"]], result, position))
                        (args.output / "experience.json").write_text(
                            json.dumps(experience, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                stream.append({"task_id": task["id"], **result})
                (args.output / "ledger.json").write_text(
                    json.dumps(budget.report(), indent=2), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {
                            "variant": variant,
                            "completed": len(stream),
                            "tasks": len(tasks),
                            "experience_entries": len(experience),
                            "estimated_usd": round(budget.estimated_usd, 4),
                        }
                    ),
                    flush=True,
                )
            streams[variant] = stream
        # Gold is consulted only after EVERY runtime stream and experience update finishes.
        scores = {"H0": [g for c in cases for g in score_run(references[c["id"]], c)]}
        for variant, stream in streams.items():
            scores[variant] = [
                g
                for result, case in zip(stream, cases, strict=True)
                for g in score_run(result["final"], case)
            ]
        summary = {
            variant: {
                "turns": len(grades),
                "correct": sum(g["correct"] for g in grades),
                "incorrect_executions": sum(g["incorrect_execution"] for g in grades),
                "overclarifications": sum(g["overclarification"] for g in grades),
            }
            for variant, grades in scores.items()
        }
        report = {
            "at": utc_now(),
            "experiment": "restricted_planning_Harness2_k1",
            "split": args.split,
            "task_order": [t["id"] for t in tasks],
            "baseline_hash": content_hash(baseline),
            "summary": summary,
            "scores_after_runtime": scores,
            "ledger": budget.report(),
            "embedding": {
                "model": embedder.model,
                "dimensions": embedder.dimensions,
                "calls": embedder.calls,
                "input_tokens": embedder.input_tokens,
            },
            "experience": experience,
            "streams": streams,
            "limitations": [
                "Restricted planning-only port with two editable components; not a full eight-component Harness2 reproduction.",
                "One fixed task order; no task-order variance estimate.",
                "H0 reference calls reused from the frozen baseline; charged logically to every arm but not billed again.",
                "Control uses six H0 samples including shared reference plus one selector; PPC uses four executions including shared reference, two editors and one composer. Multi-turn tasks differ in actual execution calls, all recorded.",
                "New planner and editor calls cap output at 4096; fixed equally across experimental arms. Frozen H0 references used the baseline 32768 cap; all actual outputs were below 4096.",
                "API feature flags remain false; candidate HarnessProvider is explicitly injected only in this research command and separately recorded.",
                "No gold or scores provided to editors, composer, selector or experience store; scoring happens after all streams.",
                "No statistical outputs generated here; data/graph/oracle remain frozen and separately validated.",
            ],
        }
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "summary": summary,
                    "estimated_usd": budget.estimated_usd,
                    "experience_entries": len(experience),
                },
                indent=2,
            )
        )
    finally:
        embedder.close()


if __name__ == "__main__":
    main()
