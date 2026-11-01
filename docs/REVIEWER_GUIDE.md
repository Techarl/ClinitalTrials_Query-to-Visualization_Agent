# TrialScope — reviewer guide

TrialScope turns natural-language questions about registered clinical studies into structured visualizations and quoted source-based answers. The V2 backend uses a bounded model–tool observation loop and a React/ECharts workspace. The model defines typed goals and chooses tools; fixed code and dataset-scoped Neo4j queries calculate chart values. Immutable ClinicalTrials.gov snapshots preserve supporting records. Neo4j is a projection of retrieved populations, not a copy of the whole registry.

## Start

Copy `.env.example` to `.env` only if it does not already exist. Configure your own `OPENAI_API_KEY` and `NEO4J_PASSWORD`, and confirm access to the configured `OPENAI_MODEL`. Keep optional experiment flags false.

```sh
docker compose up --build -d
```

Open <http://127.0.0.1:8000/>. The Docker build includes the frontend. For native installation, follow [the README](../README.md) and [VNext guide](VNEXT.md), including `npm --prefix frontend ci` and `npm --prefix frontend run build`. This is a single-user local/self-hosted service with one execution worker. The optional Vite development server proxies to API port 8001; ordinary built-frontend use runs on port 8000.

## Inspect the implementation

`POST /v2/runs` accepts a question and optional structured constraints, returning run/session identifiers. `GET /v2/runs/{id}` returns status and result artifacts; its events endpoint streams and replays progress. A result binds chart data to scope, metric, encoding, coverage, advisories, dependencies and evidence IDs. Contracts are available at `/docs`, `/openapi.json` and [the schema directory](schemas/). `/analyze` remains the compatible V1 endpoint.

The Agent reads tool observations before deciding its next action. A dependent analysis can discover sponsors in one query and use their returned identities to form the next comparison. Follow-ups use an explicit parent result. Tools enforce scope, budgets and evidence; the model does not generate executable Cypher, Python or chart counts. Source pages and relevant observations enter bounded context, while entire statistical populations remain outside the prompt.

## Inspect evidence and interpretation

Click a bar, point, node or edge to inspect all contributing study IDs and original field values. The distinctions below matter:

- Country counts overlap when a study has locations in multiple countries.
- Shared drugs do not imply sponsor collaboration; study-level co-occurrence does not imply same-arm treatment.
- Monthly dates and duration require sufficient source precision. Exclusions are reported rather than imputed.
- Explicit official-count mode has official count receipts and sampled citations, separately labelled from complete-contributor evidence.
- Qualitative answers quote text actually read. Exact quotation verification establishes source integrity; it does not independently prove the model's interpretation.

The [UI acceptance record](UI_REVIEW_FIXES.md) shows current layouts and records which interactions were tested. [Four curated V2 examples](../examples/v2/README.md) contain five recorded real model turns, complete evidence and generation-version identities: phases, a two-turn annual-axis follow-up, a medicine comparison and duration scatter. They must not be confused with zero-model manual tool acceptance. The historical dependent-query output is preserved separately as a failed diagnostic because it omitted three zero-year buckets. The five original [V1 examples](../examples/) remain unchanged historical records.

## Verify without paid calls

To explore the preserved charts interactively without keys or Neo4j, build the frontend and run `python -m uvicorn scripts.view_recorded_examples:create_app --factory --host 127.0.0.1 --port 8013`. Open <http://127.0.0.1:8013/>. This separate read-only viewer verifies the recordings first, displays them in the actual UI, and exposes complete evidence. It has no execution endpoints. See [the demo guide](DEMO.md) for setup and the distinction from a live run.

```sh
python -m app.verify_examples
python -m scripts.verify_v2_examples
python -m pytest -q
```

The first two commands verify the V1 and V2 exports from saved raw records without a model, database or network. The third runs deterministic checks; graph tests need a configured Neo4j instance and `--live-neo4j`. External-data tests may skip when optional benchmark inputs are absent. Report passed, skipped and expected failures separately; a green CI indicator alone is not a complete quality claim.

## Research and limitations

Use [the public evaluation index](PUBLIC_EVALUATION.md) to distinguish NLI4CT evidence selection, TrialQA reading/retrieval, authored Core300 system tasks and Harness transfer. These measure different things and are not pooled into one success rate. Protocols are plans; final reports must show executed populations, failures, source/code identities and all attempted-call costs. Core300's additional dependency-linkage audit is complete and changed no original episode grades.

The small historical Jev study did not establish an advantage over every cheap-model or scope-rule control. The controlled latency probe shows that connection reuse affects observed latency; it is not a quality comparison or a general serving SLA. Harness edits bounded procedures and context/experience policies. It does not autonomously rewrite LangGraph topology. Reading or applying a learned component demonstrates a mechanism, not a causal improvement; the default service does not continuously learn from experimental banks.

Codex assisted with implementation, tests and iteration. Architecture choices, evidence, negative findings and unresolved limits are recorded. Model access, upstream availability, supported operations and semantic interpretation remain practical limits. See [release source validation](release/source-validation.json). The packaged `BUILD-MANIFEST.json` identifies the exact source commit; the independently generated ZIP verification/install receipt accompanies the delivered archive.
