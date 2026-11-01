# Public datasets and frozen execution studies

These studies answer different questions. Their scores must not be pooled into a single Agent success rate. Official benchmark inputs/answers remain under ignored `data/`; downloadable public CTgov core snapshots are archived separately.

The [frozen protocol/source index](public-evaluation/frozen-protocols/index.json) preserves the exact NLI4CT, Core300 and TrialQA execution code independently of later delivery fixes. Each source ZIP was checked against every recorded file hash. These archives contain protocols and code, not raw NLI4CT or gated TrialQA data. Archive availability does not imply that all planned executions have finished; final results and completion counts are reported separately.

| Study | Population and split | What it measures |
|---|---|---|
| NLI4CT | Official 1,700 train / 200 dev / 500 test; the same fixed 200 test statements receive answers in every arm | Candidate evidence selection, complete evidence coverage, and a fixed model's entailment/contradiction decisions |
| TrialQA | All 120 questions, grouped by shared trial identity into 19 development / 22 calibration / 79 heldout | Reading and quoted answers with supplied trial identities versus keyword retrieval over 102 frozen trial records |
| Core300 | 300 template-derived episodes / 420 turns; 60 development / 40 calibration / 200 heldout across 15 disease–medicine groups | Population constraints, graph-dependent analyses, charts, evidence, follow-ups and defined limits |
| Harness v3 | 20 development episodes; planned 40 unique heldout episodes in one order | Public-feedback procedure editing and transfer of a frozen learned experience bank, compared with H0 and a matched USD retry allowance |

The 500 deterministic mutation cases are software checks, not 500 additional natural-language Agent tasks. Repeated templates, shared trial records and repeated orderings do not create independent samples. Public test material may have appeared in model training; this project separates development from test answers but cannot establish model pretraining independence.

TrialQA's [completed formal report](TRIALQA_PROTOCOL.md#completed-formal-study) covers all 240 attempted executions. On the 79-question heldout split, both supplied-source and frozen-retrieval conditions produced 64 citation-supported correct answers under the post-execution automated judge. The paired analysis does not establish a difference between the conditions. Full-source quotation checks, incomplete runtime states, source-version differences and all costs are reported separately.

Core300 has also completed: [295/300 overall, 196/200 heldout](CORE300_RESULTS.md). The [additional dependency audit](CORE300_SELECTION_AUDIT.md) covered all thirty dependency tasks and changed no original episode grades. Five failures remain included, without selective reruns. NLI4CT is still running. Harness completed development and online diagnostics; its full forty-task, four-arm formal study has started with four concurrent workers. The [immutable launch receipt](public-evaluation/harness-v3-formal-launch.json) is not a final result.

## Reproducible inputs

Install the normal locked service dependencies first. Optional research dependencies can be installed in a separate Python 3.12 environment:

```powershell
python -m pip install -r requirements.lock -r experiments/requirements-rerank.txt -r scripts/requirements-public-data.txt
python -m scripts.download_public_benchmarks
python -m evaluation.public_datasets
```

The downloader verifies exact SHA-256 values from `evaluation/public_datasets.py`. TrialQA requires an approved Hugging Face account and its read-only `HF_TOKEN` in the local `.env`; it is sent only to the Hugging Face download endpoint. Do not commit the token or gated files. The preparation step keeps runtime inputs and scoring labels in separate files, preserves the official raw download, and audits source URLs.

NLI4CT is pinned to official revision `8e8e5fc506b916984011873403b9ea5ed6dd96c2`; TrialQA to `3678e7e38748477d0606407468db9cd7e2f2e667`. Dataset checksums and licenses/redistribution limitations are recorded in the local `dataset-manifest.json`. No new model training or fine-tuning is performed.

For the exact core populations, extract [core300-sources.zip](public-evaluation/core300-sources.zip) into the repository root and verify its per-file `SHA256-MANIFEST.json`. Its [archive receipt](public-evaluation/core300-source-archive.json) records the ZIP hash. These are public CTgov records and this project's task templates, not NLI4CT or gated TrialQA material. Fetching current records with `python -m evaluation.core300 --download` creates a **new data version** and may produce different results; it does not reproduce the archived snapshot merely by using the same query.

## Execution boundaries

Runtime receives only the current/past request and allowed source observations. Hidden answers are used for scoring, not tool selection, procedure adoption or experience eligibility. TrialQA's post-run generic-model judge is an automated assessment, not clinician adjudication. The supplied-source condition gives record identities/handles, never the golden answer passage. Its retrieval condition searches a frozen 102-record collection and is explicitly not unrestricted internet retrieval.

All model attempts reserve budget before invocation. Missing usage remains charged conservatively as an unknown reservation. Estimates are not provider invoices. Failed and unexecuted planned cases stay in denominators. Immutable study identities include model, endpoint, reasoning, budgets, data and source hashes; a changed experiment requires a new output directory. Frozen source ZIPs identify the executed code even when later delivery code receives additional corrections.

The NLI formal study retains the original ranking client implementations for comparability of quality. Its timing is therefore not a controlled model-speed experiment. Use the separate [Jev latency control](JEV_LATENCY_CONTROL.md), and read the [fairness assessment](JEV_FAIRNESS_REVIEW.md). No quality threshold is tuned on formal test outputs.

