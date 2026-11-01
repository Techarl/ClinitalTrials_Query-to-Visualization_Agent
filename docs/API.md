# API and rendering contract

Models are in `app/models.py`. `/openapi.json` exposes API schemas and `/capabilities` the plan schema. Unknown request fields are rejected. Cohorts are study collections, not patient-level cohorts.

## V2 portable text citations and evidence exports

The release backend and frontend implement the additive `text-finding-v1` metadata contract below. Newly read trial/article sources retain portable provenance. Existing historical answers use the explicit legacy fallback; they are not silently enriched or reinterpreted.

An answer artifact (`source_type: "answer"`) contains `metadata.answer`, `metadata.findings`, and limitations. The model supplies only each finding's `source_handle`, JSON Pointer `pointer`, and exact `quotation`. The server derives source metadata from the owned immutable record; model-provided URLs and provenance are not accepted.

| Finding field | Meaning |
| --- | --- |
| `provenance.version`, `resolution` | `text-finding-v1`, `exact` for the new recorded metadata |
| `provenance.source_type`, `document_id`, `title`, `url` | ClinicalTrials.gov record/NCT ID or PubMed article/PMID, source title, and official URL constructed from its identity |
| `provenance.citation_id` | Deterministic citation identity for the document version, source pointer, and quotation |
| `provenance.source_hash`, `source_hash_scope` | SHA-256 of the canonical saved readable source container, labelled `canonical_source_text_container` |
| `provenance.document_hash`, `document_pointer` | SHA-256 of the individual trial/article JSON and its position in the saved container |
| `provenance.field_value_hash`, `quotation_hash` | Separate hashes of the exact resolved field value and quotation |
| `provenance.receipt_hash`, `retrieved_at` | Optional acquisition receipt hash and saved retrieval time; null where not recorded. A PubMed receipt hash describes the acquisition XML, distinct from the readable JSON hashes |
| `provenance.hash_serialization` | `canonical_json_v1`; hashes describe saved content, not current live webpage bytes |
| `provenance.version_notice` | Explicit notice that current content at the official URL may differ from the saved version |
| `source_field.pointer`, `value_included`, `value` | Exact source pointer and field value when included; false/null when omitted |
| `source_field.exact_quoted_value`, `serialized_character_count`, `omission_reason` | Exact quotation, field size, and an explicit explanation when the full field is omitted |

Whole source containers/documents are not embedded in every finding. Other resolved fields are included only up to 32,000 serialized characters. Larger fields retain the exact quotation, length and hash with `field_exceeds_32000_characters`; whole documents use `whole_source_container`. An omitted value is not an empty source field. Exact quotation/source verification does not establish semantic entailment of the generated answer.

`GET /v2/runs/{run_id}/export?format=evidence` retains the existing chart map entries (`evidence_set_id` → complete evidence page). When answer artifacts are present, it additionally includes the reserved key `__text_evidence__`:

```json
{
  "ev_chart_example": {"citations": [], "total_contributors": 0},
  "__text_evidence__": {
    "schema": "text-evidence-v1",
    "artifacts": [{"result_id": "answer_example", "goal_id": "answer", "findings": []}]
  }
}
```

This illustration describes the container shape, not a real result. The text export's findings include the original handle/pointer/quotation plus provenance and exact field values subject to the bounds above. Consumers must treat `__text_evidence__` separately from statistical contributor sets; it is not a chart evidence ID. Chart-only exports keep the original shape. Answer-only results expose the evidence download in the UI; mixed runs label it as chart and text source evidence. CSV remains for chart data.

The normal JSON result export and read-only share result preserve the same artifact metadata. Legacy artifacts remain readable: missing provenance is not inferred from IDs mentioned in a quotation. The text evidence export marks it as `resolution: "not_recorded"` with an explicit notice. The UI shows its original quotation, pointer and internal handle, without inventing an official link. For enriched citations the UI links only an exact recognised official URL/identity combination and displays fields, hashes, and the version notice in an expandable panel.

