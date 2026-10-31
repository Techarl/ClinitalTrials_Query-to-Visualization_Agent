# ClinicalTrials Query-to-Visualization Agent

A backend that turns clinical-study questions into structured visualization specifications grounded in the ClinicalTrials.gov API. GPT-6.1 Sol proposes a typed plan; fixed code and dataset-scoped Neo4j queries calculate the answer. The model does not generate executable Cypher, Python, counts or citations.

The baseline supports clarification, persistent follow-ups, categorical/geographic counts, yearly trends, cohort comparisons, enrollment histograms, sponsor–drug networks, study-level drug co-occurrence and sponsor links through shared interventions. Decision-model and Harness experiments remain disabled.

## Local setup

Requirements: Python 3.12+, Neo4j, and an OpenAI Platform key with access to `gpt-6.1-sol` through Responses. Native verification used Windows, Python 3.12.4 and Neo4j Desktop / Neo4j 2026.10.1. Dependencies are pinned.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
# Only if .env does not exist:
Copy-Item .env.example .env
```

Fill `OPENAI_API_KEY` and `NEO4J_PASSWORD` in `.env`. On the prepared machine, keep Desktop and `clinicaltrials-dev` running; URI is `bolt://127.0.0.1:7687`, user/database `neo4j`, and the password is already stored locally. Environment variables override the file. Real credentials must not enter `.env.example`.

OpenRouter is optional and is never called by the baseline. Both experiment flags must remain false; true is rejected because experimental adapters are not implemented. Main-model effort defaults to `medium`; changing to `high` is explicit and recorded.

```powershell
# Read-only diagnostics; no generation or experimental calls:
.\.venv\Scripts\python.exe -m app.diagnostics
# Start one API worker:
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open [interactive API docs](http://127.0.0.1:8000/docs). `/health` is liveness and makes no downstream calls. This is a single-user local service; authentication and multi-tenant deployment are outside this submission.

## Use

POST `/analyze`; only `query` is required:

```json
{"query": "How are melanoma studies involving pembrolizumab distributed across phases?"}
```

For follow-ups, send the returned `session_id` and `run_id` as `parent_run_id` with a question such as “Only Phase 3, then show studies starting each year from 2015 through 2025.” New topics do not inherit prior filters.

See the [complete API/rendering contract](docs/API.md). `/openapi.json` and `/capabilities` expose machine-readable schemas. Chinese questions and `language=zh` are supported.

```powershell
.\.venv\Scripts\python.exe -m app.cli "Show the phase distribution of melanoma studies involving pembrolizumab." --output data/result.json
```

The CLI uses the same planner/execution path and exports full evidence beside the response.

To exercise the deterministic pipeline with a handwritten plan and no model calls, run `python -m app.handwritten_demo` using the project interpreter. This still requires Neo4j and a live ClinicalTrials.gov connection or an existing complete cache.

## Design and tradeoffs

- Bounded context contains the current request, structured session state, field rules and repair feedback. Raw study JSON stays outside prompts.
- Responses structured output produces a plan, patch or clarification. Explicit-filter conflicts require clarification. Default budget is two model attempts, with SDK retries off.
- Literal source terms define an official search universe, including source synonym behavior. Full pagination, distinct IDs, source totals and before/after timestamps are checked. Incomplete acquisition produces no chart.
- Immutable hashed raw snapshots are authoritative; Neo4j is a derived projection. Trial identity includes dataset and NCT ID. Every supporting record in a relation path is dataset-scoped.
- Fixed queries calculate contributors, while Python performs bins and chart assembly. Displayed items have complete contributor evidence with exact original values and JSON Pointers.
- SQLite stores successful state, pending clarification, runs and evidence; LangGraph stores per-run checkpoints. Recovery retains original deadlines and attempted-call accounting.

See [design details and limitations](docs/DESIGN.md). Presentation clipping is distinct from source truncation. Tool errors never become zero studies. There is no arbitrary query/code execution or silent model fallback.

## Five actual runs

Each saved run used one real planner call:

| Example | Chart | Recorded scope |
| --- | --- | --- |
| [Phases](examples/01_phases/response.json) | Bar | 400 source studies |
| [Phase 3 follow-up](examples/02_followup_trend/response.json) | Time series | Same cohort, Phase 3, start years 2015–2025; 27 studies |
| [Drug comparison](examples/03_comparison/response.json) | Grouped bar | 400 pembrolizumab / 396 nivolumab source records; overlap disclosed |
| [Enrollment](examples/04_enrollment/response.json) | Histogram | Missing enrollment excluded; actual/estimated types retained |
| [Shared-drug sponsor links](examples/05_shared_drugs/response.json) | Network | Distinct shared interventions and all supporting studies |

These are frozen observations, not permanent live counts. Each includes the request, response, full evidence and trace. Raw source collections are under `examples/snapshots/`. Examples were generated from milestone `917f25a`, with runtime source hashes recorded; later recovery/interface hardening preserves the demonstrated calculations.

```powershell
# Offline: no database, network or model calls:
.\.venv\Scripts\python.exe -m app.verify_examples
```

The independent checker reads raw sources without production normalization, aggregation or Cypher compilation. It verifies demonstrated cohort membership, plotted values, edge weights, contributors, hashes and 71,323 exact field values. See [verification report](examples/verification.json). Both implementations are AI-assisted and may share conceptual assumptions; semantic review remains valuable.

## Validation

```powershell
# No paid calls; graph fixtures skipped:
.\.venv\Scripts\python.exe -m pytest -q
# Include isolated temporary Neo4j fixtures:
.\.venv\Scripts\python.exe -m pytest -q --live-neo4j
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m pip check
```

Tests cover pagination, empty pages, deduplication, completeness, tampering, multi-valued data, BIOLOGICAL interventions, missingness, snapshot isolation, evidence, follow-ups, topic changes, conflicts, bounded repair, recovery and HTTP validation. Fixtures are synthetic, not represented as real source results. A real Chinese clarification-and-answer flow was also tested.

`python -m app.examples` intentionally regenerates live runs and makes paid main-model calls. It is separate from offline verification.

## Docker and submission

```powershell
docker compose config --quiet
docker compose up --build -d
```

Compose uses Community Neo4j 2026.10.1, persistent volumes and one worker. API binds to loopback 8000; Neo4j host ports 7475/7688 avoid Desktop. The internal API URI is `bolt://neo4j:7687`. Stop another API on 8000 before using this alternative. An existing Neo4j volume retains its original password.

