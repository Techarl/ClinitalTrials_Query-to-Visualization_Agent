# VNext implementation record

Baseline: `dab1921` (Round 2 fixes, 109 tests). Scope: [accepted specification](../question/Cheiron_VNext_Implementation_Spec.md).

## Design decisions

- Keep v1 contracts and calculations compatible; add `app/v2` with typed tools and versioned artifacts.
- A shared tool registry owns validation, session-scoped handles, journals and budgets. HTTP, LangGraph, CLI, MCP and research use this registry.
- Tool observations drive subsequent native model function calls. Public events contain actions and observations, never hidden reasoning.
- Keep immutable root snapshots and explicit subset lineage. Rankings select entities from actual result handles, never model-invented cohort names.
- Persist tool and model attempts before execution, outcomes before checkpoint advancement. Replay completed inputs; unknown external attempts remain visible.
- Separate official exact counts from complete contributor evidence; no top-k retrieval defines a statistical population.

## Acceptance matrix

The core implementation passes 130 tests with real Neo4j. Acceptance is incremental; pending research is not a completion claim.

| ID | Capability | Status |
|---|---|---|
| A01 | Typed tasks/actions/results | implemented; acceptance in progress |
| A02 | Sourced entity candidates and same-drug comparisons | implemented; acceptance in progress |
| A03 | Full acquisition / explicit official count overview | pending |
| A04 | Versioned chart advisories | implemented; acceptance in progress |
| A05 | Observation-driven tool loop | implemented; acceptance in progress |
| A06 | Conversation, branches, run recovery | implemented; acceptance in progress |
| A07 | Durable SSE and reconnection | implemented; acceptance in progress |
| A08 | Charts, scatter and country map | implemented; acceptance in progress |
| A09 | Date precision, fields, monthly grain, duration | implemented; acceptance in progress |
| A10 | Result-dependent cohorts and multi-artifact analysis | implemented; acceptance in progress |
| A11 | React/TypeScript/ECharts | implemented; acceptance in progress |
| A12 | Click-through evidence and equivalent renderers | implemented; acceptance in progress |
| A13 | History, revocable read-only snapshots, exports | implemented; acceptance in progress |
| A14 | Actual PubMed search and abstracts | implemented; acceptance in progress |
| A15 | Shared MCP tools, stdio and Streamable HTTP | implemented; acceptance in progress |
| A16 | Challenge set, traces, usage and browser acceptance | pending |
| B01 | Atomic decision judgments, repair and cascade study | pending |
| B02 | Execution-based Harness and deduplicated experience | pending |

Official implementation references: [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [LangGraph agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents), [CTgov API](https://clinicaltrials.gov/data-api/api), [MCP](https://modelcontextprotocol.io/docs/develop/build-server). Public comparison projects are design references; their implementation code is not copied.

## Verified checkpoints (2026-11-01)

- Full regression: `pytest --live-neo4j -q`: **130 passed** (30.74 s).
- Native model + real Neo4j + frozen real CTgov data: dependent sponsor ranking and annual trends, completed, 8 model calls, estimated $0.1050224. First failed coverage probe remains in the runtime logs.
- Native scatter/duration analysis: completed, 6 model calls, estimated $0.0390035.
- Official MCP clients: stdio and Streamable HTTP each retrieved 400 trial records, phase counts, full contributor evidence, and PubMed PMIDs 38246194 / 39288737; no planner calls.
- React/TypeScript production build passes. Actual browser verified phase result, data table, field-level evidence, refresh recovery and read-only share. Browser testing exposed and fixed ECharts cleanup removing the table and a missing private field crashing the public share page.
- [Actual read-only page screenshot](vnext/screenshots/readonly-phase.png).

Remaining: broader live feature acceptance, challenge evaluation, second-stage decision/Harness research, container and final delivery documentation.
