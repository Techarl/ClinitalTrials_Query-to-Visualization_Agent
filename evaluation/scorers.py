"""Hidden semantic assertions plus raw-record/receipt verification, outside runtime prompts."""

from app.v2.contracts import HandleInput


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
    if "intervention_scope" in expected and task.get("intervention_scope") != expected["intervention_scope"]:
        failures.append("constraint:intervention_scope")
    for key, value in expected.get("filters", {}).items():
        if not equivalent(task.get("filters", {}).get(key), value):
            failures.append("constraint:" + key)
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
    return {
        "correct": not failures,
        "failures": failures,
        "independent_checks": checks,
        "incorrect_execution": result.status in ("completed", "partial") and bool(failures),
        "unnecessary_clarification": expected["status"] != "needs_clarification"
        and result.status == "needs_clarification",
        "question_quality_note": "Automatic actionability check only; semantic usefulness needs a human sample.",
    }
