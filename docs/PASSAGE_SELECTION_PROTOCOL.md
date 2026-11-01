# Evidence selection experiment protocol

This is a separate experiment from drug-name candidate retrieval and from the earlier plan gate. The scope is **single-passage relevance ranking and budgeted evidence assembly**, not incremental novelty, whole-answer sufficiency or clinical advice. Ordinary service startup does not enable it.

## Fixed comparison

The [frozen corpus](../evaluation/passage_selection.json) contains 334 eligibility passages representing 23 real trials, from 24 scanned records in the shipped CTgov snapshots. One record silently produced no passages in the historical splitter; the subsequent [audit](public-evaluation/passage-extraction-audit.json) documents the correction without changing this frozen experiment. Each passage retains the source file hash, NCT identifier, JSON field, exact character offsets and inclusion/exclusion section. Twelve authored comparison questions have explicit required evidence groups; repeated equivalent statements are alternatives. Four development questions concern asthma/melanoma; eight heldout questions concern psoriasis/rheumatoid arthritis. Trial identities do not cross the query splits. All represented trials remain in the shared retrieval corpus as distractors.

Every query first retrieves the same 12 candidates using BM25 plus `text-embedding-3-small` (256 dimensions), reciprocal rank fusion with k=60. Gold passages are never inserted into the pool. Four arms reorder that pool:

1. Original hybrid order.
2. The dedicated English `cross-encoder/ms-marco-MiniLM-L6-v2`, pinned to revision `233902d25c440f23af6f7d6e94d2946bac0bee0a`, CPU ONNX float model.
3. Configured OpenRouter Jev, one focused relevance question per passage.
4. GPT-6 Luna with the same relevance question and structured score.

No rejection threshold is tuned. Descending scores determine order; ties retain initial hybrid order. All arms use the same whole-passage packer, per-trial exact-text deduplication and **350-token serialized evidence cap** (`cl100k_base`). The cap includes passage IDs, study IDs and section labels. Model-specific wire token counts differ from this common tokenizer; actual billed usage is recorded separately. Oversized passages are skipped, never cut mid-criterion. A pre-run feasibility check confirmed that at least one complete gold combination fits the cap for every question (94–285 tokens).

The same GPT-6.1 Sol, medium reasoning, answers every arm. Its answer is an **extractive evidence table**: exact complete quotations with source IDs and section labels, plus missing information. This deliberately makes source fidelity independently checkable. It does not assess free-form clinical reasoning or whether a prose explanation is medically sound.

## Outcomes and limits

- Required-evidence coverage in the initial pool, in packed context and in the answer; coverage checks every requested side rather than only present citations.
- Retention conditional on required evidence being present in the pool. A missing first-stage candidate cannot be rescued by reranking.
- nDCG@5 for authored key-evidence labels **within the fixed candidate pool**.
- Retention of annotated exclusion passages for the three questions with a contrary premise.
- Exact quotation/section/source validation, and a strict secondary answer score requiring all required groups and no additional unannotated passages.
- All API calls, actual usage, estimated/provider-reported cost, serial reranking latency, answer latency and local CPU time. Shared corpus indexing costs are reported separately from per-query execution. Local inference has zero API fee but is not free compute.

The annotations are authored source anchors, not independent clinician judgments or exhaustive relevance labels. An unannotated passage may contain useful supplementary context. Thus the strict no-extra-passages answer score and binary nDCG must not be described as comprehensive answer correctness/relevance. Required-evidence coverage and exact quotation fidelity are the more direct measurements. The corpus is intentionally small, English, and queries identify trials explicitly; it cannot establish full-registry recall or broad multilingual performance. No clinical efficacy inference is made from eligibility text.

Development is completed before heldout execution. The run saves the corpus/protocol/code hashes before calling any model. No labels enter embeddings, ranking or answer prompts. Every score, pool and answer is saved; interrupted paid attempts remain reserved in the ledger. Historical gate and Harness results remain unchanged.

### Secondary scope control, declared before heldout

These queries explicitly name NCT IDs. A model might appear useful simply by rejecting other trials, a task deterministic scope rules can perform. After the four primary arms, an additional `scoped_hybrid` control filters the **same retrieved pool** to literal NCT IDs extracted from the user question, preserves hybrid order, uses the same packing cap and calls the same answer model. It uses no labels and no relevance model. This is a diagnostic control, not a fifth tuned reranker. Its code/policy is frozen before any heldout execution. It does not retrieve replacement candidates, so it cannot repair first-stage misses.

## Why this is distinct from the older hybrid experiment

The earlier 12-query experiment tested drug-name candidate lookup. Exact-name hit@5 was BM25 4/12 and dense/hybrid 2/12. A later generic-name mention check gave 8/12 versus 10/12, but that proxy was not independently validated as identity resolution. Neither result measures eligibility-passage selection. Here hybrid retrieval supplies candidates; Jev and the cross-encoder only reorder them. Statistical charts continue to use complete deterministic populations.

## References

[TypeSafe passage classification](https://docs.typesafe.ai/cookbooks/classifying_rag_passages) demonstrates classifying retrieved text before answering. [TypeSafe reranking](https://docs.typesafe.ai/cookbooks/rerank_typesafe) scores query–candidate pairs after initial retrieval. This experiment adopts only the focused relevance decision, without mixing contradiction, trust, novelty and sufficiency into one score. The [official cross-encoder model card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) identifies its MS MARCO training task, English scope and Apache-2.0 license.

## Reproduce

Use an isolated Python 3.12 environment with `requirements.lock` plus `experiments/requirements-rerank.txt`. Download the pinned ONNX/tokenizer files using the setup script, then run:

```powershell
python -m pip install -r requirements.lock -r experiments/requirements-rerank.txt
python -m pip install --no-deps -e .
python -m scripts.setup_passage_reranker
python -m scripts.register_passage_scope_control
python -m experiments.passage_selection --split development
python -m experiments.passage_selection --split heldout
python -m experiments.passage_scope_control
python -m scripts.report_passage_selection
```

The model reads keys from the existing private `.env`. Each command is explicitly paid and opt-in, with a persisted $6 / 600-attempt cap for the whole study. Missing usage keeps its reservation. The production service does not require ONNX, tokenizers or tiktoken.
