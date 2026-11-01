"""Hidden semantic assertions plus raw-record/receipt verification, outside runtime prompts."""

import json

from app.models import Filters
from app.v2.contracts import HandleInput

SCORER_VERSION = "v3-request-coverage-and-reading"


def equivalent(actual, expected):
    if isinstance(expected, list):
        return sorted(str(v).casefold() for v in actual or []) == sorted(str(v).casefold() for v in expected)
    if isinstance(expected, str):
        return str(actual).casefold() == expected.casefold()
    return actual == expected


def grade(result, expected, registry, state):
    failures = []
    if result.status != expected["status"]:
        failures.append("status")
    task = result.task.model_dump(mode="json") if result.task else {}
    scope = expected.get("intervention_scope", "population")
    if expected["status"] == "completed" and task.get("intervention_scope") != scope:
        failures.append("constraint:intervention_scope")
    wanted_filters = expected.get("filters", {})
    if expected["status"] == "completed":
        wanted_filters = Filters(**wanted_filters).model_dump()
    for key, value in wanted_filters.items():
        if key == "intervention_match" and len(wanted_filters.get("interventions", [])) <= 1:
            continue  # AND/OR is equivalent for a single medicine.
        if not equivalent(task.get("filters", {}).get(key), value):
            failures.append("constraint:" + key)
    if expected["status"] == "completed" and "date_range" not in expected and task.get("date_range"):
        failures.append("unrequested_date_range")
    for key, value in expected.get("date_range", {}).items():
        if (task.get("date_range") or {}).get(key) != value:
            failures.append("date_range:" + key)
    for wanted in expected.get("artifacts", []):
        if not any(
            all(equivalent(a.metadata.get("analysis_parameters", {}).get(k), v) for k, v in wanted.items())
            for a in result.artifacts
        ):
            failures.append("artifact:" + str(wanted))
    if expected.get("literature") and not any(a.source_type == "pubmed" for a in result.artifacts):
        failures.append("literature")
    if expected.get("literature_query"):
        literature = [a for a in result.artifacts if a.source_type == "pubmed"]
        if not any(a.metadata.get("search_query") == expected["literature_query"] for a in literature):
            failures.append("literature_query")
        count = expected.get("literature_limit")
        if count and not any(
            len(a.metadata.get("articles", [])) == min(count, a.coverage.matched_count or 0)
            for a in literature
        ):
            failures.append("literature_count")
        read_text = " ".join(
            o.get("text", "")
            for o in state.get("observations", [])
            if o.get("tool") == "read_source" and o.get("status") == "ok"
        )
        abstracts = [
            part["text"]
            for a in literature
            for article in a.metadata.get("articles", [])
            for part in article.get("abstract", [])
            if part.get("text")
        ]
        if abstracts and not all(
            text in read_text or json.dumps(text, ensure_ascii=False)[1:-1] in read_text for text in abstracts
        ):
            failures.append("literature_body_unread")
    if expected.get("no_charts") and any(a.visualization for a in result.artifacts):
        failures.append("unsupported_execution")
    if expected.get("specific_question") and (
        not result.questions or all(len(q.split()) < 3 for q in result.questions)
    ):
        failures.append("question_not_actionable")
    checks = []
    for artifact in result.artifacts:
        try:
            checks.append(registry.validate(HandleInput(handle=artifact.result_id), state))
        except Exception as error:
            failures.append("source_verification:" + type(error).__name__)
        if expected.get("sql_oracle") and artifact.source_type == "clinicaltrials":
            try:
                from evaluation.sql_oracle import verify_sql_artifact

                checks.append(verify_sql_artifact(artifact, registry, state))
            except Exception as error:
                failures.append("sql_oracle:" + type(error).__name__)
    if expected.get("dependent_selection"):
        selections = [
            o for o in state["observations"] if o.get("tool") == "select_entities" and o.get("status") == "ok"
        ]
        trends = [
            a
            for a in result.artifacts
            if a.metadata.get("analysis_parameters", {}).get("operation") == "time_series"
        ]
        if not selections or not trends:
            failures.append("dependent_selection_missing")
        else:
            expected_scopes = {s["scope_id"] for s in selections[-1]["selected"]}
            if not any(set(a.metadata["root_scopes"]) == expected_scopes and a.dependencies for a in trends):
                failures.append("invented_or_unrelated_cohorts")
            if expected.get("anchor_sponsor") or expected.get("selection_top_n"):
                journal = registry.store.journal_entries(state["run_id"])
                selections_args = [
                    entry["input"]["arguments"]
                    for entry in journal
                    if entry["kind"] == "tool"
                    and entry["input"].get("name") == "select_entities"
                    and (entry.get("output") or {}).get("status") == "ok"
                ]
                if not any(
                    (not expected.get("anchor_sponsor") or a["anchor_sponsor"] == expected["anchor_sponsor"])
                    and (not expected.get("selection_top_n") or a["top_n"] == expected["selection_top_n"])
                    for a in selections_args
                ):
                    failures.append("dependent_selection_request_mismatch")
    return {
        "scorer_version": SCORER_VERSION,
        "correct": not failures,
        "failures": failures,
        "failure_codes": [{"code": f.split(":", 1)[0], "detail": f} for f in failures],
        "independent_checks": checks,
        "incorrect_execution": result.status in ("completed", "partial") and bool(failures),
        "unnecessary_clarification": expected["status"] != "needs_clarification"
        and result.status == "needs_clarification",
        "question_quality_note": "Automatic actionability check only; semantic usefulness needs a human sample.",
    }
