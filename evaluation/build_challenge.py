"""Build a frozen 66-episode challenge, distinct from the historical 50-turn set."""

import json
from pathlib import Path

from evaluation.contracts import Episode, Turn
from evaluation.splits import SPLITS, audit, opaque_id


def build():
    episodes = []
    for disease, drug, brand in [
        ("melanoma", "pembrolizumab", "Keytruda"),
        ("psoriasis", "secukinumab", "Cosentyx"),
        ("asthma", "omalizumab", "Xolair"),
        ("rheumatoid arthritis", "adalimumab", "Humira"),
    ]:
        filters = {"condition": disease, "interventions": [drug]}
        prefix = f"For {disease} studies involving {drug}, "

        def expected(operation="category", **values):
            return {
                "status": "completed",
                "filters": filters,
                "artifacts": [{"operation": operation, **values}],
            }

        def add(family, *turns):
            episodes.append(
                Episode(
                    id=opaque_id(disease, family),
                    family=family,
                    entity_group=disease,
                    split=SPLITS[disease],
                    turns=[Turn(**t) for t in turns],
                )
            )

        for group in ["phase", "status", "country"]:
            query = prefix + f"show counts by {group}, with full contributor citations."
            if group == "country":
                query = f"统计 {disease} 中涉及 {drug} 的注册研究，按国家分组，并保留每个国家的完整来源。"
            add(group, {"query": query, "expected": expected(group_by=group)})
        add(
            "histogram",
            {
                "query": prefix
                + "show enrollment in seven equal-width bins, retain all source contributions.",
                "expected": expected("histogram", histogram_bins=7),
            },
        )
        add(
            "scatter",
            {
                "query": prefix
                + "plot enrollment against duration in days using exact start and completion dates. Report exclusions.",
                "expected": expected("scatter", x="trial_duration", y="enrollment"),
            },
        )
        add(
            "completion_month",
            {
                "query": prefix
                + "count trials by completion month, excluding insufficient date precision explicitly.",
                "expected": expected("time_series", time_field="completion", time_grain="month"),
            },
        )
        add(
            "first_posted",
            {
                "query": prefix + "show counts by first-posted year, not by start year.",
                "expected": expected("time_series", time_field="first_posted", time_grain="year"),
            },
        )
        bounded = expected("time_series", time_field="primary_completion", time_grain="month")
        bounded["date_range"] = {"field": "primary_completion", "lower": "2020-01-01", "upper": "2025-12-31"}
        add(
            "date_range",
            {
                "query": prefix
                + "only include primary completion dates fully within 2020-01-01 through 2025-12-31, then count by primary-completion month.",
                "expected": bounded,
            },
        )
        for relationship, wording in [
            ("sponsor_drug", "lead-sponsor to drug relationships"),
            (
                "drug_cooccurrence",
                "study-level drug co-occurrence; do not claim same-arm combination treatment",
            ),
        ]:
            add(
                relationship,
                {
                    "query": prefix + "show " + wording + ".",
                    "expected": expected("network", relationship=relationship),
                },
            )
        us = {**filters, "countries": ["United States"]}
        phase3 = {**us, "phases": ["PHASE3"]}
        add(
            "followup_clear",
            {
                "query": prefix + "only include studies with a US location and show phase counts.",
                "expected": {**expected(group_by="phase"), "filters": us},
            },
            {
                "query": "Keep the disease and drug; only Phase 3 now, by start year.",
                "expected": {**expected("time_series", time_field="start"), "filters": phase3},
            },
            {
                "query": "Remove the US restriction, retain Phase 3 and the same disease and drug; keep the annual chart.",
                "expected": {
                    **expected("time_series"),
                    "filters": {**filters, "phases": ["PHASE3"], "countries": []},
                },
            },
        )
        add(
            "axis_followup",
            {
                "query": prefix + "count by start year.",
                "expected": expected("time_series", time_field="start"),
            },
            {
                "query": "Use completion month instead. Preserve all population constraints.",
                "expected": expected("time_series", time_field="completion", time_grain="month"),
            },
        )
        add(
            "same_entity",
            {
                "query": f"Compare {brand} versus {drug} studies for {disease} by phase. First verify their identities.",
                "expected": {"status": "needs_clarification", "specific_question": True},
            },
            {
                "query": f"Treat them as one medicine. Use only {drug}, retain {disease}, and show the phase counts.",
                "expected": expected(group_by="phase"),
            },
        )
        add(
            "clarification",
            {
                "query": f"For {disease}, compare {drug} with the other drug by phase.",
                "expected": {"status": "needs_clarification", "specific_question": True},
            },
            {
                "query": f"Cancel the comparison; use only {drug} and retain {disease}. Show phase counts.",
                "expected": expected(group_by="phase"),
            },
        )
        add(
            "unsupported",
            {
                "query": prefix
                + "determine the causal improvement in patient survival versus placebo using only registry metadata, and recommend treatment.",
                "expected": {"status": "limited", "no_charts": True},
            },
        )
        add(
            "literature",
            {
                "query": prefix
                + f'show phase counts and search PubMed for exactly "{drug} {disease} randomized trial", retrieving two abstracts. Keep article counts separate from trial counts.',
                "expected": {**expected(group_by="phase"), "literature": True},
            },
        )
    for family, ending in [
        ("dependent", ""),
        ("dependent_entity_goal", "Also return the ranked sponsors as a separate result."),
    ]:
        episodes.append(
            Episode(
                id=opaque_id("melanoma", family),
                family=family,
                entity_group="melanoma",
                split="development",
                turns=[
                    Turn(
                        query="Among melanoma studies involving pembrolizumab starting in 2015 through 2025, find the top three sponsors sharing drugs with Merck Sharp & Dohme LLC, show their shared-drug network and compare annual counts of the selected sponsors. "
                        + ending,
                        expected={
                            "status": "completed",
                            "filters": {
                                "condition": "melanoma",
                                "interventions": ["pembrolizumab"],
                                "start_year": 2015,
                                "end_year": 2025,
                            },
                            "artifacts": [
                                {"operation": "network", "relationship": "sponsor_shared_drug"},
                                {"operation": "time_series"},
                            ],
                            "dependent_selection": True,
                        },
                    )
                ],
            )
        )
    for disease, drugs in [
        ("melanoma", ["pembrolizumab", "nivolumab"]),
        ("asthma", ["omalizumab", "dupilumab"]),
    ]:
        for mode, wording in [
            ("all", "include both medicines in each study"),
            ("any", "include either medicine in one combined population"),
            ("comparison", "compare the two medicines as separate cohorts"),
        ]:
            filters = {
                "condition": disease,
                "interventions": drugs,
                "intervention_match": "all" if mode == "all" else "any",
            }
            episodes.append(
                Episode(
                    id=opaque_id(disease, "logic_" + mode),
                    family="logic_" + mode,
                    entity_group=disease,
                    split=SPLITS[disease],
                    turns=[
                        Turn(
                            query=f"For {disease}, use {drugs[0]} and {drugs[1]}; {wording}. Show phase counts and complete evidence.",
                            expected={
                                "status": "completed",
                                "filters": filters,
                                "intervention_scope": "comparison" if mode == "comparison" else "population",
                                "artifacts": [{"operation": "category", "group_by": "phase"}],
                            },
                        )
                    ],
                )
            )
    return episodes


if __name__ == "__main__":
    destination = Path("evaluation/cases_v2.json")
    episodes = build()
    destination.write_text(
        json.dumps(
            {
                "version": "vnext-challenge-v1",
                "split_audit": audit(episodes),
                "episodes": [e.model_dump() for e in episodes],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(audit(episodes))
