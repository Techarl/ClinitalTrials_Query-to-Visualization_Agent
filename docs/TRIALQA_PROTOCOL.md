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

The formal producer uses `--output data/trialqa-formal-v1 --split all --limit 0 --max-usd 15 --export docs/public-evaluation/trialqa-formal-v1.json`. Do not start a second producer in that directory while it is running. After it publishes `report.json`, generate the independent statistical report:

```powershell
.venv/Scripts/python.exe -m scripts.report_trialqa --run data/trialqa-formal-v1 --output docs/public-evaluation/trialqa-formal-statistics.json
```

The reporter refuses partial studies, verifies every planned question/condition, checks source-archive and raw-record hashes, independently rechecks quotations against the frozen source records, and reconciles known cost plus unknown-usage reservations. It computes per-split cited correctness and the group-level paired statistics above without loading reference answers. Current-workspace differences from the archived executed source are reported explicitly.

## Development pilot result

The frozen v2 pilot ran two hash-selected development questions in both conditions. Both conditions completed 2/2, received 2/2 correct and citation-supported judgments, and preserved exact quotations. Retrieval runs issued their own keyword searches before reading the trial source. This is a four-run mechanism and cost check, not evidence of general accuracy or superiority of either condition.

The complete pilot ledger records 22 execution calls and four post-execution judge calls, with estimated API cost **$0.1405541** and no missing usage. Individual executions took 14.6–20.0 seconds. The manifest groups all 120 questions into 19 development, 22 calibration and 79 heldout questions across 102 trial-connected components. The completed formal study is reported separately below.

The earlier v1 smoke run stopped before any API calls because the budget wrapper did not expose the production planner's lock/path/report interface. Its four failed initialization receipts are retained locally as a runner diagnostic, excluded from Agent performance claims. A targeted production-planner regression test covers the fix; seven TrialQA adapter tests pass. The v2 report is the completed pilot result.

## Completed formal study

All **120 questions × two conditions** ran under the frozen protocol. Only after all 240 executions terminated did the evaluator load reference answers and run 237 Luna judgments; three executions published no answer and were scored unsuccessful without a judge call. No failed case was selectively rerun. These are project-adapted TrialQA results with a fallible automated judge, not an official LABBench2 score.

| Split | Questions | Given sources: correct | Given sources: cited correct | Frozen retrieval: correct | Frozen retrieval: cited correct |
|---|---:|---:|---:|---:|---:|
| Development | 19 | 18 | 14 | 18 | 16 |
| Calibration | 22 | 18 | 18 | 16 | 15 |
| Heldout | 79 | 70 | **64** | 71 | **64** |
| All | 120 | 106 | **96** | 105 | **95** |

The primary cited-correctness difference, frozen retrieval minus given sources, is **−0.83 percentage points**, with a 95% NCT-group bootstrap interval of **[−7.63, +5.88] points** and exact groupwise sign-flip p = **1.0**. The heldout difference is **0 points**, interval **[−7.89, +7.79]**, p = **1.0**. These results do not establish a difference or equivalence. The 10,000 bootstrap samples preserve all questions within each NCT-connected component and pair both conditions; there are 102 groups overall and 65 in heldout. Split analyses are exploratory and uncorrected for multiple comparisons.

Runtime completion is separate from answer quality. Given sources finished 119/120 runs, with one explicit limitation. Frozen retrieval finished 117/120, with one explicit limitation, one partial run after a `PermissionError`, and one failed run after an `APIConnectionError`. The partial run had already published an answer, so it remains eligible for independent answer grading while its runtime status stays partial. The durable diagnostic retains the exception type, but not a traceback or exact filesystem path; the cause of the permission failure is not established.

All **237 published answers** passed independent exact-quotation checks against the pinned raw trial records. That does not imply adequate semantic support: across both conditions, **20 answers judged correct did not have sufficient quoted support for all material claims**. Typical issues were extra comparison details, subgroup labels or interpretive statements outside the quoted passage. This gap is a reason to retain semantic citation evaluation alongside exact quotation validation.

The known capability limitation was a location-array counting question: both conditions read the CANOPY-A record but stopped because the frozen reading tool surface lacked a deterministic array-count operation. Another example gave a requested calculation expression without computing its final percentage. These identify a concrete missing tool capability; the results were not repaired after seeing reference answers.

Post-run source review also identified reference/current-record differences in at least three illustrative questions: HBI0101 liver-function thresholds (`ab08df93-4040-4b5d-8da0-1fe298d339d8`), the Keynote-522 AE observation window (`c89cdd12-6f70-434f-9473-67b07bfb6633`), and AMETRINE-PEDS Week-12 outcome counts (`0cac45cf-4a07-4ab9-8c58-7998d74dca84`). The latter was independently recounted from the frozen outcome array and agreed with the Agent's current-record answer. These examples are not an exhaustive source-version adjudication and do not change the original scores. The pre-execution audit had already marked 70/120 cases for version/format review; that flag is not proof that 70 questions are unanswerable or mislabeled. No cases were removed.

Median execution latency was 19.82 seconds with given sources and 23.23 seconds with frozen retrieval; the respective 95th percentiles were 30.94 and 37.04 seconds. This measures the implemented single-host pipelines, not isolated model latency.

Formal accounting contains **1,699 recorded attempts**: 1,462 execution attempts including two unknown-usage attempts, plus 237 judge calls. Known usage corresponds to **$10.067919475** in versioned API estimates; the two unknown attempts retain **$0.212135** in reservations, for a total estimated-or-reserved **$10.280054475**. These are not invoice totals. The earlier pilot's $0.1405541 is separate; combined formal-plus-pilot accounting is 1,725 attempts and $10.420608575 estimated-or-reserved. The aborted smoke run cost zero.

Artifacts: [complete runner report](public-evaluation/trialqa-formal-v1.json), [independent statistics and accounting](public-evaluation/trialqa-formal-statistics.json), [frozen protocol](public-evaluation/frozen-protocols/trialqa-protocol.json), and [executed source archive](public-evaluation/frozen-protocols/trialqa-source.zip). All 49 archived source files matched their frozen hashes; the pinned question/record hashes and all published source quotations also passed verification. The independent reporter and runtime adapter have **17 passing regression tests**. Original questions, reference answers, trial records and full execution journals remain in local ignored storage.
