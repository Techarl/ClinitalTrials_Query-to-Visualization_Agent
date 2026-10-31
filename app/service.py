"""Execution of validated plans; all source data and graph calculations stay here."""

import time

from app.analysis import AnalysisEngine
from app.config import Settings
from app.graphdb import GraphRepository
from app.models import AnalysisPlan, AnalysisResponse
from app.sources import ClinicalTrialsClient, SnapshotStore, source_query, utc_now
from app.storage import StateStore


class AnalysisService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = StateStore(settings.data_directory)
        self.snapshots = SnapshotStore(settings.data_directory)
        self.graph = GraphRepository(settings)
        try:
            self.graph.setup()
        except Exception:
            self.graph.close()
            raise
        self.engine = AnalysisEngine(settings, self.graph, self.store)

    def close(self):
        self.graph.close()

    def execute_plan(
        self, plan: AnalysisPlan, run_id: str, session_id: str, trace: list, deadline: float
    ) -> AnalysisResponse:
        with self.graph.budget(deadline):
            return self._execute_plan(plan, run_id, session_id, trace, deadline)

    def _execute_plan(
        self, plan: AnalysisPlan, run_id: str, session_id: str, trace: list, deadline: float
    ) -> AnalysisResponse:
        datasets = {}
        client = ClinicalTrialsClient(self.settings)
        try:
            for cohort in plan.cohorts:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Run budget exhausted before acquisition")
                query = source_query(cohort.filters)
                cached = self.snapshots.cached(query, self.settings.snapshot_ttl_seconds)
                if cached:
                    snapshot, studies = cached
                else:
                    snapshot, studies = client.fetch(query, deadline)
                    self.snapshots.save(snapshot, studies)
                trace.append(
                    {
                        "stage": "source",
                        "at": utc_now(),
                        "cohort_id": cohort.id,
                        "cached": cached is not None,
                        "snapshot": snapshot.model_dump(),
                    }
                )
                if not snapshot.complete:
                    return AnalysisResponse(
                        status="incomplete",
                        run_id=run_id,
                        session_id=session_id,
                        message="Source acquisition is incomplete. Narrow the source search or retry after resolving the source error.",
                        meta={"completeness": "incomplete", "plan": plan.model_dump()},
                        provenance={
                            "source": "ClinicalTrials.gov Data API",
                            "datasets": [snapshot.model_dump()],
                        },
                    )
                self.graph.import_snapshot(snapshot, studies, deadline)
                trace.append(
                    {
                        "stage": "graph_import",
                        "at": utc_now(),
                        "dataset_id": snapshot.dataset_id,
                        "record_count": len(studies),
                    }
                )
                datasets[cohort.id] = (snapshot, studies)
            if time.monotonic() >= deadline:
                raise TimeoutError("Run budget exhausted before analysis")
            visual, meta, provenance, empty = self.engine.run(plan, datasets, run_id, deadline=deadline)
            if time.monotonic() >= deadline:
                raise TimeoutError("Run budget exhausted during analysis")
            trace.append(
                {
                    "stage": "analysis",
                    "at": utc_now(),
                    "operation": plan.operation,
                    "relationship": plan.relationship if plan.operation == "network" else None,
                    "evidence_sets": len(provenance["evidence"]),
                    "validated": True,
                }
            )
            return AnalysisResponse(
                status="no_data" if empty else "ok",
                run_id=run_id,
                session_id=session_id,
                visualization=None if empty else visual,
                message="No data for the requested visualization in the complete filtered dataset."
                if empty
                else "",
                meta={**meta, "plan": plan.model_dump()},
                provenance=provenance,
            )
        finally:
            client.close()