## POST /analyze

| Field | Type | Default and validation |
| --- | --- | --- |
| query | required string | Nonempty after trimming, max 4,000 characters |
| filters | object/null | Optional explicit shared constraints |
| intervention_scope | population/comparison | Default population: every cohort retains the whole explicit intervention array. comparison permits singleton drug cohorts only for any semantics. ALL cannot be split. |
| session_id | string/null | Absent creates a session; letters/digits/underscore/hyphen, length 1–80 |
| parent_run_id | string/null | Completed analysis/clarification from the same session |
| language | en/zh | en; controls planner titles, labels, questions and explanation |

Analysis is synchronous; deploy one worker. Same-session requests are serialized and version checked. New topics use complete plans without inherited conditions. Follow-ups use typed patches: omitted fields preserve values and clear_fields removes them. Pending clarification is separate from successful state.

Clarification state retains the original request, cumulative replies, confirmed explicit filters and current questions across restarts. A historical parent restores that run's saved branch state. Successful session changes and completed results are committed atomically. Completed-run resume is idempotent.

Patches also support top_n, histogram_bins, and cohort_label (select cohort_id when relabeling a comparison). Old model assumptions are invalidated after a patch. `meta.assumptions` and `meta.analysis_scope` are derived from the resolved plan; optional fresh model comments are separate in `meta.model_assumptions`.

### Filters

| Field | Type | Meaning |
| --- | --- | --- |
| condition | string/null, max 200 | Literal official query.cond phrase |
| interventions | string array, max 10 | Nonempty search names, each at most 200 characters |
| intervention_match | any/all | Default any, source OR/AND. Explicit shared arrays apply to every cohort unless intervention_scope=comparison. Natural-language comparisons without explicit intervention filters create separate cohorts. |
| sponsor | string/null, max 200 | Lead sponsor via query.lead, not collaborator search |
| phases | enum array | EARLY_PHASE1, PHASE1, PHASE2, PHASE3, PHASE4, NA; any contained phase |
| statuses | enum array | Official study overall statuses, e.g. RECRUITING/COMPLETED |
| countries | string array, max 20 | Any exact source country; also limits country-chart categories |
| start_year/end_year | integer/null | Inclusive start-year bounds, 1800–2200; start ≤ end |
| enrollment_type | ACTUAL/ESTIMATED/null | Optional enrollment type restriction |

Omitted lists are empty and scalars null. Explicit current constraints override inherited history. A mismatch with the interpreted plan triggers clarification. Quoted/backslash-containing source names and NCT-only expressions are rejected instead of treated as executable source syntax.

Condition/intervention/sponsor use official search semantics, including synonyms. Other fields filter locally after complete acquisition. Thus phase/country/status/year filters do not reduce the initial fetch budget; narrow an overlarge source search with a more specific condition, intervention or lead sponsor.

## Response

| Field | Meaning |
| --- | --- |
| status | ok, needs_clarification, unsupported, no_data, incomplete, failed |
| run_id/session_id | Run and conversation identifiers |
| visualization | Typed specification on ok; null otherwise |
| meta | Interpreted plan, filters/counts, overlap, units, sorting, missingness, versions and usage |
| provenance | Source manifests and evidence previews linked to full collections |
| questions/message | Clarification questions or status explanation |

Completed business statuses use HTTP 200. Failed execution uses 502, dependency initialization 503, request validation 422, saved-item absence 404, and session/parent conflict 409. Failure does not mean zero studies.

`meta.cohorts` separates source and selected counts and reports missing/estimated dates and enrollment types. `cohort_overlap_counts` counts shared NCT IDs; comparison sets need not be disjoint. `presentation_clipping` describes omitted display items. Successful results use complete sources; incomplete manifests yield no chart.

Model metadata records requested/reported model, effort, response ID, latency and usage when returned. Monetary cost is null when not returned. Interrupted unknown outcomes count as attempts and remain explicitly unknown, not free. Traces exclude API keys and hidden reasoning text.

