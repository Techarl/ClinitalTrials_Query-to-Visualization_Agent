# Capability mapping to the three selected references

This is an implementation acceptance map against the capabilities the user selected, not a ranking of candidates or a claim that these repositories are equivalent in every detail. Public projects were design references; their implementation code was not copied.

| Reference emphasis | TrialScope implementation | Reviewable evidence |
|---|---|---|
| [takehome-surgical](https://github.com/yipjunkai/takehome-surgical): entities, scope, structured planning, chart advisories | Typed goals and filters, sourced name candidates, alias confirmation, full pagination/partitions, explicit official-count mode, rule-based chart notices | `app/v2/contracts.py`, `entities.py`, `acquisition.py`, `overview.py`, `charts.py`; [acceptance matrix](VNEXT_PROGRESS.md) |
| [Query-to-Visualization](https://github.com/bibekyess/Query-to-Visualization): real tool loop, streaming, dates/scatter, PubMed/MCP | Native function actions in LangGraph choose–execute–observe; bounded continuation; durable SSE; four date axes/month grain/scatter; actual NCBI and MCP clients | [72 execution episodes and complete traces](vnext/results/evaluation-final.zip), [MCP report](vnext/smoke_summary.json) |
| [qtov](https://github.com/JoelKShaju/qtov): React/ECharts, explanation, citations, saved/shareable results | React/TypeScript/ECharts, multi-artifact workspace, geographic renderer, point/node/edge citation drawer, semantic-equivalent table/map switching, history, revocable read-only snapshots, JSON/CSV/evidence/PNG exports | [real browser screenshots](vnext/screenshots), [workspace/API guide](VNEXT.md) |

The project also retains its own emphasis: independently recomputed complete item sets, exact raw-field citations, explicit statistical population versus citation coverage, immutable snapshot lineage, real Neo4j result-dependent cohorts, and honest negative research results. These are demonstrated guarantees within the tested scope, not a claim of production readiness or superiority over other candidates.

Deliberate limits: a small sourced drug vocabulary rather than a comprehensive terminology service; local single-user hosting rather than multi-tenant authentication; PubMed abstracts rather than full text; a small authored evaluation rather than a representative clinical-language benchmark; no vector top-k used as a statistical universe.