Docker Desktop 25.0.3 verification passed: API image build, both service health checks, the same 29 regression tests inside Linux, and a real 400-study analysis followed by an API restart and a 27-study Phase 3 follow-up. Source evidence, paging, snapshot reuse and completed-run idempotency were checked. See [container validation](docs/DOCKER_VALIDATION.json). Native Desktop/Python execution was also exercised.

To intentionally repeat the real two-call container smoke test (paid main-model calls):

```powershell
.\.venv\Scripts\python.exe scripts/smoke_docker.py --restart-api
```

If BuildKit cannot fetch a Docker Hub token, pull `python:3.12-slim` directly with `docker pull python:3.12-slim`, then retry the build. This worked around the registry-token timeout observed during validation. Changes to `.env` require `docker compose up -d api` to recreate the API with its new environment.

```powershell
.\.venv\Scripts\python.exe scripts/package_submission.py
```

This builds `dist/cheiron-clinicaltrials-agent.zip` with an explicit allowlist and credential scan. Source, tests, docs, examples and evidence are included. `.env`, local databases, downloads, environments and Git history are excluded.

## AI use and improvements

The user supplied the assignment and architecture handoff. OpenAI Codex drafted, implemented, tested and revised code, prompts, tests, reference checks and docs. Deliberate choices include constrained plans, immutable snapshots, fixed calculations, scoped relationships, full evidence and bounded recovery. No code from the similar take-home repositories in the handoff was copied.

Limits include bounded source searches, imperfect semantic/entity interpretation, synchronous execution, one worker, no authentication or retention cleanup, no frontend/scatter plot and no experiments. Improvements would prioritize terminology-backed interpretation, broader semantic evaluation, large-dataset jobs, authenticated deployment, container CI and separately controlled experiments. No experimental benefit is claimed.
