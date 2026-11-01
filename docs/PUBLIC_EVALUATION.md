# Public datasets and frozen execution studies

These studies answer different questions. Their scores must not be pooled into a single Agent success rate. Official benchmark inputs/answers remain under ignored `data/`; downloadable public CTgov core snapshots are archived separately.

| Study | Population and split | What it measures |
|---|---|---|
| NLI4CT | Official 1,700 train / 200 dev / 500 test; the same fixed 200 test statements receive answers in every arm | Candidate evidence selection, complete evidence coverage, and a fixed model's entailment/contradiction decisions |
| TrialQA | All 120 questions, grouped by shared trial identity into 19 development / 22 calibration / 79 heldout | Reading and quoted answers with supplied trial identities versus keyword retrieval over 102 frozen trial records |
| Core300 | 300 template-derived episodes / 420 turns; 60 development / 40 calibration / 200 heldout across 15 disease–medicine groups | Population constraints, graph-dependent analyses, charts, evidence, follow-ups and defined limits |
| Harness v3 | 20 development episodes; planned 40 unique heldout episodes in one order | Public-feedback procedure editing and transfer of a frozen learned experience bank, compared with H0 and a matched USD retry allowance |

The 500 deterministic mutation cases are software checks, not 500 additional natural-language Agent tasks. Repeated templates, shared trial records and repeated orderings do not create independent samples. Public test material may have appeared in model training; this project separates development from test answers but cannot establish model pretraining independence.

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

The selection audit is an additional full-population check prompted by a synthetic scorer counterexample. It preserves original grades and separately binds the requested anchor/top-N to the actual selection receipt and trend scope lineage. It does not selectively rerun failed questions or overwrite the initial experiment.

Detailed results are published only after planned execution completes. Historical small passage-selection and Harness reports remain separate observations with their original corpora and limitations.
