"""Freeze engineer-authored semantic cases; expectations never enter planner context."""

import json
from pathlib import Path

from app.models import AnalysisPlan, Cohort, Filters

ROOT = Path(__file__).resolve().parents[1]


def main():
    cases = []

    def add(
        name,
        query,
        *,
        operation="category",
        group_by="phase",
        filters=None,
        cohorts=None,
        action="execute",
        seed=None,
        pending=None,
        **fields,
    ):
        expected = {"action": action}
        if action == "execute":
            expected.update(
                operation=operation,
                group_by=group_by,
                cohorts=cohorts or [Filters(**(filters or {})).model_dump()],
                **fields,
            )
        case = {
            "id": name,
            "split": "development",
            "turns": [
                {
                    "request": {
                        "query": query,
                        "language": "zh" if any("\u4e00" <= c <= "\u9fff" for c in query) else "en",
                    },
                    "expected": expected,
                }
            ],
        }
        if seed:
            case["seed_plan"] = seed
        if pending:
            case["seed_pending"] = pending
        cases.append(case)
        return case

    add(
        "melanoma_phase",
        "Show phase distribution for melanoma studies involving pembrolizumab.",
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"]},
    )
    add(
        "asthma_status",
        "Show status counts for asthma studies involving dupilumab.",
        group_by="status",
        filters={"condition": "asthma", "interventions": ["dupilumab"]},
    )
    add(
        "diabetes_countries",
        "Show the top 5 countries for type 2 diabetes studies involving metformin.",
        group_by="country",
        top_n=5,
        filters={"condition": "type 2 diabetes", "interventions": ["metformin"]},
    )
    add(
        "breast_sponsor",
        "Show the 7 largest lead sponsors by distinct breast cancer trial count for trastuzumab.",
        group_by="sponsor",
        top_n=7,
        filters={"condition": "breast cancer", "interventions": ["trastuzumab"]},
    )
    add(
        "rheumatoid_histogram",
        "Show a 6-bin enrollment histogram of rheumatoid arthritis studies involving adalimumab, actual enrollment only.",
        operation="histogram",
        histogram_bins=6,
        filters={
            "condition": "rheumatoid arthritis",
            "interventions": ["adalimumab"],
            "enrollment_type": "ACTUAL",
        },
    )
    add(
        "lung_trend",
        "Count lung cancer studies involving osimertinib by start year, starts from 2018 through 2025 inclusive.",
        operation="time_series",
        time_field="start_year",
        filters={
            "condition": "lung cancer",
            "interventions": ["osimertinib"],
            "start_year": 2018,
            "end_year": 2025,
        },
    )
    add(
        "psoriasis_zh",
        "按试验阶段统计银屑病中使用司库奇尤单抗（secukinumab）的研究。",
        filters={"condition": "psoriasis", "interventions": ["secukinumab"]},
    )
    add(
        "hepatitis_zh",
        "统计乙型肝炎（hepatitis B）中替诺福韦（tenofovir）研究的招募状态分布。",
        group_by="status",
        filters={"condition": "hepatitis B", "interventions": ["tenofovir"]},
    )
    add(
        "or_population",
        "Show phase distribution for melanoma trials involving either pembrolizumab OR nivolumab, as ONE pooled cohort.",
        filters={
            "condition": "melanoma",
            "interventions": ["pembrolizumab", "nivolumab"],
            "intervention_match": "any",
        },
    )
    add(
        "and_population",
        "Show phase distribution for melanoma trials containing BOTH pembrolizumab AND nivolumab in the same study, as one cohort.",
        filters={
            "condition": "melanoma",
            "interventions": ["pembrolizumab", "nivolumab"],
            "intervention_match": "all",
        },
    )
    comparison = [
        Filters(condition="melanoma", interventions=[d]).model_dump() for d in ["pembrolizumab", "nivolumab"]
    ]
    add(
        "compare_drugs",
        "Compare phase distributions: pembrolizumab versus nivolumab in melanoma, two separate cohorts.",
        cohorts=comparison,
    )
    add(
        "compare_conditions",
        "Compare status distributions for asthma versus COPD, using dupilumab studies in both.",
        group_by="status",
        cohorts=[Filters(condition=c, interventions=["dupilumab"]).model_dump() for c in ["asthma", "COPD"]],
    )
    add(
        "phase_combined",
        "Count melanoma pembrolizumab studies by status, including phase 2 or phase 3 (including combined phases).",
        group_by="status",
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"], "phases": ["PHASE2", "PHASE3"]},
    )
    add(
        "country_or",
        "Show phase counts for asthma dupilumab studies with sites in Canada or the United States. Keep both countries as an OR filter.",
        filters={
            "condition": "asthma",
            "interventions": ["dupilumab"],
            "countries": ["Canada", "United States"],
        },
    )
    add(
        "recruiting",
        "Show countries for currently RECRUITING melanoma pembrolizumab studies.",
        group_by="country",
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"], "statuses": ["RECRUITING"]},
    )
    add(
        "posted_axis",
        "Show first-posted-year counts for rheumatoid arthritis adalimumab studies. Do not apply any date bounds.",
        operation="time_series",
        time_field="first_posted_year",
        filters={"condition": "rheumatoid arthritis", "interventions": ["adalimumab"]},
    )
    add(
        "sponsor_drug",
        "Show a lead-sponsor to drug network for asthma studies involving dupilumab, top 8 links.",
        operation="network",
        relationship="sponsor_drug",
        top_n=8,
        filters={"condition": "asthma", "interventions": ["dupilumab"]},
    )
    add(
        "drug_cooccur",
        "Show the top 12 drug co-occurrence links within individual breast cancer trastuzumab studies. I understand this is study-level, not arm-level.",
        operation="network",
        relationship="drug_cooccurrence",
        top_n=12,
        filters={"condition": "breast cancer", "interventions": ["trastuzumab"]},
    )
    add(
        "shared_sponsor",
        "Show the 5 strongest sponsor pairs linked through shared drugs in melanoma pembrolizumab studies. Weight by distinct shared interventions.",
        operation="network",
        relationship="sponsor_shared_drug",
        top_n=5,
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"]},
    )
    add(
        "drug_only",
        "Show phase distribution of all registered studies involving semaglutide, no disease restriction.",
        filters={"interventions": ["semaglutide"]},
    )
    add(
        "lead_sponsor",
        "Show study-type distribution for studies led by Mayo Clinic involving Parkinson disease.",
        group_by="study_type",
        filters={"condition": "Parkinson disease", "sponsor": "Mayo Clinic"},
    )
    add(
        "sponsor_class",
        "Show sponsor class counts for Crohn disease infliximab studies.",
        group_by="sponsor_class",
        filters={"condition": "Crohn disease", "interventions": ["infliximab"]},
    )
    add(
        "intervention_types",
        "Show intervention type counts for migraine erenumab studies.",
        group_by="intervention_type",
        filters={"condition": "migraine", "interventions": ["erenumab"]},
    )
    add(
        "estimated_hist",
        "Show an enrollment histogram with 20 bins for obesity semaglutide studies, estimated enrollment only.",
        operation="histogram",
        histogram_bins=20,
        filters={"condition": "obesity", "interventions": ["semaglutide"], "enrollment_type": "ESTIMATED"},
    )
    add("ambiguous_pd", "Show phase distribution for PD trials.", action="clarify")
    add("missing_drug", "Show trials for this drug by phase.", action="clarify")
    add(
        "ambiguous_logic",
        "Show counts for melanoma studies involving pembrolizumab and nivolumab; I have not decided whether to pool, intersect, or compare them.",
        action="clarify",
    )
    add(
        "posted_bounds",
        "Show counts of studies first posted between 2015 and 2020 for melanoma pembrolizumab. Do not filter study start year.",
        action="clarify",
    )
    add(
        "efficacy",
        "Compare survival benefit and cure rates of pembrolizumab versus nivolumab in melanoma.",
        action="unsupported",
    )
    add("treatment", "Which melanoma drug should I take? Recommend a dose.", action="unsupported")
    add("full_site", "Fetch every study on ClinicalTrials.gov and chart all sponsors.", action="unsupported")
    add(
        "scatter",
        "Make a scatterplot of average participant age versus mortality for asthma dupilumab trials.",
        action="unsupported",
    )
    base = AnalysisPlan(
        operation="category",
        cohorts=[
            Cohort(
                id="a",
                label="Melanoma",
                filters=Filters(condition="melanoma", interventions=["pembrolizumab"], countries=["Canada"]),
            )
        ],
    ).model_dump()
    add(
        "followup_phase",
        "Keep the previous cohort, phase 3 only.",
        seed=base,
        filters={
            "condition": "melanoma",
            "interventions": ["pembrolizumab"],
            "countries": ["Canada"],
            "phases": ["PHASE3"],
        },
    )
    add(
        "followup_trend",
        "Keep all existing filters, but show start-year counts from 2015 to 2025.",
        seed=base,
        operation="time_series",
        time_field="start_year",
        filters={
            "condition": "melanoma",
            "interventions": ["pembrolizumab"],
            "countries": ["Canada"],
            "start_year": 2015,
            "end_year": 2025,
        },
    )
    add(
        "followup_top5",
        "Keep the previous cohort and show the top 5 lead sponsors.",
        seed=base,
        group_by="sponsor",
        top_n=5,
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"], "countries": ["Canada"]},
    )
    add(
        "followup_bins",
        "Keep the previous cohort, use an enrollment histogram with 20 bins.",
        seed=base,
        operation="histogram",
        histogram_bins=20,
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"], "countries": ["Canada"]},
    )
    add(
        "followup_clear_country",
        "Remove the country restriction only, keep the previous condition and drug, still grouped by phase.",
        seed=base,
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"]},
    )
    add(
        "new_topic",
        "New topic: show phase distribution for asthma dupilumab studies. Discard all previous filters.",
        seed=base,
        filters={"condition": "asthma", "interventions": ["dupilumab"]},
    )
    pending = {
        "request": {
            "query": "Phase distribution for melanoma and this drug",
            "filters": {"condition": "melanoma"},
        },
        "confirmed_filters": {"condition": "melanoma"},
        "replies": [{"query": "the infusion from my appointment"}],
        "questions": ["Please provide the drug name."],
    }
    add(
        "pending_resolved",
        "pembrolizumab",
        pending=pending,
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"]},
    )
    add(
        "pending_still_unclear",
        "The same infusion, I do not know its name.",
        pending=pending,
        action="clarify",
    )
    add(
        "pending_topic_switch",
        "New topic, forget that drug: show asthma dupilumab studies by status.",
        pending=pending,
        group_by="status",
        filters={"condition": "asthma", "interventions": ["dupilumab"]},
    )
    add(
        "zh_lung",
        "按开始年份统计肺癌中奥希替尼（osimertinib）的研究，开始年份为2019至2025年，包含边界。",
        operation="time_series",
        time_field="start_year",
        filters={
            "condition": "lung cancer",
            "interventions": ["osimertinib"],
            "start_year": 2019,
            "end_year": 2025,
        },
    )
    add(
        "brand_keytruda",
        "Show phase distribution of melanoma studies involving Keytruda (pembrolizumab). Use the generic drug name.",
        filters={"condition": "melanoma", "interventions": ["pembrolizumab"]},
    )
    add(
        "zh_compare",
        "比较黑色素瘤（melanoma）中 pembrolizumab 与 nivolumab 两组研究的阶段分布，两种药分别统计。",
        cohorts=comparison,
    )
    add(
        "explicit_all",
        "Phase counts for studies containing both specified drugs in melanoma, one intersection cohort.",
        filters={
            "condition": "melanoma",
            "interventions": ["pembrolizumab", "nivolumab"],
            "intervention_match": "all",
        },
    )["turns"][0]["request"]["filters"] = {
        "condition": "melanoma",
        "interventions": ["pembrolizumab", "nivolumab"],
        "intervention_match": "all",
    }
    add(
        "explicit_conflict",
        "Show asthma phase counts, while the structured disease is melanoma.",
        action="clarify",
    )["turns"][0]["request"]["filters"] = {"condition": "melanoma"}
    add(
        "unknown_drug",
        "Show phase distribution for melanoma with the drug XYZUNKNOWN-742, which I may have misspelled; please establish its identity first.",
        action="clarify",
    )
    conversation = add(
        "repeated_clarification", "For melanoma and this drug, show phase counts.", action="clarify"
    )
    conversation["turns"][0]["request"]["filters"] = {"condition": "melanoma"}
    conversation["turns"].extend(
        [
            {
                "request": {"query": "The infusion from my appointment", "language": "en"},
                "expected": {"action": "clarify"},
            },
            {
                "request": {"query": "pembrolizumab", "language": "en"},
                "expected": {
                    "action": "execute",
                    "operation": "category",
                    "group_by": "phase",
                    "cohorts": [Filters(condition="melanoma", interventions=["pembrolizumab"]).model_dump()],
                },
            },
        ]
    )
    assert len(cases) == 48
    # Fixed before the first model run, with a deterministic distributed holdout.
    for index, case in enumerate(cases):
        case["split"] = "heldout" if index % 4 == 3 else "development"
    document = {
        "version": "semantic-v1",
        "annotation": "Engineer-authored expectations; no independent clinical annotation. Search-name equivalence is explicitly enumerated by case. Heldout cases are excluded from prompt edits and future experience retrieval.",
        "cases": cases,
    }
    target = ROOT / "docs" / "evals" / "semantic_cases.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen {len(cases)} cases / {sum(len(c['turns']) for c in cases)} turns")


if __name__ == "__main__":
    main()
