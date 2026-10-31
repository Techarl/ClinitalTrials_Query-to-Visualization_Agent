# Explicit research pilots

The default FastAPI service remains the frozen repaired baseline. It imports no experiment runner, calls no OpenRouter model, and performs no runtime harness learning. Experimental API feature flags remain false and are rejected if enabled; research is exposed through separate opt-in commands in `experiments/`. These pilots were authorized after the baseline review fixes and release validation completed.

## Decision gate: B / B+G / B+D

The paired replay uses all 50 turns from the frozen baseline report. B is the existing planner plus fixed validators. B+G adds one GPT-6.1 Sol structured gate. B+D adds one real `typesafe/jev-1.14` call through OpenRouter's Decisions API. Gates receive identical request/context, proposed decision, resolved plan and capability text. They never receive expected answers, scores, raw study counts or future turns.

The action-probability threshold 0.65 and plan-preservation threshold 0.8 were fixed before the full replay. The gate cannot invent a missing plan, promote a clarification into execution, or bypass fixed validation. Uncertain action classifications preserve the baseline result. Errors are recorded as failures, with no silent model fallback. This is paired counterfactual replay, not end-to-end interactive gated conversations.

| Configuration | Correct / 50 | Incorrect executions | Overclarifications | Answered | Added p50 / p95 latency |
| --- | --- | --- | --- | --- | --- |
| B | 50 | 0 | 0 | 37 | 0 / 0 s |
| B+G | 50 | 0 | 0 | 37 | 2.797 / 4.281 s |
| B+D, fixed gate policy | 25 | 0 | 25 | 12 | 2.219 / 2.813 s |

Jev's raw action matches 46/50 labels; the additional Noul threshold causes most extra clarification. This negative result applies to this exact prompt, gate policy and small task set. It does not show that all decision models or calibrated routing are ineffective. No threshold was retuned on heldout scores. Descriptive probability/Brier summaries are not evidence of clinical calibration.

The extra GPT gate used 96,043 input / 2,403 output tokens. Jev used 111,468 input / 3,013 reported output tokens and OpenRouter reported $0.004681656 total. Both retain the baseline's 50 planner calls and add 50 gate calls; there is no planner-cost saving. OpenAI did not return an invoice amount. The initial one-turn connectivity probe is separate from the full replay.

```powershell
# Dry inspection, no model calls:
.\.venv\Scripts\python.exe -m experiments.decision --baseline docs\evals\semantic_report_ea65a0a.json --output data\decision_new
# Explicitly paid:
.\.venv\Scripts\python.exe -m experiments.decision --baseline docs\evals\semantic_report_ea65a0a.json --output data\decision_new --live
```

