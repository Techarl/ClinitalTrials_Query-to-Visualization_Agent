# Jev for evidence selection: measured benefit and a stronger simple control

**Jev improved selection over unfiltered hybrid order on this small benchmark, but did not beat Luna or deterministic trial-ID filtering on the heldout questions.** The useful architectural finding is to preserve structured scope first, then test text ranking only where ambiguity remains. This experiment supports the feasibility of focused evidence selection; it does not justify enabling Jev by default.

The [frozen protocol](PASSAGE_SELECTION_PROTOCOL.md) and [machine-readable results](passage-selection/summary.json) describe 334 exact-source eligibility passages: 24 trials were scanned, but only 23 produced passages. There are four development questions and eight disease-disjoint heldout questions. All four primary arms receive the same hybrid top-12 candidates and a 350-token evidence cap. The answer model is GPT-6.1 Sol, medium reasoning. The actual decision model returned `typesafe/jev-1.14-20261014`; the cheap model is GPT-6 Luna. A fifth, locally frozen diagnostic filters the same pool to NCT IDs literally present in the question, without calling a ranking model.

A subsequent [extraction audit](public-evaluation/passage-extraction-audit.json) finds 365 passages across all 24 records with the corrected splitter, retaining every non-whitespace criterion character as an exact source span. Historical scores and archives above are unchanged; they describe the original 334-passage corpus.

## Heldout results

**Latency interpretation correction:** the historical timings below include unequal HTTP client reuse. They measure those implementations, not an intrinsic model speed ranking. See the [controlled client/concurrency probe](JEV_LATENCY_CONTROL.md) and [fairness review](JEV_FAIRNESS_REVIEW.md).

| Method | Required-evidence coverage in context, macro mean | Answers containing every required exact quotation / 8 | Key-evidence nDCG@5 within pool | Mean ranking seconds | Ranking + answer API cost, all 8 queries |
|---|---:|---:|---:|---:|---:|
| Hybrid order | 62.50% | 2 | 0.716 | <0.001 | $0.028424 |
| MiniLM dedicated cross-encoder | 85.42% | 5 | 0.877 | 1.221 | $0.026574 |
| Jev relevance | 89.58% | 6 | 1.000 | 34.144 | $0.033315 |
| Luna relevance | 89.58% | 6 | 1.000 | 16.320 | $0.033757 |
| Literal trial-ID filter + hybrid order | 89.58% | 6 | 0.985 | <0.001 | $0.026438 |

Jev improves context coverage in five paired questions and ties in three; it loses none against unfiltered hybrid order. Luna and the rule control have the same coverage pattern. The dedicated cross-encoder improves four and ties four. This is only eight authored questions, without repeated sampling or independent human labels; it is not a broad superiority claim. The English MS MARCO MiniLM is one small dedicated reranker, not a representative of every modern reranking model.

Ranking latency is the measured **serial** cost of scoring the 12 candidates; parallel serving could change it and was not benchmarked. Mean answer times are 3.91 / 3.27 / 5.69 / 3.78 / 3.57 seconds, respectively; mean initial query retrieval is another 1.07 seconds. The rule filter is below the timer's effective resolution. CPU inference has no API bill but still consumes compute. The cost column includes each arm's generation and ranking calls, excludes shared indexing/query embeddings, and is not an invoice. Small answer-cost differences partly reflect different extracted text lengths.

All 139 emitted quotations across the 60 development/heldout answers match the selected source text and section exactly. That does **not** mean every answer is complete. The strict secondary rubric also rejects extra unannotated passages; its heldout scores are 1/8, 5/8, 4/8, 5/8 and 5/8. Extra passages can be useful supplementary context: in the development IgE question, some methods also quote an IgE-related exclusion. The annotations mark required anchors rather than every possibly relevant statement. Consequently this strict rubric must not be equated with clinical correctness. No free-form medical reasoning is scored.

For the two heldout questions containing a contrary premise, the hybrid context retains half the annotated exclusion passages; the other four approaches retain all of them. The sample is too small to establish general robustness to conflicting evidence.

## What happened to hybrid retrieval?