### NLI readout checks registered before formal outcome access

The frozen NLI identity is `476bb042ff5273e16975f4e638aef8ff0c85e62877ca08a6bea540c038ea86ed`. All selectors operate within the supplied trials, not a whole-library retrieval collection. Hybrid, MiniLM, Jev, and Luna share the same RRF top-24 candidate set and greedy 1,024-token packing procedure. This cap counts `cl100k_base` tokens in the selected passage JSON, including IDs and trial-role metadata; it excludes the statement, instructions, ranking calls, and answer output. Packing skips a candidate that does not fit and tries subsequent candidates, with no learned relevance threshold. The full-text control receives the entire supplied source and is intentionally exempt from this information budget.

The five arms answer the same frozen 200 statements using requested `gpt-6.1-sol`, reasoning `medium`, with a 2,048-output-token cap. Jev and Luna score the same candidate fields with the same relevance rubric; Luna uses `gpt-6-luna` with `low` reasoning and a 512-output-token cap. The dedicated control is the pinned `cross-encoder/ms-marco-MiniLM-L6-v2`. It also receives the candidate JSON, but uses the original statement as its query rather than the instruction wrapper supplied to Jev/Luna. These are fixed system configurations, not a claim that every model received equally extensive tuning. The rubric's criterion-oriented wording is retained for this formal run; changing it requires a separate train/dev-selected protocol.

The reporter retains the 500-case main and 200-case answer denominators, including failures, and validates observed candidate sets, selection lengths, and citation-ID membership flags before publication. Complete evidence coverage refers to material retained in context; label accuracy refers to the official entailment/contradiction answer. Neither alone measures semantic support of the answer's citations. An empty citation list passes the frozen membership check vacuously, so supplementary nonempty-citation and correct-label-with-nonempty-valid-ID rates are reported separately without changing the original score.

Direct Jev–Luna and Jev–MiniLM paired cluster-bootstrap contrasts were added to the reporting plan on 2026-11-01 before this reviewer accessed formal results. They supplement the existing comparisons against hybrid/full; the 113 shared-trial components, fixed seed, and 4,000 resamples are unchanged. These and all strata remain exploratory, with no multiplicity correction or retrospective choice of a favourable subset. No partial formal grades or live cost ledger were used to make this change.

Costs retain unknown-usage reservations and all attempted calls. Explicit NLI arm tags support separate ranking-plus-answer costs; embedding and unattributed attempts remain shared. Identified embedding counts may exclude unknown calls with no metadata, while those calls remain in total costs. Local CPU cost is not priced. Identical ledger contents alone cannot prove duplicate billing records: independent equal-content ledgers are retained and flagged, while only aliases of the same physical input file are deduplicated. Ranking latency excludes embedding prewarm and initial retrieval and may include resumed cache reads; it is not an end-to-end or controlled-provider latency result.

## Protocols and complete reporting

- [Core300 protocol](CORE300_PROTOCOL.md): exact population/split, independent SQL checks and their coverage limits.
- [TrialQA protocol](TRIALQA_PROTOCOL.md): source drift, two reading conditions, grading and paired analysis.
- [Harness v3 protocol](HARNESS_V3_PROTOCOL.md): public adoption, negative lessons, frozen transfer and budget accounting.
- [Registered NLI input strata](public-evaluation/nli-input-strata.json): includes statements whose full context already fits the selection budget, section types, and shared-trial clusters. Registration timing is explicit.

The NLI and Core300 reporters refuse partial formal results:

```powershell
python -m scripts.report_public_nli
python -m scripts.report_core300
python -m scripts.audit_core300_selection
```

For NLI, the explicit final command is:

```powershell
python -m scripts.report_public_nli --study data/nli4ct-official-test-v2 --inputs data/external-benchmarks/nli4ct-test-inputs.json --strata docs/public-evaluation/nli-input-strata.json --pilot data/public-pilot-nli4ct-v1/ledger.json --aborted data/nli4ct-official-test-v1/ledger.json
```

Required final inputs are the complete study `report.json` (2,500 terminal case-arm records), its unchanged `protocol.json`, `protocol.source.zip`, and final `ledger.json`, plus the input-only dataset/strata and the two historical ledgers above. The reporter does not need to open the official gold file or per-case intermediate output directories. If `report.json` is absent it exits with code 2 before reading partial data; a present but incomplete/mismatched report is rejected. Run only after the formal worker has finished. Outputs are `docs/NLI4CT_RESULTS.md` and `docs/public-evaluation/nli4ct-{summary,cases,accounting}.json`. This command neither resumes nor reruns model execution, and does not overwrite the original study outputs.

The selection audit is an additional full-population check prompted by a synthetic scorer counterexample. It preserves original grades and separately binds the requested anchor/top-N to the actual selection receipt and trend scope lineage. It does not selectively rerun failed questions or overwrite the initial experiment.

Detailed results are published only after planned execution completes. Historical small passage-selection and Harness reports remain separate observations with their original corpora and limitations.