## Tabular charts

Every visualization has type, title, encoding and data. Encoding channels declare field, type and title. Supported tabular types are bar_chart, grouped_bar, time_series and histogram.

| Row field | Meaning |
| --- | --- |
| id | Unique within the response |
| cohort | Distinct human-readable series label |
| category | Bar category; null otherwise |
| year | Integer series year; null otherwise |
| bin_start/bin_end | Enrollment interval bounds; null otherwise |
| trial_count | Distinct contributing NCT IDs |
| contributor_count | Equals trial_count on tabular rows |
| evidence_set_id | Full supporting collection, including empty zero-bucket collections |

Bars use x=category, series use x=year, histograms x=bin_start/x2=bin_end. Y is trial_count; the series channel is cohort. Histogram bins are left-closed/right-open, with the last right endpoint inclusive. Actual/estimated enrollment types are disclosed and optionally filtered.

Combined phases remain combined categories. Missing phases are Not reported, distinct from NA/Not applicable. A PHASE2 filter includes Phase 1/2. Time series fill zero years over reported bounds and disclose excluded missing dates. Year filters always refer to study start, even if x is first-posted year; other bound semantics require clarification.

Rows are sorted: phases in clinical order, years ascending, other categories by descending combined cohort count then label. Comparisons share top-N categories, with absent combinations filled by zero. Multi-valued country/intervention categories can overlap.

## Networks

network_graph data contains nodes and edges. Encoding declares node IDs/labels/types and edge source/target/weight.

Nodes contain id, label, entity_type (sponsor/intervention), trial_count and evidence_set_id. Counts cover the node's role-specific supporting studies on displayed edges.

Edges contain id, source, target, weight, weight_metric, shared_interventions, contributor_count and evidence_set_id. Endpoints reference node IDs. Associations are undirected; ID ordering prevents duplicates.

| Relationship | Weight | Interpretation |
| --- | --- | --- |
| sponsor_drug | Distinct trials | Lead sponsor and DRUG/BIOLOGICAL intervention in a study |
| drug_cooccurrence | Distinct trials | Both interventions in a study, without a same-arm claim |
| sponsor_shared_drug | Distinct shared interventions | Sponsors' studies share interventions, not necessarily collaboration or ownership |

Shared-drug edges can have more supporting studies than their weight. Full evidence covers both sponsors' studies, scoped to the dataset. shared_interventions lists common entities. Edges sort by weight descending then IDs, with display clipping disclosed separately from completeness.

## Evidence, sources and runs

- GET `/evidence/{id}?offset=0&limit=50`: contributors, rule, total_contributors, next_offset. Limit 1–500; null next_offset means finished.
- GET `/datasets/{id}`: source query, counts, timestamps, versions and raw hash.
- GET `/datasets/{id}/studies/{nct_id}`: original saved JSON from that snapshot.
- GET `/runs/{id}`: request, response and execution trace.
- POST `/runs/{id}/resume`: return a completed response unchanged or continue an unfinished checkpoint if the session has not advanced. The original deadline remains; use a new request after expiry.

Citations contain dataset_id, nct_id, official URL, exact fields (JSON Pointer path and original value), and missing_fields. Missing paths can be audited against the raw snapshot; parent objects are included when available. Rules describe grouping and scope.

`provenance.evidence` is a preview map with contributor_count, citations, preview_complete and full-evidence URL. Preview length is not the contributor count. Follow pagination for all references. CLI/submission exports include complete sidecars independent of the running database.

Round 2 fixes: clarification scope resolves current explicitly supplied value first, then confirmed pending scope, then original/default population. Omitting the field differs from explicitly selecting population. Provider attempts/outcomes are journaled separately from checkpoints; unknown/missing usage never reports complete. Histogram counts use the same stable boundary sequence that is rendered, with the final right endpoint inclusive.
