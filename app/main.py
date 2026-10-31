"""Local HTTP API. Heavy dependencies initialize on first analysis, not on liveness."""

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app import __version__
from app.agent import AgentRunner
from app.config import Settings
from app.models import AnalysisPlan, AnalysisRequest, AnalysisResponse
from app.service import AnalysisService
from app.sources import SnapshotStore
from app.storage import StateConflict, StateStore


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application):
        yield
        if application.state.runner is not None:
            application.state.runner.close()
        if application.state.service is not None:
            application.state.service.close()

    application = FastAPI(
        title="ClinicalTrials Query-to-Visualization Agent",
        version=__version__,
        description="ClinicalTrials.gov analysis with structured charts, source snapshots, and contributor evidence.",
        lifespan=lifespan,
    )
    application.state.settings = configuration
    application.state.service = None
    application.state.runner = None
    application.state.initialization_lock = threading.Lock()
    store = StateStore(configuration.data_directory)

    def runner() -> AgentRunner:
        with application.state.initialization_lock:
            if application.state.runner is None:
                try:
                    application.state.service = AnalysisService(configuration)
                    application.state.runner = AgentRunner(configuration, application.state.service)
                except Exception as error:
                    if application.state.service is not None:
                        application.state.service.close()
                        application.state.service = None
                    raise HTTPException(
                        503, f"Dependency initialization failed ({type(error).__name__})."
                    ) from error
        return application.state.runner

    def analysis_result(response: AnalysisResponse):
        return JSONResponse(
            status_code=502 if response.status == "failed" else 200, content=response.model_dump(mode="json")
        )

    @application.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        # Liveness does not make database or paid provider calls.
        return {"status": "ok", "version": __version__}

    @application.exception_handler(StateConflict)
    def conflict_handler(_, error: StateConflict):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.post("/analyze", response_model=AnalysisResponse, tags=["analysis"])
    def analyze(request: AnalysisRequest):
        return analysis_result(runner().analyze(request))

    @application.get("/runs/{run_id}", tags=["runs"])
    def get_run(run_id: str):
        run = store.run(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run

    @application.post("/runs/{run_id}/resume", response_model=AnalysisResponse, tags=["runs"])
    def resume_run(run_id: str):
        try:
            return analysis_result(runner().resume(run_id))
        except KeyError as error:
            raise HTTPException(404, "Run not found") from error

    @application.get("/evidence/{evidence_id}", tags=["evidence"])
    def evidence(
        evidence_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=500)
    ):
        page = store.evidence_page(evidence_id, offset, limit)
        if page is None:
            raise HTTPException(404, "Evidence set not found")
        return page

    @application.get("/datasets/{dataset_id}", tags=["sources"])
    def dataset(dataset_id: str):
        try:
            snapshot, _ = SnapshotStore(configuration.data_directory).load(dataset_id)
            return snapshot.model_dump()
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(404, "Dataset not found or failed integrity verification") from error

    @application.get("/datasets/{dataset_id}/studies/{nct_id}", tags=["sources"])
    def study(dataset_id: str, nct_id: str):
        try:
            _, studies = SnapshotStore(configuration.data_directory).load(dataset_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(404, "Dataset not found or failed integrity verification") from error
        for raw in studies:
            if raw["protocolSection"]["identificationModule"]["nctId"] == nct_id:
                return raw
        raise HTTPException(404, "Study not found in this snapshot")

    @application.get("/capabilities", tags=["analysis"])
    def capabilities():
        return {
            "plan_schema": AnalysisPlan.model_json_schema(),
            "experiments": {"decision": False, "harness": False},
            "scope": "single-user local service; source search semantics are documented in response metadata",
        }

    return application
