"""Versioned, on-demand procedures and public execution lessons."""

import re

from pydantic import Field

from app.models import Contract
from app.sources import content_hash


class SearchExperiences(Contract):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=3, ge=1, le=5)


class SkillHandle(Contract):
    skill_id: str


BUILTINS = [
    {
        "name": "Complete medicine comparisons",
        "applicability": "comparison missing cohort duplicate scope swapped labels",
        "procedure": "Search each requested medicine under the same disease/date filters. Preserve empty scopes. Analyze all requested scopes together, with their actual medicine names as labels. Validate before finalizing.",
        "source": "Engineering procedure from VNext review F01; not automatically learned",
        "adopted": True,
    },
    {
        "name": "Read source text before explanation",
        "applicability": "abstract trial eligibility evidence text missing context",
        "procedure": "Use the returned source_handle with read_source. Select a precise JSON pointer and follow next_offset until the relevant field is read. Preserve conflicting and exclusion statements. Do not infer article findings from titles.",
        "source": "Engineering procedure from VNext review F02; not automatically learned",
        "adopted": True,
    },
]


def entries(registry):
    records = [*BUILTINS, *getattr(registry, "experience_entries", [])]
    result = {}
    for item in records:
        procedure = item.get("procedure", item.get("component", {}).get("skill", ""))
        data = {**item, "procedure": procedure}
        identity = "skill_" + content_hash(data)[:24]
        result[identity] = {**data, "skill_id": identity, "version": content_hash(data)}
    return result


def register_skills(registry):
    def search(args, state):
        words = set(re.findall(r"\w+", args.query.casefold()))
        ranked = []
        for identity, item in entries(registry).items():
            text = " ".join(str(item.get(k, "")) for k in ("name", "applicability", "procedure", "families"))
            score = len(words & set(re.findall(r"\w+", text.casefold())))
            if score:
                ranked.append((score, identity, item))
        ranked.sort(key=lambda v: (-v[0], v[1]))
        return {
            "matches": [
                {
                    "skill_id": identity,
                    "name": item.get("name", item.get("id")),
                    "applicability": item["applicability"],
                    "adopted": item["adopted"],
                    "version": item["version"],
                }
                for _, identity, item in ranked[: args.limit]
            ],
            "selection": "keyword overlap; negative lessons are readable but cannot be activated",
        }

    def read(args, state):
        item = entries(registry).get(args.skill_id)
        if not item:
            raise ValueError("Unknown skill version")
        return item

    def apply(args, state):
        item = read(args, state)
        if args.skill_id not in state.get("read_skill_ids", []):
            raise ValueError("Read the skill before activating it")
        if not item["adopted"]:
            raise ValueError("Negative experiences are lessons, not executable procedures")
        return {"skill_id": args.skill_id, "version": item["version"], "procedure": item["procedure"][:1600]}

    registry.register(
        "search_experiences",
        "Find procedures or positive/negative public execution lessons when stuck or facing an evidence gap. Optional for simple tasks.",
        SearchExperiences,
        search,
    )
    registry.register(
        "read_skill",
        "Read a specific immutable skill version, applicability and provenance.",
        SkillHandle,
        read,
    )
    registry.register(
        "apply_skill",
        "Activate a previously read adopted procedure for this run. Activation is logged; it does not establish improvement.",
        SkillHandle,
        apply,
    )
