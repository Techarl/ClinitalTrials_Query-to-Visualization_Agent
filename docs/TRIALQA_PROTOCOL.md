# TrialQA: production tool-loop evaluation

This is a project adaptation of [EdisonScientific LABBench2 / TrialQA](https://github.com/EdisonScientific/labbench2), not an official LABBench2 score. The official Hugging Face source is pinned by `evaluation/public_datasets.py`; original data and canary remain in ignored `data/`, with no raw dataset redistribution. The dataset has 120 questions referring to 102 unique trial records. All questions remain in the coverage denominator.

## Two conditions

- **Given sources:** the question plus owned handles for the trial records linked by the dataset. The Agent must read relevant fields itself. No reference answer or key passage is supplied.
- **Frozen retrieval:** the question alone. The Agent chooses `lookup_trials` queries, reads returned fields, and publishes a cited answer through the production V2 tool loop. Search covers the same frozen 102-record collection using the existing keyword retrieval tool. This is not full-web retrieval.

Both use the configured GPT-6.1 Sol endpoint and medium reasoning, 12 model calls, 20 tool calls, the same context limit, and a 600-second run deadline. The available tool surface is the same in both conditions: lookup, source reading, text-answer publication, result validation, and optional experience/skill reading. Statistical and PubMed tools are excluded from this text-reading study to keep its frozen source boundary explicit. The actual V2Agent, SQLite state, journal, source-handle ownership and quotation checks are unchanged.

## Splits and cost

Questions sharing any NCT ID form connected components; deterministic hashing assigns whole components to development, calibration, or heldout. This grouping happens without answers or scoring feedback. Runtime order is also fixed by question-ID hashing. A pilot uses the first two development questions, two conditions, and a total **$1.50 cap**, including judging. Formal expansion requires a separate run directory and a frozen protocol; it does not start automatically after the pilot.

The protocol includes selected IDs, group assignments, input/record hashes, relevant source-code hashes, actual model configuration, and judge prompt. Source code is archived beside the manifest. Each paid attempt is tagged with question, condition and stage before the call. Missing usage keeps its conservative reservation. Cached results are rejected when the protocol/input fingerprint differs. An interrupted case resumes its stored run and durable model/tool decisions; idempotent submissions cover the gap before the run-ID file is published.

## Scoring and limitations

Only after **all planned Agent executions terminate** does the evaluator load `trialqa-gold.json`. A fixed GPT-6 Luna, low-reasoning structured judge compares the answer with the official ideal answer and key passage, and separately judges whether the answer's actual quotations support its claims. This judge is fallible; its verdict is never fed to the Agent, experience bank, adoption policy or retries.

Report runtime completion, answer correctness, cited-answer correctness, exact quotation integrity, source-read counts, failures, latency and total cost separately. Failed and missing runs remain in the denominator. A correct source quote is not by itself proof that the model's interpretation is correct.

The current CTgov JSON snapshots can differ from the benchmark's source version or formatting. The pre-execution source audit is diagnostic only: it neither selects questions nor supplies passages to the model. Source drift and retrieval/reading failures must be distinguished in review. The grouped heldout subset is reported separately from development and calibration questions; a 120-question combined result would not be described as 120 untouched heldout cases.

For the formal comparison, report all 120 questions and each split separately. The primary paired contrast is frozen retrieval minus given sources for cited correctness. Missing answers or judgments count as unsuccessful in this operational rate and are also enumerated separately. Resample whole NCT-connected groups, preserving all questions and both arms within each sampled group, for a 10,000-replicate percentile bootstrap interval (fixed seed 20261101). A paired sign-flip test swaps arm labels for whole groups; enumerate all assignments when at most 20 groups have nonzero differences, otherwise use 10,000 fixed-seed assignments. These intervals and tests quantify this dataset comparison, not general clinical competence, and do not remove automated-judge error or public-benchmark contamination.

Run the pilot explicitly:

```powershell
.venv/Scripts/python.exe -m evaluation.trialqa_study --output data/trialqa-pilot-v2 --split development --limit 2 --max-usd 1.5 --export docs/public-evaluation/trialqa-pilot-v2.json
```

Local traces include runtime inputs and full source quotations. The shareable JSON report excludes question text, reference answers, raw records and tool traces.

## Development pilot result

The frozen v2 pilot ran two hash-selected development questions in both conditions. Both conditions completed 2/2, received 2/2 correct and citation-supported judgments, and preserved exact quotations. Retrieval runs issued their own keyword searches before reading the trial source. This is a four-run mechanism and cost check, not evidence of general accuracy or superiority of either condition.

The complete ledger records 22 execution calls and four post-execution judge calls, with estimated API cost **$0.1405541** and no missing usage. Individual executions took 14.6–20.0 seconds. The manifest groups all 120 questions into 19 development, 22 calibration and 79 heldout questions across 102 trial-connected components. Formal execution has not started.

The earlier v1 smoke run stopped before any API calls because the budget wrapper did not expose the production planner's lock/path/report interface. Its four failed initialization receipts are retained locally as a runner diagnostic, excluded from Agent performance claims. A targeted production-planner regression test covers the fix; seven TrialQA adapter tests pass. The v2 report is the completed pilot result.
