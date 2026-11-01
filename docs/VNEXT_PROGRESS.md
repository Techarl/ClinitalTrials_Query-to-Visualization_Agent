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

Final Windows regression: 138 tests passed with real Neo4j (22.09 seconds). See [execution research](VNEXT_RESEARCH.md), [public-project parity](VNEXT_PARITY.md) and [verification record](vnext/validation.json).

| ID | Capability | Status |
|---|---|---|
| A01 | Typed tasks/actions/results | implemented and verified |
| A02 | Sourced entity candidates and same-drug comparisons | implemented and verified |
| A03 | Full acquisition / explicit official count overview | implemented and verified; details below |
| A04 | Versioned chart advisories | implemented and verified |
| A05 | Observation-driven tool loop | implemented and verified |
| A06 | Conversation, branches, run recovery | implemented and verified |
| A07 | Durable SSE and reconnection | implemented and verified |
| A08 | Charts, scatter and country map | implemented and verified |
| A09 | Date precision, fields, monthly grain, duration | implemented and verified |
| A10 | Result-dependent cohorts and multi-artifact analysis | implemented and verified |
| A11 | React/TypeScript/ECharts | implemented and verified |
| A12 | Click-through evidence and equivalent renderers | implemented and verified |
| A13 | History, revocable read-only snapshots, exports | implemented and verified |
| A14 | Actual PubMed search and abstracts | implemented and verified |
| A15 | Shared MCP tools, stdio and Streamable HTTP | implemented and verified |
| A16 | Challenge set, traces, usage and browser acceptance | implemented and verified; details below |
| B01 | Atomic decision judgments, repair and cascade study | implemented and verified; details below |
| B02 | Execution-based Harness and deduplicated experience | implemented and verified; details below |

Official implementation references: [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [LangGraph agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents), [CTgov API](https://clinicaltrials.gov/data-api/api), [MCP](https://modelcontextprotocol.io/docs/develop/build-server). Public comparison projects are design references; their implementation code is not copied.

## Verified checkpoints (2026-11-01)

- Full regression: `pytest --live-neo4j -q`: **138 passed** (22.09 s).
- Native model + real Neo4j + frozen real CTgov data: dependent sponsor ranking and annual trends, completed, 8 model calls, estimated $0.1050224. First failed coverage probe remains in the runtime logs.
- Native scatter/duration analysis: completed, 6 model calls, estimated $0.0390035.
- Official MCP clients: stdio and Streamable HTTP each retrieved 400 trial records, phase counts, full contributor evidence, and PubMed PMIDs 38246194 / 39288737; no planner calls.
- React/TypeScript production build passes. Actual browser verified phase result, data table, field-level evidence, refresh recovery and read-only share. Browser testing exposed and fixed ECharts cleanup removing the table and a missing private field crashing the public share page.
- [Actual read-only page screenshot](vnext/screenshots/readonly-phase.png).

## Completed acceptance and research

- Full acquisition: [5,309 asthma trials in four disjoint partitions](vnext/wide-acquisition.json). Exact official counts and incomplete contributor samples remain visibly distinct.
- [Native HTTP](vnext/http-acceptance.json) and [isolated Linux Docker](vnext/docker-acceptance.json) acceptance use actual Neo4j and verify full contributor evidence.
- Browser checks cover map/scatter/node clicks, edge evidence through the accessible table, refresh, mobile layout, share creation and revocation. Direct thin-edge pixel clicking was not independently confirmed.
- A real empty search led to sourced alias clarification. Confirmation exposed an identity dependency omission: fixed by linking matching sourced identities, exposing prior artifact summaries and validating the full dependency closure. Unrelated identities and independent charts still fail dependency validation. Successful historical continuation `vr_cafd8a7ebec6472b9e7eda8be169da35` contains the sourced pembrolizumab identity and 400 melanoma trials; earlier failed runs remain available. [Screenshot](vnext/screenshots/confirmed-identity-completed.png).
- The final authored challenge is 72/72; later browser defects have separate regression evidence. This is not a broad real-world accuracy estimate.
- Atomic Jev, actual system repair, conditional cascade and execution Harness studies are complete. Cheap cascade activation was withheld after failed calibration. Harness produced zero adopted positive components. Full costs, limits and trace archives are in [VNEXT_RESEARCH.md](VNEXT_RESEARCH.md).
- Five historical examples retain independent verification of 33,824 raw fields.

The separately requested Jev passage-selection study follows this checkpoint. The earlier entity-name retrieval experiment does not establish its effectiveness.
