# VNext execution studies — 2026-11-01

This stage tests actual tools, Neo4j calculations and source evidence. It does not overwrite the historical planning-only studies in [EXPERIMENTS.md](EXPERIMENTS.md). Machine-readable totals are in [summary.json](vnext/results/summary.json); [index.json](vnext/results/index.json) records SHA-256 hashes for every trace archive.

## End-to-end challenge

The final 72 authored episodes passed all semantic and complete-source checks: development 21/21, calibration 16/16, heldout 35/35. They used 460 model calls, an estimated $2.324093, and mean episode wall time 46.03 seconds. The model was GPT-6.1 Sol with the configured reasoning effort; exact settings, code hashes, dependency hash and frozen-source hashes are preserved inside [evaluation-final.zip](vnext/results/evaluation-final.zip).

The split separates disease/drug groups, not capability templates. It covers phase/status/country, histogram, scatter, four date fields, month precision, date bounds, relationships, dependent sponsor selection, follow-up/clear operations, same-drug clarification, unsupported requests, literature and AND/OR/comparison. This is a small authored challenge, not an estimate of open-ended user accuracy. Automatic question-quality checks are weaker than a human review. Real browser testing subsequently found a dependency-link omission after entity clarification that these 72 episodes did not cover; that issue has a separate regression and live acceptance record.

The initial 66 episodes and six added logic episodes remain separate in [evaluation-initial.zip](vnext/results/evaluation-initial.zip) and [evaluation-logic.zip](vnext/results/evaluation-logic.zip). Do not combine their earlier-version results into the final run.

### Scoring correction

An injected unrequested country restriction exposed an oracle defect: the first scorer checked only explicitly listed expected fields, allowing additional filters. The corrected scorer checks default-empty restrictions, comparison scope and unrequested date bounds as well. All saved executions were regraded offline, without paid calls or replacing original outputs. The final 72/72 score is unchanged; four injected-country controls change from apparent success to failure. Both grades remain in each archive. Historical traces containing ignored `clear_fields` names receive an explicitly recorded compatibility mapping, without changing their effective filters.

## Decision checks and system repair

The judgment study uses 91 cases from 24 grouped episodes: 28 observed plans and 63 schema-valid country/entity/date-axis mutations. There were no observed natural semantic errors in the source sample; synthetic errors are not presented as natural failure rates. Related dialogue turns and mutations stay in the same split. Thresholds were selected on calibration and saved before heldout calls.

| Heldout judgment arm | Correct disposition / 26 | False blocks of 8 valid plans | Unnecessary clarifications |
|---|---:|---:|---:|
| Original composite Jev rule | 6 | 2 | 20 |
| Atomic Jev, full context | 6 | 7 | 0 |
| Atomic Jev, compact context | 0 | 8 | 0 |
| Generic Sol judge, compact context | 25 | 1 | 0 |

Zero missed erroneous executions is insufficient: a checker that blocks almost everything is not useful. Atomic decomposition removes one form of inappropriate clarification but this Jev configuration still overblocks or classifies supported tasks as unsupported. The compact variant does not establish a context-compression benefit. The study cost $0.389909 across 364 judgment calls. Full inputs, assessments, metadata, origin labels and frozen thresholds are in [decision.zip](vnext/results/decision.zip).

We then tested four paired real execution episodes, injecting an extra UK filter into the first proposed task. Rejected clear requests receive internal repair feedback; only genuine missing information should go to the user. Repair is bounded to two attempts.

| Execution arm | Correct / 4 | Model + judge calls | Estimated cost | Mean seconds |
|---|---:|---:|---:|---:|
| Strong planner, no injection | 4 | 26 | $0.106699 | 28.32 |
| Injected error, fixed checks only | 0 | 28 | $0.120409 | 25.85 |
| Injected error + Jev repair | 2 | 39 | $0.113445 | 36.87 |
| Injected error + generic judge repair | 4 | 41 | $0.164026 | 42.86 |

