# Review fixes and public evaluation expansion

This work continues from `9e107fe`; historical reports are retained. The submission is prepared in an independent release worktree so ongoing frozen studies can continue unchanged.

## Submission source changes

The release source now includes all five tested backend corrections: inferred scope and explicit-null preservation, comparison-member retention, task execution cache identity, contiguous PubMed-page quotation validation, bounded atomic-write retries, and portable text-source provenance/export. The nine former expected-failure assertions are normal regressions in this worktree. Frozen study results still describe their original execution code, not these later corrections. Release source checks passed **919 Python tests, 2 optional-data skips, 0 xfails**, fourteen frontend tests, build, Ruff, nine schema comparisons, and all V1/V2 offline examples. Actual HTTP and browser provenance checks also passed with zero model calls. [Source receipt](release/source-validation.json), [HTTP receipt](release/http-acceptance.json), [browser receipt](release/browser-acceptance.json). Fresh ZIP installation acceptance follows after the fixed source commit; earlier checkpoint figures below are historical.

## Checkpoint pushed on 2026-11-01

Checkpoint `3b33c98` is pushed. Its [completed CI](public-evaluation/checkpoint-3b33c98-ci.json) reports **865 passed, 2 skipped, 9 xfailed** with real Neo4j; fourteen frontend tests and the build passed. The nine known expected failures belong to that checkpoint and are graduated to normal assertions in the release source.

| Workstream | Completed evidence | Remaining |
|---|---|---|
| Core300 | 300 episodes / 420 turns; 295/300 overall, 196/200 heldout; all thirty dependency cases audited with no grade changes | Final delivery-code checks remain separate from the frozen study |
| TrialQA | 120 questions × two conditions; 237 post-run judgments; heldout cited correctness 64/79 in both conditions | No further formal calls planned |
| NLI4CT | Frozen 500-question / 200-answer protocol and source archive | Formal execution and complete report are running/pending |
| Harness v3 | Twenty development tasks and two online diagnostics; full forty-task/four-arm formal study launched at four workers | Formal comparisons, costs and post-run audit |
| UI and examples | Current-history selection fix; portable-source UI; four curated groups/five turns; credential-free read-only viewer | Final release acceptance |
| Packaging | Nested ZIP/gzip credential scanning and independent archive verifier tested | Fixed release commit, package, fresh extraction/install/demo |

