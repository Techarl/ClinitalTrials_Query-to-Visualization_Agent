# NLI4CT official test split: evidence selection and fixed-model answers

The primary analysis retains all **500 questions**. All five arms answer the same frozen subset of **200 questions**. Failures remain in the denominators and count as unsuccessful.
Complete evidence coverage measures whether selected context retains every annotated evidence item. Answer correctness measures agreement with the official binary label. These are distinct outcomes.

| Method | Complete evidence / 500 | Correct answers / 200 | Failures / answer-subset failures |
|---|---:|---:|---:|
| hybrid | 279 | 135 | 0 / 0 |
| cross_encoder | 291 | 138 | 0 / 0 |
| jev | 300 | 141 | 0 / 0 |
| luna | 288 | 132 | 0 / 0 |
| full | 500 | 160 | 0 / 0 |

Paired differences in percentage points, with 95% bootstrap intervals over shared-trial connected components:

| Contrast | Complete-evidence difference | Answer-correctness difference |
|---|---:|---:|
| cross_encoder-minus-hybrid | +2.4 [+0.2, +4.5] | +1.5 [-2.8, +5.1] |
| jev-minus-hybrid | +4.2 [+2.0, +6.8] | +3.0 [-0.6, +6.8] |
| luna-minus-hybrid | +1.8 [-0.9, +4.4] | -1.5 [-6.2, +2.8] |
| full-minus-hybrid | +44.2 [+36.7, +50.4] | +12.5 [+7.3, +16.6] |
| hybrid-minus-full | -44.2 [-50.4, -36.7] | -12.5 [-16.6, -7.3] |
| cross_encoder-minus-full | -41.8 [-47.5, -34.7] | -11.0 [-15.3, -6.9] |
| jev-minus-full | -40.0 [-45.7, -32.9] | -9.5 [-13.2, -4.9] |
| luna-minus-full | -42.4 [-48.2, -35.2] | -14.0 [-18.1, -9.3] |
| jev-minus-luna | +2.4 [+0.8, +4.2] | +4.5 [+0.8, +8.8] |
| jev-minus-cross_encoder | +1.8 [+0.4, +3.5] | +1.5 [-2.4, +6.5] |

Input strata include the 237 questions whose full context fits 1024 tokens and their complement, the 326 questions with at most 24 source sentences and their complement, Single/Comparison tasks, and four section types. All stratum results and paired intervals remain in the JSON outputs; favorable subsets must not replace the primary results.
Strata were registered after execution began but before reviewers accessed outcomes. This is not a claim of preregistration before the first execution.

Intervals use seed 20261101 and 4000 resamples of shared-trial connected components; the primary population contains 113 components. Each draw is paired across methods, with differences weighted by question counts. Answer-subset intervals resample only components containing answer questions. Absolute proportions also have descriptive Wilson intervals: their independent-sample assumption does not fit shared trials and may produce narrow intervals. Stratification and multiple comparisons are exploratory, without multiplicity correction; these intervals do not establish a definitive winner.

Engineering interpretation: evidence retention and final answer quality must be considered separately. A reranker's coverage gain need not produce a reliably measured answer gain; the direct paired intervals above quantify that distinction. The budget-exempt full-text control tests whether compression is worthwhile when reading all supplied material is feasible. Its complete evidence coverage follows by construction from supplying every source sentence. These results evaluate the frozen configurations, not universal model superiority.

The full-text arm is exempt from the 1024-token selection cap. The other four arms share up to 24 candidates from hybrid retrieval. This tests evidence selection within supplied trial materials, not whole-corpus retrieval or chart correctness. Valid citation IDs establish membership in selected context, not support for the conclusion. If an answer call fails before its arm result is saved, evidence metrics also receive zero: this is the frozen runner's end-to-end scoring convention.
The 1024-token budget counts selected passage JSON with cl100k_base, including IDs and metadata. It excludes questions, instructions, reranking requests and outputs, so complete pipeline inputs are not equalized. Full text is an additional-information control. Budgeted arms use the same sequential greedy packer: skip candidates that do not fit and consider later ones, without truncation or a relevance threshold.
All five arms share the answer model and reasoning setting pinned in the protocol. Rerankers receive the original statement and complete candidate metadata. Jev/Luna additionally share a relevance rubric; MiniLM uses its fixed query-candidate encoding. This compares predefined system configurations, without claiming equally extensive model-specific tuning.
Direct Jev-minus-Luna and Jev-minus-cross_encoder paired intervals were added before formal outcomes were accessed. Citation-ID membership accepts an empty list, so the JSON also reports nonempty citation rates and the joint outcome of a correct label with nonempty valid IDs. That joint outcome still does not establish semantic citation support or complete necessary evidence.

