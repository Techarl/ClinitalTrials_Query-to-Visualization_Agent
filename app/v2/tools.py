"""Shared typed tools. Every transport executes this registry, not a parallel kernel."""

import time
from collections import Counter
from dataclasses import dataclass

from app.analysis import resolve_pointer
from app.models import AnalysisPlan, Cohort, Filters
from app.reference import expected_network, expected_table
from app.sources import ClinicalTrialsClient, Snapshot, content_hash, source_query, utc_now
from app.v2.charts import advisories, full_citation, temporal_or_scatter
from app.v2.contracts import (
    AnalysisInput,
    Artifact,
    Coverage,
    DateRange,
    DetailInput,
    EvidenceInput,
    HandleInput,
    SearchInput,
    SelectInput,
    TaskSpec,
)
from app.v2.dates import FIELDS, in_range, source_date


@dataclass
class Tool:
    name: str
    description: str
    schema: type
    execute: object


class ToolRegistry:
    def __init__(self, settings, service, store, source=None):
        self.settings, self.service, self.store = settings, service, store
        self.source = source
        self.tools = {}
        self.register(
            "search_trials",
            "Fetch a complete source scope; returns a scope_id for later tools. Never drop task constraints.",
            SearchInput,
            self.search,
        )
        self.register(
            "inspect_query_scope",
            "Inspect official match count before full retrieval. Not an analysis or a sample-based statistic.",
            SearchInput,
            self.inspect_scope,
        )
        self.register(
            "profile_dataset",
            "Inspect missingness and date precision for an owned scope handle.",
            HandleInput,
            self.profile,
        )
        self.register(
            "analyze",
            "Calculate category, trend, histogram, scatter or network from scope handles. Deterministic counts and complete evidence.",
            AnalysisInput,
            self.analyze,
        )
        self.register(
            "select_entities",
            "Use a complete sponsor-shared-drug network to select top sponsors connected to an anchor, then derive exact sponsor cohorts within its root population.",
            SelectInput,
            self.select,
        )
        self.register(
            "fetch_trial_details",
            "Read one trial and precise fields within an owned scope.",
            DetailInput,
            self.details,
        )
        self.register(
            "inspect_evidence",
            "Page complete contributor evidence for one item in an owned result.",
            EvidenceInput,
            self.evidence,
        )
        self.register(
            "validate_result",
            "Independently verify an owned chart against source records and evidence before finalization.",
            HandleInput,
            self.validate,
        )

    def register(self, name, description, schema, execute):
        self.tools[name] = Tool(name, description, schema, execute)

    def descriptions(self):
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.schema.model_json_schema(),
            }
            for tool in self.tools.values()
        ]

    def call(self, name, arguments, state):
        if name not in self.tools:
            raise ValueError("Unknown tool")
        if not state.get("task"):
            raise ValueError("Define the task before calling data tools")
        if time.time() >= state["deadline_epoch"] or self.store.run(state["run_id"])["cancelled"]:
            raise TimeoutError("Run cancelled or deadline exhausted")
        tool = self.tools[name]
        args = tool.schema.model_validate(arguments)
        signature = content_hash(
            {
                "tool": name,
                "version": "tools-v2",
                "constraints": state["constraints_version"],
                "arguments": args.model_dump(mode="json"),
            }
        )
        journal = self.store.journal(state["run_id"], signature)
        if journal and journal["status"] == "completed":
            return {**journal["output"], "reused": True}
        entries = self.store.journal_entries(state["run_id"])
        if sum(e["attempts"] for e in entries if e["kind"] == "tool") >= self.settings.v2_max_tool_calls:
            raise TimeoutError("Tool-call budget exhausted")
        self.store.begin(
            state["run_id"], signature, "tool", {"name": name, "arguments": args.model_dump(mode="json")}
        )
        self.store.event(
            state["run_id"],
            "tool_started",
            {"tool": name, "input_hash": signature, "retry_unknown_attempt": journal is not None},
        )
        started = time.monotonic()
        try:
            output = tool.execute(args, state)
            output = {"tool": name, "status": "ok", **output}
        except (ValueError, KeyError, TimeoutError) as error:
            output = {
                "tool": name,
                "status": "error",
                "error_type": type(error).__name__,
                "message": str(error)[:600],
            }
        except Exception as error:
            output = {
                "tool": name,
                "status": "error",
                "error_type": type(error).__name__,
                "message": "Source or tool failed; no statistic was substituted.",
            }
        output["elapsed_seconds"] = round(time.monotonic() - started, 3)
        self.store.complete(state["run_id"], signature, output)
        self.store.event(
            state["run_id"],
            "tool_finished",
            {k: v for k, v in output.items() if k not in ("raw", "citations")},
        )
        return output

    def deadline(self, state):
        return time.monotonic() + max(0, state["deadline_epoch"] - time.time())

    def check_filters(self, args, state):
        task = TaskSpec.model_validate(state["task"])
        expected = task.filters.model_dump()
        actual = args.filters.model_dump()
        for key, value in expected.items():
            if key == "intervention_match" and len(actual["interventions"]) <= 1:
                continue
            if key == "interventions" and task.intervention_scope == "comparison":
                if len(actual[key]) == 1 and set(actual[key]) <= set(value):
                    continue
            if actual[key] != value:
                raise ValueError(f"Tool would change confirmed constraint: {key}")
        if args.date_range != task.date_range:
            raise ValueError("Tool date range must match the confirmed task range")
        return task

    def acquire(self, query, state):
        if self.source is not None:
            snapshot, studies = self.source.fetch(query, self.deadline(state))
            try:
                self.service.snapshots.load(snapshot.dataset_id)
            except FileNotFoundError:
                self.service.snapshots.save(snapshot, studies)
            return snapshot, studies
        cached = self.service.snapshots.cached(query, self.settings.snapshot_ttl_seconds)
        if cached:
            return cached
        settings = self.settings.model_copy(
            update={
                "max_source_records": self.settings.v2_max_source_records,
                "ctgov_page_size": 1000,
                "max_source_pages": max(100, self.settings.v2_max_source_records // 1000 + 2),
            }
        )
        client = ClinicalTrialsClient(settings)
        try:
            from app.v2.acquisition import fetch_partitioned

            snapshot, studies, audit = fetch_partitioned(
                client,
                query,
                self.deadline(state),
                cancelled=lambda: self.store.run(state["run_id"])["cancelled"],
            )
            self.store.asset(
                state["session_id"],
                state["run_id"],
                "acquisition",
                {"dataset_id": snapshot.dataset_id, **audit},
            )
            self.service.snapshots.save(snapshot, studies)
            return snapshot, studies
        finally:
            client.close()

    def search(self, args, state):
        self.check_filters(args, state)
        query = source_query(args.filters)
        if args.alias_handle:
            resolver = self.store.get(args.alias_handle, state["session_id"], "entity")
            mappings = {r["raw_name"]: r for r in resolver["resolutions"]}
            phrases = []
            for name in args.filters.interventions:
                mapping = mappings.get(name)
                if not mapping or mapping["resolution_status"] not in ("exact", "alias"):
                    raise ValueError("Alias expansion requires an unambiguous sourced mapping")
                candidate = mapping["candidates"][0]
                aliases = sorted(set([name, candidate["canonical_name"], *candidate["aliases"]]))
                if any('"' in alias or "\\" in alias for alias in aliases):
                    raise ValueError("Unsupported alias syntax")
                phrases.append("(" + " OR ".join('"' + alias + '"' for alias in aliases) + ")")
            if phrases and any(
                mappings[name]["resolution_status"] == "alias" for name in args.filters.interventions
            ):
                query["query.intr"] = (" AND " if args.filters.intervention_match == "all" else " OR ").join(
                    phrases
                )
        snapshot, studies = self.acquire(query, state)
        if not snapshot.complete:
            raise ValueError(
                f"Incomplete source acquisition: {snapshot.reason}; matched={snapshot.expected_count}, fetched={len(studies)}"
            )
        with self.service.graph.budget(self.deadline(state)):
            self.service.graph.import_snapshot(snapshot, studies)
            selected = {
                r["record"]["nct_id"] for r in self.service.graph.records(snapshot.dataset_id, args.filters)
            }
        dated = [
            r
            for r in studies
            if r["protocolSection"]["identificationModule"]["nctId"] in selected
            and in_range(r, args.date_range)
        ]
        scope = {
            "dataset_id": snapshot.dataset_id,
            "root_dataset_id": snapshot.dataset_id,
            "root_scope_id": None,
            "filters": args.filters.model_dump(),
            "date_range": args.date_range.model_dump(mode="json") if args.date_range else None,
            "nct_ids": [r["protocolSection"]["identificationModule"]["nctId"] for r in dated],
            "date_filter_excluded": len(selected) - len(dated),
            "alias_handle": args.alias_handle,
            "constraints_version": state["constraints_version"],
            "label": "Population",
            "lineage": [],
        }
        handle = self.store.asset(state["session_id"], state["run_id"], "scope", scope)
        return {
            "scope_id": handle,
            "matched_count": snapshot.expected_count,
            "selected_count": len(dated),
            "complete": True,
            "date_filter_excluded": scope["date_filter_excluded"],
            "query": query,
            "source_version": snapshot.source_version_after,
        }

    def inspect_scope(self, args, state):
        self.check_filters(args, state)
        if self.source:
            snapshot, _ = self.source.fetch(source_query(args.filters), self.deadline(state))
            return {
                "matched_count": snapshot.expected_count,
                "mode": "frozen_replay",
                "local_filters_pending": True,
            }
        client = ClinicalTrialsClient(self.settings)
        try:
            query = source_query(args.filters)
            raw = client.get(
                "/studies",
                {**query, "countTotal": "true", "pageSize": 1, "format": "json"},
                self.deadline(state),
            )
            if type(raw.get("totalCount")) is not int:
                raise ValueError("Official source count is missing")
            handle = self.store.asset(
                state["session_id"],
                state["run_id"],
                "source_count",
                {"query": query, "raw": raw, "at": utc_now()},
            )
            return {
                "matched_count": raw["totalCount"],
                "receipt_id": handle,
                "local_filters_pending": True,
                "full_record_limit": self.settings.v2_max_source_records,
            }
        finally:
            client.close()

    def scope(self, handle, state):
        scope = self.store.get(handle, state["session_id"], "scope")
        snapshot, studies = self.service.snapshots.load(scope["dataset_id"])
        ids = set(scope["nct_ids"])
        raw = [r for r in studies if r["protocolSection"]["identificationModule"]["nctId"] in ids]
        if len(raw) != len(ids) or not snapshot.complete:
            raise ValueError("Scope coverage verification failed")
        return scope, snapshot, raw

    def project(self, handle, state):
        scope, parent, raw = self.scope(handle, state)
        if len(raw) == parent.retrieved_unique_count:
            self.service.graph.import_snapshot(parent, raw, self.deadline(state))
            return scope, parent, raw
        dataset_id = "ds_" + content_hash({"root": parent.raw_hash, "selected": scope["nct_ids"]})[:32]
        query = {**parent.query, "v2.selection_hash": content_hash(scope["nct_ids"])}
        derived = Snapshot(
            dataset_id=dataset_id,
            query=query,
            query_hash=content_hash(query),
            started_at=parent.started_at,
            finished_at=parent.finished_at,
            source_version_before=parent.source_version_before,
            source_version_after=parent.source_version_after,
            expected_count=len(raw),
            retrieved_unique_count=len(raw),
            complete=True,
            raw_hash=content_hash(raw),
            schema_version="derived-snapshot-v2",
        )
        try:
            self.service.snapshots.load(dataset_id)
        except FileNotFoundError:
            self.service.snapshots.save(derived, raw)
        self.service.graph.import_snapshot(derived, raw, self.deadline(state))
        return scope, derived, raw

    def profile(self, args, state):
        _, _, raw = self.scope(args.handle, state)
        dates = {field: dict(Counter(source_date(r, field)["precision"] for r in raw)) for field in FIELDS}
        sponsors = Counter(
            r["protocolSection"]
            .get("sponsorCollaboratorsModule", {})
            .get("leadSponsor", {})
            .get("name", "Not reported")
            for r in raw
        )
        missing = sum(
            type(r["protocolSection"].get("designModule", {}).get("enrollmentInfo", {}).get("count"))
            is not int
            for r in raw
        )
        return {
            "scope_id": args.handle,
            "record_count": len(raw),
            "date_precision": dates,
            "missing_enrollment": missing,
            "sponsors": dict(sponsors.most_common(30)),
            "sponsors_clipped": len(sponsors) > 30,
        }

    def goal(self, goal_id, state, kind="chart", operation=None):
        goal = next((g for g in state["task"]["goals"] if g["id"] == goal_id), None)
        if (
            not goal
            or goal["kind"] != kind
            or (goal.get("operation") and operation and goal["operation"] != operation)
        ):
            raise ValueError("Result does not match a declared task goal")
        return goal

    def publish(self, artifact, state, payload):
        handle = self.store.asset(
            state["session_id"],
            state["run_id"],
            "result",
            {"artifact": artifact.model_dump(mode="json"), **payload},
        )
        self.validate(HandleInput(handle=handle), state)
        return {
            "result_id": handle,
            "goal_id": artifact.goal_id,
            "title": artifact.title,
            "coverage": artifact.coverage.model_dump(),
            "warnings": [w.model_dump() for w in artifact.warnings],
        }

    def artifact(self, handle, state):
        payload = self.store.get(handle, state["session_id"], "result")
        return Artifact.model_validate(
            {**payload["artifact"], "result_id": handle, "artifact_id": "art_" + handle[7:]}
        )

    def analyze(self, args, state):
        goal = self.goal(args.goal_id, state, operation=args.operation)
        for key, value in goal.get("parameters", {}).items():
            if value is not None and getattr(args, key) != value:
                raise ValueError(f"Analysis would change confirmed chart parameter: {key}")
        if args.labels and len(args.labels) != len(args.scope_ids):
            raise ValueError("Provide exactly one label per scope")
        if args.operation == "network" and len(args.scope_ids) != 1:
            raise ValueError("Network analysis requires one population")
        scopes = [self.project(handle, state) for handle in args.scope_ids]
        labels = args.labels or [
            scope.get("label", f"Cohort {i + 1}") for i, (scope, _, _) in enumerate(scopes)
        ]
        if len(set(labels)) != len(labels):
            raise ValueError("Comparison cohorts require distinct display labels")
        for scope, _, _ in scopes:
            self.check_filters(
                SearchInput(
                    filters=Filters.model_validate(scope["filters"]),
                    date_range=DateRange.model_validate(scope["date_range"])
                    if scope.get("date_range")
                    else None,
                ),
                state,
            )
        if len(scopes) > 1 and state["task"]["intervention_scope"] == "comparison":
            from app.v2.entities import resolve

            names = [
                scope["filters"]["interventions"][0]
                for scope, _, _ in scopes
                if len(scope["filters"]["interventions"]) == 1
            ]
            if len(names) > 1 and resolve(names)["same_entity_comparison"]:
                raise ValueError(
                    "These cohort names identify the same medicine; clarify whether the user wants one combined population or a different comparison"
                )
        legacy_plan = None
        if args.operation in ("time_series", "scatter"):
            visual, meta, evidence_ids = temporal_or_scatter(args, scopes, self.store.base, state["run_id"])
        else:
            cohorts = [
                Cohort(
                    id=f"c{i}",
                    label=args.labels[i] if args.labels else scope.get("label", f"Cohort {i + 1}"),
                    filters=Filters(
                        condition="v2 verified snapshot population",
                        countries=scope["filters"].get("countries", []) if args.group_by == "country" else [],
                    ),
                )
                for i, (scope, _, _) in enumerate(scopes)
            ]
            legacy_plan = AnalysisPlan(
                operation=args.operation,
                cohorts=cohorts,
                group_by=args.group_by,
                relationship=args.relationship,
                histogram_bins=args.histogram_bins,
                top_n=args.top_n,
                title=args.title,
            )
            datasets = {f"c{i}": (snapshot, raw) for i, (_, snapshot, raw) in enumerate(scopes)}
            with self.service.graph.budget(self.deadline(state)):
                visual_model, meta, provenance, _ = self.service.engine.run(
                    legacy_plan, datasets, state["run_id"], self.deadline(state)
                )
            visual, evidence_ids = visual_model.model_dump(), list(provenance["evidence"])
        meta.update(
            {
                "root_scopes": args.scope_ids,
                "lineage": [scope for scope, _, _ in scopes],
                "source_manifests": [snapshot.model_dump() for _, snapshot, _ in scopes],
                "analysis_parameters": args.model_dump(),
                "legacy_plan": legacy_plan.model_dump() if legacy_plan else None,
            }
        )
        coverage = Coverage(
            statistics_basis="complete_records",
            count_exactness="exact",
            population_coverage="complete",
            evidence_coverage="complete_contributors",
            fetched_count=sum(len(raw) for _, _, raw in scopes),
            matched_count=sum(len(raw) for _, _, raw in scopes),
        )
        alternatives = (
            ["bar", "map", "table"]
            if args.operation == "category" and args.group_by == "country" and len(scopes) == 1
            else ["bar", "table"]
            if args.operation == "category"
            else ["table"]
        )
        artifact = Artifact(
            artifact_id="",
            result_id="",
            goal_id=args.goal_id,
            source_type="clinicaltrials",
            scope_id=args.scope_ids[0],
            title=args.title,
            metric=meta["metric_definition"],
            visualization=visual,
            coverage=coverage,
            evidence_ids=evidence_ids,
            metadata=meta,
            warnings=advisories(visual, meta),
            alternative_renderers=alternatives,
            dependencies=sorted(
                {
                    item["result_id"]
                    for scope, _, _ in scopes
                    for item in scope.get("lineage", [])
                    if "result_id" in item
                }
            ),
        )
        output = self.publish(artifact, state, {"scope_ids": args.scope_ids})
        if args.operation == "network":
            output["network_summary"] = {
                "nodes": visual["data"]["nodes"][:30],
                "edges": visual["data"]["edges"][:20],
                "presentation_clipping": meta["presentation_clipping"],
            }
        else:
            output["summary"] = {"rows": visual["data"][:12], "row_count": len(visual["data"])}
        return output

    def select(self, args, state):
        artifact = self.artifact(args.result_id, state)
        if (
            artifact.scope_id != args.scope_id
            or artifact.metadata.get("analysis_parameters", {}).get("relationship") != "sponsor_shared_drug"
        ):
            raise ValueError("Selection requires a shared-drug network from the same population")
        # Requery the complete scoped graph, not a display-clipped top-N network.
        scope, snapshot, raw = self.project(args.scope_id, state)
        with self.service.graph.budget(self.deadline(state)):
            matches = self.service.graph.network(snapshot.dataset_id, Filters(), "sponsor_shared_drug")
        candidates = {}
        for edge in matches:
            side = (
                "source"
                if edge["source_label"].casefold() == args.anchor_sponsor.casefold()
                else "target"
                if edge["target_label"].casefold() == args.anchor_sponsor.casefold()
                else None
            )
            if side:
                other = "target" if side == "source" else "source"
                row = candidates.setdefault(
                    edge[other], {"id": edge[other], "name": edge[other + "_label"], "shared_ids": set()}
                )
                row["shared_ids"].add(edge["shared_id"])
        if not candidates:
            raise ValueError(
                "Anchor sponsor has no shared-drug relationships in this population; inspect exact sponsor names"
            )
        ranked = sorted(candidates.values(), key=lambda item: (-len(item["shared_ids"]), item["id"]))[
            : args.top_n
        ]
        entity_goals = [goal for goal in state["task"]["goals"] if goal["kind"] == "entity"]
        ranking_result = None
        if len(entity_goals) == 1:
            from app.v2.receipts import ranking_from_records

            independent = ranking_from_records(raw, args.anchor_sponsor, args.top_n)
            if [(r["id"], len(r["shared_ids"])) for r in ranked] != [
                (r["id"], r["shared_intervention_count"]) for r in independent
            ]:
                raise ValueError("Graph ranking differs from raw-record oracle")
            indexed = {r["protocolSection"]["identificationModule"]["nctId"]: r for r in raw}
            ranking_rows = []
            for row in independent:
                evidence_id = self.store.base.evidence(
                    state["run_id"],
                    [full_citation(snapshot.dataset_id, indexed[nct]) for nct in row["contributors"]],
                    {
                        "operation": "shared_drug_ranking",
                        "anchor": args.anchor_sponsor,
                        "sponsor": row["name"],
                        "scope_id": args.scope_id,
                    },
                )
                ranking_rows.append(
                    {k: v for k, v in row.items() if k != "contributors"} | {"evidence_set_id": evidence_id}
                )
            ranking = Artifact(
                artifact_id="",
                result_id="",
                goal_id=entity_goals[0]["id"],
                source_type="entity",
                scope_id=args.scope_id,
                title="Sponsors ranked by shared interventions",
                metric="Distinct shared drug identities within the complete root population",
                coverage=Coverage(
                    statistics_basis="entity",
                    count_exactness="exact",
                    population_coverage="complete",
                    evidence_coverage="complete_contributors",
                    fetched_count=len(raw),
                    matched_count=len(raw),
                ),
                dependencies=[args.result_id],
                evidence_ids=[r["evidence_set_id"] for r in ranking_rows],
                metadata={
                    "anchor": args.anchor_sponsor,
                    "ranking": ranking_rows,
                    "top_n": args.top_n,
                    "root_scope_id": args.scope_id,
                    "network_result_id": args.result_id,
                },
            )
            ranking_result = self.publish(ranking, state, {})["result_id"]
        selected = []
        for row in ranked:
            ids = [
                r["protocolSection"]["identificationModule"]["nctId"]
                for r in raw
                if r["protocolSection"]
                .get("sponsorCollaboratorsModule", {})
                .get("leadSponsor", {})
                .get("name", "")
                .casefold()
                == row["name"].casefold()
            ]
            derived = {
                **scope,
                "root_scope_id": scope.get("root_scope_id") or args.scope_id,
                "nct_ids": ids,
                "label": row["name"],
                "lineage": [
                    *scope["lineage"],
                    {
                        "parent_scope": args.scope_id,
                        "result_id": ranking_result or args.result_id,
                        "selection": "top_shared_drug_sponsors",
                        "anchor": args.anchor_sponsor,
                        "entity_id": row["id"],
                        "rank": len(selected) + 1,
                    },
                ],
            }
            handle = self.store.asset(state["session_id"], state["run_id"], "scope", derived)
            selected.append(
                {
                    "scope_id": handle,
                    "sponsor_id": row["id"],
                    "sponsor_name": row["name"],
                    "shared_intervention_count": len(row["shared_ids"]),
                    "trial_count": len(ids),
                }
            )
        return {
            "selected": selected,
            **({"result_id": ranking_result} if ranking_result else {}),
            "ranking_scope": "complete_root_population",
            "root_scope_id": args.scope_id,
            "tie_break": "canonical sponsor ID ascending",
            "candidate_count": len(candidates),
        }

    def details(self, args, state):
        _, snapshot, raw = self.scope(args.scope_id, state)
        for record in raw:
            if record["protocolSection"]["identificationModule"]["nctId"] == args.nct_id:
                return {"raw": record, "citation": full_citation(snapshot.dataset_id, record)}
        raise ValueError("Trial is not in the authorized scope")

    def evidence(self, args, state):
        artifact = self.artifact(args.result_id, state)
        if args.evidence_id not in artifact.evidence_ids:
            raise ValueError("Evidence does not belong to this result")
        page = self.store.base.evidence_page(args.evidence_id, args.offset, args.limit)
        if page is None:
            raise ValueError("Evidence is missing")
        return page

    def validate(self, args, state):
        artifact = self.artifact(args.handle, state)
        kind = (
            "literature"
            if artifact.source_type == "pubmed"
            else "entity"
            if artifact.source_type == "entity"
            else "chart"
        )
        parameters = artifact.metadata.get("analysis_parameters", {})
        goal = self.goal(artifact.goal_id, state, kind, parameters.get("operation"))
        for key, value in goal.get("parameters", {}).items():
            if value is not None and parameters.get(key) != value:
                raise ValueError("Prior artifact chart parameters differ from current task")
        if (
            artifact.source_type != "clinicaltrials"
            or artifact.coverage.statistics_basis != "complete_records"
        ):
            from app.v2.receipts import verify_receipts

            if (
                artifact.coverage.statistics_basis == "official_counts"
                and artifact.metadata.get("filters") != state["task"]["filters"]
            ):
                raise ValueError("Official count population differs from current task")
            return verify_receipts(self, artifact, state)
        payload = self.store.get(args.handle, state["session_id"], "result")
        scopes = []
        for handle, manifest in zip(payload["scope_ids"], artifact.metadata["source_manifests"], strict=True):
            scope = self.store.get(handle, state["session_id"], "scope")
            self.check_filters(
                SearchInput(filters=scope["filters"], date_range=scope.get("date_range")), state
            )
            snapshot, records = self.service.snapshots.load(manifest["dataset_id"])
            if snapshot.model_dump() != manifest or {
                r["protocolSection"]["identificationModule"]["nctId"] for r in records
            } != set(scope["nct_ids"]):
                raise ValueError("Result population no longer matches its immutable scope")
            scopes.append((scope, snapshot, records))
        raw_by_dataset = {
            snapshot.dataset_id: {r["protocolSection"]["identificationModule"]["nctId"]: r for r in records}
            for _, snapshot, records in scopes
        }
        evidence = {}
        fields_checked = 0
        for ev in artifact.evidence_ids:
            page = self.store.base.evidence_page(ev, 0, 1000000)
            if page is None or page["next_offset"] is not None:
                raise ValueError("Full evidence unavailable")
            for citation in page["citations"]:
                raw = raw_by_dataset[citation["dataset_id"]][citation["nct_id"]]
                for field in citation["fields"]:
                    if resolve_pointer(raw, field["path"]) != field["value"]:
                        raise ValueError("Evidence field does not match source")
                    fields_checked += 1
            evidence[ev] = {c["nct_id"] for c in page["citations"]}
        plan = artifact.metadata.get("legacy_plan")
        visual = artifact.visualization
        if plan:
            records = {c["label"]: values[2] for c, values in zip(plan["cohorts"], scopes, strict=True)}
            if plan["operation"] == "network":
                edges, nodes, _, _ = expected_network(
                    plan, records, min(plan["top_n"], self.settings.network_max_edges)
                )
                if len(edges) != len(visual["data"]["edges"]) or len(nodes) != len(visual["data"]["nodes"]):
                    raise ValueError("Network item coverage mismatch")
                for actual, (pair, expected) in zip(visual["data"]["edges"], edges, strict=True):
                    if (
                        (actual["source"], actual["target"]) != pair
                        or actual["weight"] != expected["weight"]
                        or evidence[actual["evidence_set_id"]] != expected["ids"]
                    ):
                        raise ValueError("Network count or contributors mismatch")
                for node in visual["data"]["nodes"]:
                    if (
                        node["id"] not in nodes
                        or node["trial_count"] != len(nodes[node["id"]])
                        or evidence[node["evidence_set_id"]] != nodes[node["id"]]
                    ):
                        raise ValueError("Network node evidence mismatch")
            else:
                expected, _, _ = expected_table(plan, records)
                if len(expected) != len(visual["data"]):
                    raise ValueError("Chart item coverage mismatch")
                for actual, item in zip(visual["data"], expected, strict=True):
                    if (
                        actual["trial_count"] != len(item["ids"])
                        or actual["contributor_count"] != len(item["ids"])
                        or actual["cohort"] != item["cohort"]
                        or evidence[actual["evidence_set_id"]] != item["ids"]
                    ):
                        raise ValueError("Chart value or contributor mismatch")
                    for key in ("category", "bin_start", "bin_end"):
                        if actual.get(key) != item.get(key):
                            raise ValueError("Chart category or interval mismatch")
                if plan["operation"] == "histogram":
                    for label, raw in records.items():
                        values = {
                            r["protocolSection"]["identificationModule"]["nctId"]: r["protocolSection"]
                            .get("designModule", {})
                            .get("enrollmentInfo", {})
                            .get("count")
                            for r in raw
                        }
                        rows = [r for r in visual["data"] if r["cohort"] == label]
                        seen = Counter()
                        for index, row in enumerate(rows):
                            for nct in evidence[row["evidence_set_id"]]:
                                value = values[nct]
                                if (
                                    value is None
                                    or not row["bin_start"] <= value
                                    or not (
                                        value < row["bin_end"]
                                        or index == len(rows) - 1
                                        and value <= row["bin_end"]
                                    )
                                ):
                                    raise ValueError("Contributor outside declared histogram interval")
                                seen[nct] += 1
                        if seen != Counter({nct: 1 for nct, value in values.items() if value is not None}):
                            raise ValueError("Histogram contributors must occur exactly once")
        else:
            from app.v2.verify import verify_extended

            verify_extended(artifact, scopes, evidence)
        return {
            "result_id": args.handle,
            "validated": True,
            "exact_fields_checked": fields_checked,
            "complete_item_sets_checked": True,
        }
