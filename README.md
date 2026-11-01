# ClinicalTrials Query-to-Visualization Agent

A local analysis workspace that turns clinical-study questions into interactive, source-grounded visualizations. The VNext agent uses native model tool calls and a bounded LangGraph observation loop: results from one tool can determine the next query and its cohorts. Fixed code and dataset-scoped Neo4j queries calculate counts and evidence; the model does not generate executable Cypher, Python, counts or citations.

The React/TypeScript/ECharts workspace adds streamed progress, multiple charts, field-level citations, history, revocable read-only sharing and exports. V2 supports sourced entity aliases, monthly date fields, duration scatter plots, country maps, official-count overviews with explicitly limited sample evidence, PubMed abstracts, and actual MCP stdio/HTTP transports. The original `/analyze` contract remains compatible. Research experiments are separate opt-in commands; they do not alter the default service.

Start with the [VNext guide](docs/VNEXT.md), [acceptance matrix](docs/VNEXT_PROGRESS.md), and [real smoke evidence](docs/vnext/smoke_summary.json). The baseline five examples and historical research reports below remain unchanged; VNext evaluation is reported separately.

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

OpenRouter is optional and is never called by the default application. Both API experiment flags remain false; `/analyze` retains baseline-v1 and `/v2/runs` uses the observation-driven tool loop. Historical research commands are described in [the experiment protocol and results](docs/EXPERIMENTS.md). Main-model effort defaults to `medium`; changing to `high` is explicit and recorded.

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
| [Drug comparison](examples/03_comparison/response.json) | Grouped bar | Asthma: 60 dupilumab / 116 omalizumab source records; overlap disclosed |
| [Enrollment](examples/04_enrollment/response.json) | Histogram | Psoriasis/secukinumab: 149 source records; missing enrollment excluded |
| [Shared-drug sponsor links](examples/05_shared_drugs/response.json) | Network | Rheumatoid arthritis/adalimumab: 242 source records; distinct shared interventions |

These are frozen observations, not permanent live counts. Each includes the request, response, full evidence and trace. Raw source collections are under `examples/snapshots/`. All five retain their frozen `5344202` generation source and original runtime hash. The current `118a0e5` engine recomputed every visualization, statistical metadata field, provenance object and full evidence collection from the frozen JSON through real Neo4j; all were unchanged. See [current replay](docs/ROUND3_EXAMPLE_REPLAY.json). Older snapshots remain as immutable historical artifacts.

```powershell
# Offline: no database, network or model calls:
.\.venv\Scripts\python.exe -m app.verify_examples
```

Histogram assignment uses one exact-rational boundary sequence rounded once; the independent checker enumerates Decimal-derived intervals and also checks each contributor against the actual serialized interval, with exactly-once coverage per cohort.

The independent checker reads raw sources without production normalization, aggregation or Cypher compilation. It independently generates the complete expected rows/edges/nodes, including top-N order, zero buckets and role-specific contributors, and verifies hashes and 33,824 exact field values. Twenty-six schema-valid corruptions must fail its negative tests. See [verification report](examples/verification.json). Both implementations are AI-assisted and may share conceptual assumptions; semantic review remains valuable.

## Validation

The [Round 2 review fixes](docs/ROUND3_REVIEW_RESPONSE.zh-CN.md) close F1–F4: stable histogram intervals plus direct contributor invariants, current-explicit clarification scope, durable model-attempt/outcome accounting, and budget reservations for unknown usage. The current [validation report](docs/ROUND3_VALIDATION.json) records 109 tests with real Neo4j in both native and Linux environments. Historical research results remain frozen.

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

The [round-one review response](docs/ROUND2_REVIEW_RESPONSE.zh-CN.md) maps R1–R9 to fixes and regressions. The frozen [semantic cases](docs/evals/semantic_cases.json) cover 48 cases / 50 actual main-model turns across diseases, English/Chinese, AND/OR/comparison, follow-ups and repeated clarification. The frozen planner-v2 source scored 49/50, including 37/37 executable plans and 9/9 clarification turns. The difference is a full-site request that safely asked for narrower scope instead of returning unsupported. Across three passes the scores were 49/50, 50/50 and 49/50; all observations are retained. This small engineer-authored set is not a population accuracy guarantee. Expected answers never enter planner input. This measures query-to-plan semantics, independently of numerical verification.

```powershell
# No paid calls: validate the case file:
.\.venv\Scripts\python.exe -m app.semantic_eval
# Explicitly paid, isolated model evaluation; no CTgov/Neo4j calls:
.\.venv\Scripts\python.exe -m app.semantic_eval --live
```

See the preserved [semantic report](docs/evals/semantic_report.json). This is the prior planner-v2 observation, not a new planner-v3 benchmark. The current fix was checked with [targeted live scope correction](docs/ROUND3_LIVE_SCOPE.json). GitHub CI runs deterministic checks and real Neo4j fixtures with synthetic test credentials, without model keys or paid calls.

The authorized research pilots are kept outside `app/`: a real Jev decision gate with an extra-GPT control, source-grounded BM25/dense/RRF candidate retrieval, and a restricted Harness² PPC/experience pilot. The [research report](docs/EXPERIMENTS.md) records the protocols, costs and limitations, including negative results. They do not change the default service.

## Docker and submission

```powershell
docker compose config --quiet
docker compose up --build -d
```

Compose uses Community Neo4j 2026.10.1, persistent volumes and one worker. API binds to loopback 8000; Neo4j host ports 7475/7688 avoid Desktop. The internal API URI is `bolt://neo4j:7687`. Stop another API on 8000 before using this alternative. An existing Neo4j volume retains its original password.

Current Docker verification passed: API image build, both service health checks, all 109 tests inside Linux, and a real two-turn scope correction producing 400/396 comparison cohorts. See [current validation](docs/ROUND3_VALIDATION.json). Native Desktop/Python execution also passed 109 tests. The [prior restart smoke](docs/DOCKER_VALIDATION.json), generated from `5344202`, separately records a 400-study analysis, API restart and 27-study Phase 3 follow-up with evidence paging, snapshot reuse and completed-run idempotency.

The historical [fresh-volume Compose check](docs/CLEAN_DOCKER_VALIDATION.json) also passed the real analysis/restart flow and was removed afterward; its optional port override is [scripts/compose.clean.yaml](scripts/compose.clean.yaml). The [current ZIP check](docs/ROUND3_PACKAGE_VALIDATION.json) extracted the package, installed its code into the previously verified environment with an identical dependency lock, passed 101 offline tests (8 database tests skipped), verified all five exports and passed `pip check`, without credentials or paid calls. The original [fresh-environment installation check](docs/ZIP_VALIDATION.json) is retained with its original source and test counts.

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

Limits include bounded source searches, imperfect semantic/entity interpretation, synchronous execution, one worker, no authentication or retention cleanup, and no frontend/scatter plot. Improvements would prioritize terminology-backed interpretation, independently annotated evaluation, large-dataset jobs and authenticated deployment. Experimental configurations remain isolated; no automatic improvement is claimed.