The formal Jev implementation creates a new HTTP client per passage, while Luna reuses its client. Observed latency therefore includes asymmetric client overhead and cannot rank pure model speed. The [independent latency control](JEV_LATENCY_CONTROL.md) tested equal persistent connections and concurrency; its small development-set results remain separate from this test-set quality comparison. The original eight-question study remains in its [report](PASSAGE_SELECTION_RESULTS.md).
Ranking timers exclude shared embedding warmup and initial retrieval, and may include reads of cached passage scores after recovery. They do not measure end-to-end task duration or fair model-serving latency.

Descriptive ranking latency on the same 497 questions (3 questions excluded only from this timing table):

| Arm | Same-case samples | Median seconds | p95 seconds |
|---|---:|---:|---:|
| hybrid | 497 | 0.140 | 0.375 |
| cross_encoder | 497 | 0.984 | 2.446 |
| jev | 497 | 51.453 | 161.644 |
| luna | 497 | 44.797 | 155.996 |
| full | 497 | 0.000 | 0.000 |

Client and execution-period asymmetries remain. Full text performs no ranking, so its zero does not mean zero answer latency. The JSON retains all original timers and the separately filtered per-arm summaries; quality denominators remain unchanged.

Costs include failed attempts, reservations and shared embeddings. Values are estimates or provider-reported charges, not invoices:

| Stage | Attempts | Known cost USD | Unknown-usage reservation USD |
|---|---:|---:|---:|
| pilot | 171 | 0.089538 | 0.000000 |
| aborted_v1 | 158 | 0.062540 | 0.000000 |
| formal_v2 | 17195 | 3.995942 | 0.079918 |

Formal costs attributed by explicit arm tags, including ranking and answer attempts:

| Arm | Attempts | Known cost USD | Unknown-usage reservation USD |
|---|---:|---:|---:|
| shared_or_unattributed | 45 | 0.002076 | 0.000000 |
| hybrid | 200 | 0.623211 | 0.000000 |
| cross_encoder | 200 | 0.630475 | 0.000000 |
| jev | 8270 | 0.813054 | 0.000000 |
| luna | 8280 | 0.930621 | 0.079918 |
| full | 200 | 0.996505 | 0.000000 |

Shared embeddings and unattributed attempts are listed separately, without equal allocation or duplicate assignment to arms. Local MiniLM CPU time is not converted to USD; API charges do not represent total deployment cost. Unknown attempts without metadata cannot reliably be classified as embeddings but remain in total costs.

## Interruption and recovery

After the desktop-process interruption, execution resumed from frozen source matching the original protocol. The 500 questions, common 200-question answer subset, five arms and original budget ceiling were unchanged. Saved arm results and passage-score caches were reused.
The pre-resume snapshot contains 14983 files. Filenames establish that all five arm results were saved for 391 questions; this is execution progress, not accuracy.
Pre-resume filenames conservatively flag 6 case/arm timings: the case had saved activity without a saved arm result, or had a failure marker. This can include later arms that had not started. Exclusions apply only to latency quantiles; all quality denominators and paired analyses remain unchanged.
Original ranking_seconds remain in the per-case JSON and diagnostic summaries. A resumed timer can cover only remaining work and cannot represent a complete ranking or task. Retained times still describe this implementation and do not remove connection or execution-period differences.
The pre-resume ledger was verified as an exact prefix of the final ledger, preserving 10 prior unknown-usage attempts and $0.0799175 in reservations. Recovery added 3520 attempts. Snapshot costs are already included in formal_v2 and are not added again.
The [recovery appendix and latency exclusions](public-evaluation/nli4ct-recovery.json) retain snapshot, protocol and completion-receipt hashes. Unsanitized intermediate materials are not published.
A common-case latency summary additionally retains only questions for which all five arms completed without interruption flags. It supports descriptive same-case comparison only, without replacing the primary quality analysis or removing client and execution-period differences.

The final in-memory report adds elapsed-time telemetry to 1 ledger event(s) after the ledger's last disk write. Each addition was independently matched to the saved score metadata; all other event fields, usage, costs, statuses and ordering are identical. Accounting uses the original persisted ledger. Neither original was modified. Both digests and the exact timing-only discrepancy receipt are retained in summary.final_ledger_reconciliation.

Total: **17524 attempts, $4.227937**. Usage is unknown for 10 attempts, retaining $0.079918 in reservations. Embedding cost of $0.004192 is already included.

Outputs: [summary](public-evaluation/nli4ct-summary.json) · [sanitized per-case metrics](public-evaluation/nli4ct-cases.json) · [cost reconciliation](public-evaluation/nli4ct-accounting.json). Question text, raw materials and gold answers are not published.
The [independent official-source audit](public-evaluation/nli4ct-independent-audit.json) reconstructs source sections, annotated evidence and answer-label scores without importing product scoring functions. Public sanitized case metrics support aggregate and paired-statistic reproduction; source-level reproduction also needs the local official dataset and saved study outputs. It does not independently replay models, tokenization or billing.