Before reranking, the heldout BM25 top-12 contains all required evidence groups for **8/8** questions. Dense retrieval does so for **3/8**; equal-weight reciprocal-rank fusion for **6/8**. Macro group coverage is 100%, 60.42% and 89.58%, respectively. In development, fusion instead improves complete-pool coverage from BM25's 3/4 and dense's 2/4 to 4/4. The two splits illustrate why hybrid retrieval should be measured rather than assumed superior.

These questions contain exact trial IDs and specific criteria, which favor lexical matching. Fusion can promote text about the right medical topic from the wrong trial and push necessary evidence beyond the top-12 cutoff. Reranking cannot recover those absent passages. In the heldout washout comparison one trial's criterion is missing; in the cancer-history comparison one required statement is missing. All available groups were retained by Jev, Luna and the scope rule, so their 89.58% context coverage reaches this fixed pool's ceiling.

This finding is separate from the older 12-query **drug-name** experiment. That experiment's strict-name hits were BM25 4/12 versus dense/hybrid 2/12; the later generic-name mention proxy was 8/12 versus 10/12 without independent identity annotation. Neither older measure establishes passage selection quality. The present study also only measures this bounded annotated corpus, not full-registry recall.

## A real follow-up retrieval, reported separately

After freezing the main results, a [single-case diagnostic](passage-selection/gap-recovery.json) starts from the actual failed `psoriasis_washout` answer. A bounded LangGraph loop gives the model the original question and its own missing-information report, **without gold labels**. The model chooses a focused query for `NCT00770965`, the retrieval tool searches that explicit trial scope, and Jev selects evidence from the merged candidate pool. Sol then quotes both required criteria and decides to stop.

The tool returns six real source passages. One additional retrieval increases required-evidence and answer coverage from 50% to 100%, using 322 context tokens; all final quotations are exact. The trace includes the model's requested query, validated trial IDs, returned passages, scores, final quotations and termination decision. This is an actual observed-data-dependent loop over frozen source data, not a prefilled successful answer.

The probe is **post-hoc, one selected failure**, with no counterfactual reranker comparison. It shows that targeted retrieval plus scope filtering can repair this miss; it does not attribute that recovery uniquely to Jev or change the original 6/8 result. An initial search correctly scoped to the requested trial IDs could also avoid this failure. Whole-evidence sufficiency remains separate from a single-passage relevance score.

## Accounting, artifacts and implementation

- Main comparison and scope control: **364 API attempts**, estimated/provider-reported **$0.206085**, including 16 embedding requests costing an estimated $0.000474. No unknown-usage attempts.
- Separate recovery probe: **11 additional attempts**, **$0.008276**, one additional retrieval. It reuses corpus embeddings; all new calls are metered.
- Combined new work: **375 attempts, approximately $0.214360**. This excludes the previous VNext studies, browser smokes, earlier work and account-wide usage.
- [All primary pools, scores, answers, ledgers, original source code and embedding cache](passage-selection/traces.zip), with [SHA manifest metadata](passage-selection/archive.json).
- [Recovery trace and ledger](passage-selection/gap-recovery.zip), and [per-case results](passage-selection/cases.json).

The dedicated model is pinned by revision and file hashes. All 144 query–candidate pairs fit its 512-token input limit; none were truncated (maximum 291 tokens). Every frozen passage is verified against its original snapshot bytes and character span. The final code passes **144 tests with real Neo4j**; the prior VNext checkpoint and its CI remain documented separately.

`app/v2/evidence_selection.py` provides the replaceable, read-only passage representation and context packer. The optional research runner swaps hybrid, cross-encoder, Jev and Luna ranking while sharing the same inputs and packer. It is not wired into default chart execution: statistical counts still use complete structured scopes. ONNX/tokenizer dependencies are confined to an optional environment. After the recorded study, a small guard was added to reject a budget too small even for `[]`; it does not change the tested 350-token behavior. The archive preserves the exact original source hashes rather than substituting that later revision.

The implementation choice supported by these results is **structured scope → measured retrieval → optional relevance ranking → budgeted evidence → coverage-driven follow-up**. Keep Jev available for experiments where rules cannot resolve relevance. The current explicit-ID test does not establish an incremental quality benefit over the simple scoped baseline, so automatic deployment would be premature.
