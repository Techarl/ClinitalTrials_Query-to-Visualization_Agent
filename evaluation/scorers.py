"""Hidden semantic assertions plus raw-record/receipt verification, outside runtime prompts."""

import json

from app.models import Filters
from app.v2.contracts import HandleInput

SCORER_VERSION = "v4-selection-provenance-and-reading"


def equivalent(actual, expected):
    if isinstance(expected, list):
        return sorted(str(v).casefold() for v in actual or []) == sorted(str(v).casefold() for v in expected)
    if isinstance(expected, str):
        return str(actual).casefold() == expected.casefold()
    return actual == expected


def dependency_failures(expected, artifacts, journal):
    """Bind the requested anchor/N to the selection actually backing the trend and each lineage."""
    if not expected.get("dependent_selection"):
        return []
    by_handle = {a["result_id"]: a for a in artifacts}
    trends = [
        a for a in artifacts if a["metadata"].get("analysis_parameters", {}).get("operation") == "time_series"
    ]
    selections = [
        e
        for e in journal
        if e["kind"] == "tool"
        and e["input"].get("name") == "select_entities"
        and (e.get("output") or {}).get("status") == "ok"
    ]
    if not selections or not trends:
        return ["dependent_selection_missing"]
    for entry in selections:
        args, output = entry["input"]["arguments"], entry["output"]
        if expected.get("anchor_sponsor") and args["anchor_sponsor"] != expected["anchor_sponsor"]:
            continue
        if expected.get("selection_top_n") and args["top_n"] != expected["selection_top_n"]:
            continue
        selected = output.get("selected", [])
        scopes = {r["scope_id"]: r for r in selected}
        if not selected or len(scopes) != len(selected):
            continue
        if len(scopes) != min(args["top_n"], output["candidate_count"]):
            continue
        if output["root_scope_id"] != args["scope_id"]:
            continue
        network = by_handle.get(args["result_id"])
        if (
            not network
            or network["metadata"].get("analysis_parameters", {}).get("relationship") != "sponsor_shared_drug"
        ):
            continue
        if network["metadata"].get("root_scopes") != [args["scope_id"]]:
            continue
        dependency = output.get("result_id") or args["result_id"]
        if dependency not in by_handle:
            continue
        if dependency != args["result_id"] and args["result_id"] not in by_handle[dependency].get(
            "dependencies", []
        ):
            continue
        for trend in trends:
            roots = trend["metadata"].get("root_scopes", [])
            lineage_scopes = trend["metadata"].get("lineage", [])
            if (
                len(roots) != len(scopes)
                or set(roots) != set(scopes)
                or dependency not in trend.get("dependencies", [])
            ):
                continue
            if len(lineage_scopes) != len(roots):
                continue
            if all(
                any(
                    step.get("selection") == "top_shared_drug_sponsors"
                    and step.get("entity_id") == scopes[scope_id]["sponsor_id"]
                    and step.get("anchor") == args["anchor_sponsor"]
                    and step.get("parent_scope") == args["scope_id"]
                    and step.get("result_id") == dependency
                    for step in scope.get("lineage", [])
                )
                for scope_id, scope in zip(roots, lineage_scopes, strict=True)
            ):
                return []
    return ["dependent_selection_unbound_to_requested_anchor_and_n"]

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
        if not literature_body_read(literature, state.get("observations", [])):
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
        failures.extend(dependency_failures(
            expected,
            [a.model_dump(mode="json") for a in result.artifacts],
            registry.store.journal_entries(state["run_id"]),
        ))
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


def read_source_segments(observations):
    """Reconstruct only contiguous, non-conflicting pages of one immutable field."""
    grouped = {}
    for row in observations:
        if row.get("tool") != "read_source" or row.get("status") != "ok":
            continue
        handle, pointer = row.get("source_handle"), row.get("pointer", "")
        offset, text = row.get("offset"), row.get("text")
        if not isinstance(handle, str) or not isinstance(pointer, str) or not isinstance(text, str):
            continue
        if type(offset) is not int or offset < 0:
            continue
        if row.get("total_characters") is not None and (
            type(row["total_characters"]) is not int or offset + len(text) > row["total_characters"]
        ):
            continue
        key = handle, pointer, row.get("source_hash")
        grouped.setdefault(key, []).append((offset, text))
    result = []
    for (handle, pointer, source_hash), pages in grouped.items():
        segments, conflict = [], False
        for offset, text in sorted(pages, key=lambda p: (p[0], len(p[1]), p[1])):
            if not segments or offset > segments[-1][0] + len(segments[-1][1]):
                segments.append([offset, text])
                continue
            start, previous = segments[-1]
            overlap = min(len(text), start + len(previous) - offset)
            if previous[offset - start:offset - start + overlap] != text[:overlap]:
                conflict = True
                break
            segments[-1][1] += text[overlap:]
        if not conflict:
            result.extend({"source_handle": handle, "pointer": pointer, "source_hash": source_hash,
                           "offset": offset, "text": text} for offset, text in segments)
    return result


def literature_body_read(literature, observations):
    segments = read_source_segments(observations)
    for artifact in literature:
        # Current PubMed stores a literature receipt handle in metadata and returns
        # a different readable handle. Bind it through the successful fetch result.
        handles = {o["source_handle"] for o in observations
                   if o.get("tool") in ("search_pubmed", "fetch_articles") and o.get("status") == "ok"
                   and o.get("result_id") == artifact.result_id and isinstance(o.get("source_handle"), str)}
        if isinstance(artifact.metadata.get("readable_source_handle"), str):
            handles.add(artifact.metadata["readable_source_handle"])
        for article_index, article in enumerate(artifact.metadata.get("articles", [])):
            for part_index, part in enumerate(article.get("abstract", [])):
                text = part.get("text")
                if not text:
                    continue
                field = f"/articles/{article_index}/abstract/{part_index}/text"
                if not any(
                    segment["source_handle"] in handles
                    and (not segment["pointer"] or field == segment["pointer"] or field.startswith(segment["pointer"] + "/"))
                    and (text in segment["text"] or json.dumps(text, ensure_ascii=False)[1:-1] in segment["text"])
                    for segment in segments
                ):
                    return False
    return True