The two Jev failures stopped at the repair limit rather than asking broad confirmation. On one, even a corrected plan received low date-preservation scores despite no requested date restriction. The generic judge repaired all four injected faults, at additional cost and latency. This demonstrates the repair path, not a general accuracy gain or savings over the already-correct strong baseline. The generative repair arm was added as a diagnostic after the first Jev results; its threshold was unchanged and no claim of a fully preregistered four-arm study is made.

The cheap-planner cascade was implemented but **not activated**: Jev calibration failed the predefined false-block/missed-error gate. Model availability is not evidence of cost-effectiveness. [repair.zip](vnext/results/repair.zip) contains every attempt, including unsuccessful repairs and the corrected offline grades. The total ledger ($0.504579) includes planning and all judge calls; no unknown-usage attempt is treated as free.

## Harness with real execution

Two independent empty-memory streams each executed three tasks in opposite orders. Each task starts from strong H0. General/domain editors see the first current-turn public trace, propose bounded procedures or context selection, run actual probes, compose, probe again and execute the complete episode. Future user answers and hidden labels are unavailable to editors and runtime. The tool kernels, evidence, oracle, model and budgets cannot be edited.

| Final execution arm | Correct / 6 | Calls | Estimated final-execution cost | Mean seconds |
|---|---:|---:|---:|---:|
| Strong H0 | 6 | 43 | $0.172438 | 33.25 |
| Matched call-budget retries, selected | 6 | 42 | $0.161868 | 31.16 |
| PPC without memory | 6 | 43 | $0.172574 | 31.20 |
| PPC with memory | 6 | 42 | $0.159322 | 31.23 |
| Fixed manual procedure | 6 | 34 | $0.129623 | 27.79 |

These are final-execution costs, **not total optimization costs**. The complete experiment used 535 calls and $2.334859, including editors, every probe, composer, retries and failures. The matching control matches allocated model-call allowance, not exact dollars; unused residual allowance and all attempts are recorded.

No automatically modified component met the adoption rule. Each stream retained one concrete negative experience; there were zero retrieved/applied/adopted positive components. Context packing did not hit its limit in these tasks, so no measured context-policy benefit is claimed. A separate fixed-budget regression verifies that relevant-versus-recent packing actually changes the observations delivered to the model when the limit is reached.

The manual procedure removes redundant standalone validation after an analysis that already validates internally; finalization still independently validates. It preserved 6/6 quality while reducing final calls from 43 to 34. That is an engineering control demonstrating available efficiency headroom, **not automatic self-improvement**. Candidate date-precision procedures tended to retain or add profiling rather than remove the redundant validation. One fewer call in the unchanged PPC-memory arm is sampling variation, not learned transfer.

Across all 82 baseline/probe/final/retry executions, 80 passed. The two failures were unselected multi-turn retries with insufficient remaining call allowance, not evidence of defective candidate kernels. All attempts are retained. [harness.zip](vnext/results/harness.zip) includes proposals, public probe observations, compositions, experiences, complete turn journals, failures and budget accounting.

## Reproduction and limits

Run commands are in [VNEXT.md](VNEXT.md). `python -m evaluation.rescore_v2 <local-study-directory>` performs independent offline regrading when the local persisted evidence/snapshot stores are available. Trace ZIPs contain full exported responses, state, model/tool journals and events; mutable SQLite/checkpoint stores are excluded. Frozen source records and PubMed receipts are shipped separately in the repository for reproducible new runs. Archive manifests enumerate every included file and its hash.

The six archived studies total 1,955 paid attempts and an estimated/reported $7.724572. This excludes separate browser smokes, the focused dependency regression, earlier project work and account-wide usage. OpenAI figures use recorded short-context standard rates; they are not invoices. Source APIs and Neo4j calls are not counted as model calls. No experiment is enabled by ordinary application startup or CI.

The next requested study is evidence relevance and context selection. It will use a separate frozen corpus and controls; results here do not establish whether Jev is a useful passage reranker.
