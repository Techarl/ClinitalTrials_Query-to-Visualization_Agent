# Review fixes and public evaluation expansion

This work continues from `9e107fe`; historical reports are retained. Current work is not yet a new final release.

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

- A live Neo4j checkpoint passed 160 tests before the subsequent source-answer and submission changes.
- An additional 500 deterministic mutation cases plus 21 SQL tests passed in targeted groups. They are not Agent episodes.
- SQL checks independently rederive selected record membership from the complete parent snapshot, category counts and shared-drug edge weights.
- UI fixes passed desktop/mobile browser acceptance: submission protection, grouped history, readable phase labels, matching evidence, drawer focus/Escape, and qualitative text rendering. See [UI evidence](UI_REVIEW_FIXES.md).
- A later checkpoint passed 718 native tests and 738 Linux tests with real Neo4j; Linux also recorded two strict expected failures from a new independent cross-check. These are intermediate counts, not final release results. [Integration receipt](public-evaluation/integration-checkpoint.json).
- MCP stdio and HTTP each exercised actual chart/evidence tools, session token isolation and cancellation without model calls. The final HTTP run used verified PubMed cached receipts after a live upstream HTTP error.
- Cross-check found incomplete inferred-date/list correction and an anchor/top-N grading loophole. Unapplied patches and shadow tests preserve the currently running study's source version. A complete-population post-run audit will report original and corrected grades separately.

## Public data and pilots

- NLI4CT official archive pinned at `8e8e5fc506b916984011873403b9ea5ed6dd96c2`: 1,700 train / 200 dev / 500 test. Runtime inputs and labels are separate files.
- TrialQA official dataset pinned at `3678e7e38748477d0606407468db9cd7e2f2e667`: 120 questions, 102 unique trial records downloaded. All source URLs are CTgov record URLs; current-source/benchmark-version differences require separate reporting.
- Dataset files and answers remain under ignored `data/`; source links, checksums and aggregate reports are distributable without copying the raw gated dataset into the repository.
- Six NLI development questions, five arms: 171 calls, approximately $0.08954. No failed arms; all five answer arms scored 4/6. This is a mechanism/cost pilot, not evidence of superiority.
- Six core development episodes: 6/6, approximately $0.44139, including dependent sponsor cohorts and three-turn date-axis changes.
- A 500-question NLI formal run with the same preselected 200 answer cases per arm is underway. An initial serial attempt was stopped for bounded parallel execution; retain and account for both ledgers. Do not merge its duplicate predictions into formal scores.
- 300 core episodes generated from 15 predeclared complete source populations: 60 development / 40 calibration / 200 heldout; 120 single-step, 80 compound/dependent, 60 dialogue, 40 boundary. Templates recur; this is not 300 independently authored questions.

## Still required before delivery

- TrialQA pilot passed 2 questions in both arms; all 120 questions × two arms now run under a frozen protocol. Finish core300, NLI, TrialQA and Harness with complete failure/expense accounting.
- Apply prepared scope/scorer and infrastructure follow-ups after immutable executions finish; rerun affected checks and explicitly distinguish executed study code from final delivery code.
- Finish regression/integration/UI acceptance after all edits; record exact code/config versions for each study.
- Report paired per-question results, input-only strata, confidence intervals and source/version limitations. Do not select favorable test subsets.
- Preserve previous negative results and distinguish manual procedures, explicit skill use, public-feedback adoption and measured downstream benefit.
- Final docs, schema exports, clean-container checks, credential-safe package, commit and push.