See [Core results](CORE300_RESULTS.md), [dependency audit](CORE300_SELECTION_AUDIT.md), [TrialQA results](TRIALQA_PROTOCOL.md#completed-formal-study), [Harness launch](public-evaluation/harness-v3-formal-launch.json), and [recorded demo](DEMO.md). The final Harness main ceiling is $53.33 including its earlier main-stage costs; its newly available formal allowance is $43.9267027. The project gate reserves the entire $20 NLI ceiling and a $0.50 demonstration allowance, keeping the conservative new-phase bound at $99.999400907. These are budget reservations, not final expenditure or invoices.

Checkpoint `72d95bd` was pushed to the private repository. Its [GitHub CI](https://github.com/Techarl/ClinitalTrials_Query-to-Visualization_Agent/actions/runs/41283144143) is green with **741 passed, 2 skipped and 9 xfailed**, as independently checked in the submission-readiness review. The expected failures expose known scope/scorer gaps; green CI does not mean those gaps are fixed. Earlier checkpoint counts below describe earlier test sets and are not the final release tally.

## Completed mechanisms

- Comparison publication/finalization requires every medicine exactly once, including an explicit empty scope, and binds labels to identities.
- Bounded `read_source` pages enter the next model context, with immutable source handles, pointers, offsets and source hashes. PubMed returns readable abstract handles.
- Quoted final findings and qualitative text-answer artifacts require source text actually read during the run. Quote integrity is distinct from semantic correctness.
- Inferred scalar/list scope errors can be repaired using verbatim original-request evidence; structured user constraints remain binding. Superseded scope/results are retained in audit history.
- Durable last-call model decisions replay before checking the remaining call allowance. Cancellation is checked after planner return and before controls. Obsolete result attempts can be superseded.
- Explicit bounds on the plotted date field produce zero endpoint buckets, including all-empty populations. Independent verifier checks the full bucket set.
- Atomic submission keys prevent duplicate logical requests. History queries filter by session before the result limit.
- Executor tools search/read/apply versioned skills. Negative lessons are readable but cannot be activated. Activation is logged separately from demonstrated benefit.
- Harness memory adoption uses public feedback only. Cached PPC replays restore the public bank checkpoint; model/code/config identities are frozen. Token/cost efficiency and fuller framework context are being added to the next protocol.
- Research execution fingerprints bind model, endpoint, reasoning, budgets, inputs, code and dependencies. Partial episodes resume persisted runs; failed/missing episodes remain in denominators.
- Eligibility extraction supports plain prose, hyphens, escaped bullets and inline headings. Historical corpus results are not rewritten.

## Independent checks

- The complete isolated release candidate (exact `3b33c98` plus the five prepared backend patches) passed **907 unique Python tests, 2 data-unavailable skips and 0 xfails**, combining 898 offline passes with nine isolated Docker Neo4j passes. Fourteen frontend tests, build and Ruff also passed. [Receipt and reproducibility archive](public-evaluation/release-candidate-tests/receipt.json) retain exact patches, graduated tests, import attestations and test logs. Root code is still frozen for NLI, so these results do not replace final root/ZIP validation. The candidate reused locked dependencies and did not constitute a fresh installation.

- A live Neo4j checkpoint passed 160 tests before the subsequent source-answer and submission changes.
- An additional 500 deterministic mutation cases plus 21 SQL tests passed in targeted groups. They are not Agent episodes.
- SQL checks independently rederive selected record membership from the complete parent snapshot, category counts and shared-drug edge weights.
- UI fixes passed desktop/mobile browser acceptance: submission protection, grouped history, readable phase labels, matching evidence, drawer focus/Escape, and qualitative text rendering. See [UI evidence](UI_REVIEW_FIXES.md).
- A later checkpoint passed 718 native tests and 738 Linux tests with real Neo4j; Linux also recorded two strict expected failures from a new independent cross-check. These are intermediate counts, not final release results. [Integration receipt](public-evaluation/integration-checkpoint.json).
- MCP stdio and HTTP each exercised actual chart/evidence tools, session token isolation and cancellation without model calls. The final HTTP run used verified PubMed cached receipts after a live upstream HTTP error.
- Cross-check found incomplete inferred-date/list correction and an anchor/top-N grading loophole. Unapplied patches and shadow tests preserve the currently running study's source version. A complete-population post-run audit will report original and corrected grades separately.
- Submission-readiness review additionally found that a goal change could reuse an earlier tool failure and that selecting the already-current history version could clear the result. The history fix is in the frontend and browser-verified. Five backend patches passed 51 tests and actual HTTP/browser checks in an isolated candidate; they remain unapplied to the root source while the frozen NLI execution runs. Candidate acceptance is not final root-release acceptance.

## Public data and pilots

- NLI4CT official archive pinned at `8e8e5fc506b916984011873403b9ea5ed6dd96c2`: 1,700 train / 200 dev / 500 test. Runtime inputs and labels are separate files.
- TrialQA official dataset pinned at `3678e7e38748477d0606407468db9cd7e2f2e667`: 120 questions, 102 unique trial records downloaded. All source URLs are CTgov record URLs; current-source/benchmark-version differences require separate reporting.
- Dataset files and answers remain under ignored `data/`; source links, checksums and aggregate reports are distributable without copying the raw gated dataset into the repository.
- Six NLI development questions, five arms: 171 calls, approximately $0.08954. No failed arms; all five answer arms scored 4/6. This is a mechanism/cost pilot, not evidence of superiority.
- Six core development episodes: 6/6, approximately $0.44139, including dependent sponsor cohorts and three-turn date-axis changes.
- A 500-question NLI formal run with the same preselected 200 answer cases per arm is underway. An initial serial attempt was stopped for bounded parallel execution; retain and account for both ledgers. Do not merge its duplicate predictions into formal scores.
- 300 core episodes generated from 15 predeclared complete source populations: 60 development / 40 calibration / 200 heldout; 120 single-step, 80 compound/dependent, 60 dialogue, 40 boundary. Templates recur; this is not 300 independently authored questions.

## Still required before delivery

- TrialQA and Core300 have complete reports and independent audits. Finish NLI and Harness with equally complete accounting; retain TrialQA's $10.280054475 and Core300's $14.2884179 estimated-or-reserved formal costs, with pilots separately accounted.
- Backend follow-ups are applied in the independent release worktree. Finish its integration and package checks while preserving the original study directory.
- Finish regression/integration/UI acceptance after all edits; record exact code/config versions for each study.
- Report paired per-question results, input-only strata, confidence intervals and source/version limitations. Do not select favorable test subsets.
- Preserve previous negative results and distinguish manual procedures, explicit skill use, public-feedback adoption and measured downstream benefit.
- Final docs, schema exports, clean-container checks, credential-safe package, commit and push.