Protocol source: [OpenRouter Jev](https://openrouter.ai/docs/guides/community/jev), [Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request). Typed output does not imply semantic correctness.

## BM25 + embedding candidates

The candidate corpus contains 949 conservative DRUG/BIOLOGICAL name identities from the active frozen example sources. Documents contain submitter-provided `otherNames` plus up to three exact source objects and JSON Pointers. They are not an authoritative ingredient/brand dictionary. No names are automatically merged, accepted or used to define the statistical universe.

The pilot compares local BM25, `text-embedding-3-small` at 256 dimensions, and RRF with k=60 over each retriever's first 50 results. The embedding cache is local and content-addressed. Index and 14 query embeddings used 24 API calls / 15,599 input tokens. Twelve hand-authored known-name queries include brands and Chinese names; two unknown/ambiguous probes deliberately demonstrate that dense retrieval still returns candidates.

Primary exact source-label hit@5 was 4/12 for BM25 and 2/12 for dense/hybrid. Inspecting the outputs showed that many plausible names such as `Pembrolizumab (KEYTRUDA)` fail an exact `pembrolizumab` label test. A separately disclosed, **post-hoc** generic-name mention proxy gives 8/12 BM25 and 10/12 dense/hybrid. That proxy excludes obvious placebo/combination/biosimilar labels, lacks independent annotation, and does not prove ingredient equivalence. Both metrics and full ranked source candidates are retained; neither is end-to-end entity-resolution accuracy.

This pilot supports keeping retrieval as an inspectable candidate aid. It does not justify changing the validated baseline or claiming that hybrid beats dense retrieval here. Reliable Chinese/brand normalization still needs a terminology source and a separately annotated ambiguity set. Complete official source retrieval remains mandatory for counts.

```powershell
.\.venv\Scripts\python.exe -m experiments.entity_retrieval
.\.venv\Scripts\python.exe -m experiments.entity_retrieval --live --output data\entity_new
```

Method references: [OpenAI embeddings](https://developers.openai.com/api/docs/guides/embeddings), [RRF ranking](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking). Implementation is original; no library or paper code was copied.

## Restricted Harness² protocol

The pilot preserves task-level Propose–Probe–Compose and a cross-task experience library. It is a **planning-only restricted port**, not a reproduction of the original eight-component professional-task benchmark. Only a planner instruction addendum and a smaller context character cap may change. The base instructions, validators, schema, source compiler, graph, statistics, evidence, model and hard budgets remain fixed. A custom HarnessProvider is injected only by this command; API feature flags remain unchanged and the outer experiment record identifies the injected configuration.

Before the heldout run, the protocol is fixed to the 12 heldout cases in the frozen file's order (14 turns including the three-turn clarification case), with these four arms:

- H0: frozen reference execution, reused without another API charge.
- H0 multisample: six fixed-H0 executions including the reference, followed by one unlabelled selector.
- PPC without memory: H0 reference, general editor, domain editor, two actual probes, composer, and actual composed execution.
- PPC with memory: the same PPC, restarting each task from H0 and retrieving up to three prior experience entries through BM25 + embeddings + RRF.

For single-turn tasks, the control and PPC each comprise seven logical model calls including the shared reference. Multi-turn execution counts differ and are reported. All new executor/editor/selector calls use the same model and medium effort, a 4,096-token output cap, and an explicit estimate ledger capped at $20 for the pilot. The frozen reference had the production 32,768 cap; its actual outputs were below 4,096. This is not exact dollar/token matching; actual usage for every role is retained.

The runtime receives only current public task fields, observed executions and earlier unlabelled experience. Expected answers and scorer outputs are stripped before runtime; **all scoring happens after every stream and experience update finishes**. Identical action/population plans produce a negative observation and no invented improved component. Changed components are only composer-adopted candidates, never labelled as oracle-verified successes. No future task enters an earlier edit or retrieval call. The single-order pilot has no task-order variance estimate.

The one-development-task smoke completed all four paths at an estimated $0.0904 with identical correct results. Its empty experience library motivated recording explicit negative evidence when edits change no observable behavior; this implementation adjustment was made before the heldout run.

```powershell
.\.venv\Scripts\python.exe -m experiments.harness --baseline docs\evals\semantic_report_ea65a0a.json --output data\harness_new
.\.venv\Scripts\python.exe -m experiments.harness --baseline docs\evals\semantic_report_ea65a0a.json --output data\harness_new --live --max-estimated-usd 20
```

Original method: [Harness² paper](https://arxiv.org/abs/2610.14224), [official implementation](https://github.com/google-research/harness2). This repository implements its own restricted adaptation; it does not install or copy that executor.

The frozen 69bd5ea run is complete. Full traces, actual usage and the audited valid subset are retained in [harness_report.json](evals/harness_report.json).

### Protocol audit during the run, before scores

The frozen harness pilot passes a complete task transcript and its H0 replay to the editors. For the final three-turn `repeated_clarification` case, this exposes later user answers while designing guidance that will also be replayed at the first turn. It is therefore unsuitable as a causal online-conversation test. This issue was identified from code inspection before the runtime streams completed or gold scoring ran. The complete 12-task / 14-turn exploratory record will be preserved, while the valid comparison subset is the preceding **11 single-turn tasks**. The interactive case is diagnostic only; it cannot support a claim of better clarification memory. Baseline multi-turn validation remains separate. A future interactive PPC needs turn-by-turn causal editing.


### Observed Harness² pilot results

| Arm | Valid single-turn tasks | Correct | Incorrect executions | Overclarifications |
| --- | --- | --- | --- | --- |
| H0 | 11 | 11 | 0 | 0 |
| H0 multisample | 11 | 11 | 0 | 0 |
| PPC without memory | 11 | 11 | 0 | 0 |
| PPC with memory | 11 | 11 | 0 | 0 |

The retained exploratory 12-task/14-turn aggregate is also 14/14 in each arm; the multi-turn case remains diagnostic despite that score. The complete pilot made **238 new GPT calls** and used an estimated **$1.106343** at standard list rates, excluding the already-billed shared H0 references and the separately recorded embedding calls. The estimate uses actual cache/read/write and output token usage; it is not an invoice. The one-task development smoke cost estimate is recorded separately.

The experience stream stored 12 unlabelled observations and performed 11 retrieval events, using 12 cached embedding requests / 308 input tokens. It adopted **zero reusable modified components**. One final plan signature differed from the reference without an adopted component; this is an observed difference, not proof of improvement. Most entries explicitly record no observed behavior change. This tests storage and retrieval plumbing, but does not establish beneficial component transfer or task-order robustness.

There is no observed accuracy gain over the already-correct H0 on this small pilot. The extra computation and tokens therefore do not justify enabling online harness adaptation in the baseline. A stronger follow-up needs independently annotated harder tasks and causally isolated per-turn adaptation, rather than weakening H0 or hiding negative results.

Subsequent CLI hardening defaults to single-turn tasks (11 heldout cases); `--include-transcript-diagnostic` explicitly includes the full-transcript diagnostic. API-error bodies are suppressed from research CLI exceptions. Those safety/default changes were made after the recorded 69bd5ea pilot; its raw observations and frozen provenance remain unchanged.

## Artifact index

- [Frozen semantic baseline used by the experiments](evals/semantic_report_ea65a0a.json)
- [Decision paired replay](evals/decision_report.json)
- [Hybrid candidate rankings and exact source references](evals/entity_retrieval_report.json)
- [Harness runtime streams, scores and ledger](evals/harness_report.json)
- [Unlabelled experience library](evals/harness_experience.json)

The default service remains unchanged by these research results. No experimental benefit is claimed.


For the valid 11 single-turn tasks, each compute-control/PPC arm made exactly 66 new GPT calls plus 11 reused H0 references (77 logical calls). Their estimated new GPT costs were $0.2512 (multisample), $0.4104 (PPC without memory), and $0.3085 (PPC with memory). Sum-of-component latency p50 was 28.766 / 34.109 / 30.393 seconds respectively; these sums exclude some initialization, file, embedding and cache overhead, so they are not exact end-to-end wall-clock measurements. Role-level accounting is reproducible with `python -m scripts.summarize_harness` and is retained in the report. Equal call counts do not imply equal token or dollar costs.

Recorded API usage for this round is itemized in [API_USAGE.json](API_USAGE.json): 475 generation calls, approximately $1.9877 at recorded standard cache/input/output list rates; Jev reported $0.004778 for 51 calls including the connectivity probe. Embeddings made 36 calls / 15,907 input tokens, with no invoice amount returned. These figures exclude earlier project work and account-wide usage, and the OpenAI figure is an estimate rather than a bill.
