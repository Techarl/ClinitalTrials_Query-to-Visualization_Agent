# Design decisions and practical limits

## Responsibilities

FastAPI serves contracts. OpenAI Responses produces constrained plans. LangGraph manages planning, validation, bounded repair and execution. Neo4j runs scoped group/relationship queries, including sponsor → trial → intervention ← trial ← sponsor. Python handles normalization, numerical bins, charts and source checks. Each calculation has one production implementation.

These are modules in one backend. Neo4j is a derived projection, not a replacement for ClinicalTrials.gov or a full-site mirror. No arbitrary Cypher/Python execution route is exposed.

## Acquisition and snapshots

Condition/intervention/lead-sponsor terms are literal source phrases. The official search result, including synonym behavior, defines the population. Secondary exact filters run on complete snapshots. Exact-query caches use a 24-hour default TTL; narrowing secondary filters is safe, while changed source queries need their own full collection. There is no speculative cache-containment inference.

The client obtains a total, follows opaque tokens including empty pages, deduplicates NCT IDs, checks the final unique count and compares before/after timestamps. Conflicting duplicate bodies are incomplete. Source searches above 5,000 records are refused instead of sampled. Failures and limits produce incomplete manifests without charts.

These checks establish a reproducible local collection, not an upstream transactional snapshot guarantee. Undisclosed source drift is still possible. Both version observations and acquisition times are retained; caches are as of their recorded fetch.

Files are exclusively created under a new dataset ID and never overwritten by application logic. Canonical JSON has a SHA256 hash checked on load. Graph trial identity is dataset_id plus nct_id. Imports are idempotent and marked ready only after count validation.

Entity names use conservative case/whitespace normalization. There is no authoritative terminology service or automatic merging of similar organizations. Raw aliases are preserved. Chinese/common-name interpretation is a model step and can be wrong; uncertain identities should trigger clarification. Original input and interpreted source terms remain auditable. Official search matches are not claimed as exact ontology-level identities.

## Calculations and evidence

Pydantic and fixed semantic checks validate plans. Operations map to allowlisted Cypher fragments; values become parameters. Every supporting trial variable, including both sides of a multi-hop path, is dataset-scoped and filtered. Drug networks include DRUG and BIOLOGICAL.

Distinct NCT IDs prevent multi-valued expansion from inflating counts. Combined phases remain combined; category totals can overlap. Missingness stays missing, zero enrollment stays zero. Date/enrollment types are disclosed. Overall recruiting does not imply every listed site is recruiting.

Every displayed datum links to its full contributors. Citations retain exact fields, JSON Pointers, source links and dataset IDs; missing paths are explicit. Graph hashes are checked against raw snapshots. Preview and full evidence are separate. Display clipping occurs after analysis and is distinct from acquisition limits.

Shared-drug sponsor weights count common intervention entities. Supporting studies are a different count and cover both sponsors. Nodes count only their role-specific contributors to displayed edges. These associations do not prove collaboration, ownership, efficacy or same-arm use.

## Context, state and recovery

Context contains a compact catalog, current request, relevant structured state and bounded repair feedback. Raw documents and unbounded chat history stay outside prompts. A hard character budget fails explicitly rather than dropping constraints. Prompt, field catalog, harness and code hashes are recorded.

New topics use complete plans. Explicit follow-ups use patches: omitted fields preserve and clear_fields removes. Current explicit constraints are checked against the interpreted plan. Pending clarifications retain their constraints but do not replace successful state; failed/incomplete runs also leave successful state intact.

SQLite state and LangGraph checkpoints are separate. session_id identifies the conversation; run_id identifies the attempt and equals its graph thread ID. A local lock serializes sessions, with version checks on commits. Use one worker; this is not distributed orchestration.

Default budget is two main-model attempts with SDK retries off. Source retries happen below the model layer. A cooperative deadline and request timeouts bound work; graph queries have a 30-second server timeout. Recovery retains the original deadline and pre-recorded attempts. External billing cannot be guaranteed exactly-once after a crash; unknown usage is explicitly unknown.

## Reserved extensions

- GenerationClient separates structured generation from provider details.
- DecisionClient reserves a typed decision API separate from generation.
- DecisionPolicy can change execute/clarify/unsupported before fixed validation; baseline keeps the planner action without an extra call.
- HarnessProvider/HarnessConfig supply versioned instructions/context policy. They cannot expand the global context ceiling or replace data/evidence validation.
- ContextBuilder assembles bounded stage context.
- ExperienceStore is disabled: no cross-task learning or experience writes.
- TraceSink records structured events for audit and later evaluation.

No experimental adapter, optimizer, self-modification process or decision-model call is shipped. Both experiment flags stay false. No silent fallback model is used.

## Validation and remaining limits

The deterministic path was exercised before model integration. Five actual model runs with full evidence/raw sources are frozen. The independent checker does not import production normalization, aggregation or graph queries. Both implementations were AI-assisted and may share conceptual assumptions.

Tests cover paging, integrity, fixture isolation, counts, missingness, networks, evidence, follow-ups, topic changes, conflicts, bounded repair, HTTP validation and interrupted recovery. A real Chinese clarification-and-answer flow also passed.

Limits: bounded source searches; no alternative-date filtering, patient data, efficacy inference or unrestricted paths; imperfect semantic/entity interpretation; synchronous execution; one worker; no authentication or retention cleanup; no frontend/scatter plot; no experiments. Compose syntax was checked, but containers were not started because the engine was unavailable. Native Windows/Desktop execution was tested.

The user supplied the architecture, and Codex implemented and revised code, prompts, tests and docs. No implementation code was copied from similar take-home repositories. Improvements would prioritize terminology-backed interpretation, broader evaluation, large-data jobs, retention, authenticated deployment, container CI and controlled experiments.
