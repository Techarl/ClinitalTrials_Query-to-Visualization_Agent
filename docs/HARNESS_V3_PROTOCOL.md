# Harness v3: frozen transfer and a separate online mechanism diagnostic

Protocol written before paid pilot calls. The first stage is a single development task under a **$1 hard ledger cap**. Formal runs require a cost review after this pilot; no formal result is implied by this protocol.

## Questions and population

Does a public-feedback procedure editor improve execution, and does a development-learned experience bank help previously unseen disease groups? Twenty development episodes span all twenty development templates and the three development disease groups. Forty distinct heldout episodes contain four tasks per each of ten heldout disease groups. Selection rotates four consecutive templates across the twenty heldout templates, yielding two occurrences of every template. Selection reads only split, family, disease group and ID, never expected answers or observed scores. The prototype specifies two fixed orders: the selected list and its reversal. After the pilot cost review, the main study freezes **one order, forty distinct formal questions**. A second order is not funded or launched. If later separately authorized, it would be a repeat of the same questions, not forty new questions.

`protocol.json` fixes every episode ID, order, group count, data manifest hash, model settings and source hashes. A paired source ZIP preserves the exact implementation. Frozen source records and the existing deterministic statistical verifier are shared across arms. Because source identity is strict, changing execution code requires a new output directory; resuming a changed framework is rejected.

## Main frozen-transfer arms

1. Strong production H0: unchanged current prompt, tools, context policy and optional on-gap skill access. Production built-in procedures remain available and are explicitly engineering seeds.
2. PPC without learned memory: two independent editors, candidate execution, composition execution and final execution. Public first-turn probes protect against future-turn leakage. Production built-ins remain available.
3. PPC with learned memory: identical pipeline, plus the development checkpoint. Each formal task receives its own copy of this **read-only incoming bank**. Lessons generated during formal execution are logged but never passed to the next task.
4. USD-matched retry/selection: the same H0 is the first candidate; additional attempts receive the actual/reserved cost of PPC without memory, including editors, unselected candidates and failures. Every additional API request must fit a conservative reservation within the remaining allowance. Selection uses public completion/error/call/token/cost quality, never hidden correctness. At most twenty retries are permitted; unused residuals are reported. This is a matched spending allowance, not a promise of identical realized spending.

The common H0 is executed and charged once in the shared ledger, then included in each arm's interpreted total. Both incremental and common costs are reported. All attempted calls retain either recorded usage costs or conservative unknown-usage reservations; raw input/output tokens and call counts accompany USD totals.

Development executes H0 plus PPC memory sequentially for twenty tasks. Public lessons include unsuccessful edits, whose negative flag prevents activation but permits reading. A checkpoint after each task reconstructs the same incoming bank on restart. Component adoption depends solely on public execution behavior, not hidden grader results. The frozen final bank and its hash are preserved.

## Mechanism and online diagnostic

A separate development-only two-task stream compares proactive experience access with disabled experience tools. It also runs online PPC accumulation, keeping its history separate from the formal frozen-transfer experiment. `on_gap`, `proactive`, and `disabled` are explicit runtime policies. The proactive instruction is an engineered policy modification, not itself an automatically learned benefit.

Reports distinguish editor-retrieved lesson IDs, editor-declared `source_experience_ids`, executor `read_skill` events and executor `apply_skill` events. Executor IDs are classified as learned entries or engineering built-ins. A read or activation establishes that the mechanism operated, not that it caused an accuracy or efficiency benefit. If no positive learned component is activated, that negative result must be reported. The diagnostic is not an independent efficacy benchmark.

## Evaluation and pilot decision

Hidden correctness grades are computed for reporting, never supplied to editors, adoption, retry selection or bank eligibility. Report per-episode paired outcomes, per-family counts, unique questions, the single funded order, full cost, latency and mechanism counts. Interpret accuracy differences cautiously with only forty unique formal tasks. Do not turn order repetitions or deterministic mutation tests into independent question counts.

The first paid pilot runs all four arms on one development task. Forecast twenty-task development plus forty formal tasks in one and two orders from measured all-arm cost, explicitly warning that a single-step pilot underestimates long dialogues. The original planning ceiling was $40. The completed pilot and existing dialogue/dependency costs forecast $47.47-52.43 for twenty development tasks plus forty formal tasks in one order. The coordinator therefore allocated a new **$55 hard main-study ceiling**, within the project-wide $100 budget. Only development and mechanism work are released; formal execution remains explicitly held. The isolated combined pilot budget stays at most **$1**. Before starting formal runs, review the forecast and preserve forty unique tasks before purchasing a second order. Any changed scale is frozen before formal result inspection.

The original `main01` command path is archived after an infrastructure stop and must not be resumed. The active sequence is capsule02 development, a separate empty-bank online diagnostic, then the explicitly released four-worker formal driver. The [frozen formal protocol](public-evaluation/harness-v3-archives/formal-protocol.json), [budget gate](public-evaluation/harness-v3-archives/formal-budget-gate.json), and [launch receipt](public-evaluation/harness-v3-formal-launch.json) preserve its settings and release gates.

The immutable machine manifest and raw ledger, rather than this prose, identify a particular completed run. At the initial protocol stage no mechanism or effectiveness result has yet been observed.

## Pilot outcome and main freeze

The first cost pilot completed 85 ledger requests at $0.7068055 actual-or-reserved cost, including one unknown-usage reservation. H0 used five model calls; both selected PPC procedures used four calls and passed the independent hidden audit, but the incoming memory bank was empty. This is one development question, not evidence of transfer. A separate proactive/disabled mechanism probe uses the resulting public lesson and the same $1 combined pilot ledger.

The initial pilot data enumeration missed nested snapshot paths; a dated supplemental receipt preserves those hashes. The **fresh main protocol** fixes this before any main execution by hashing every JSON source file recursively, including raw studies and snapshot manifests. The main ledger also records per-request durations. Budget-denied zero-request retry loops are stopped immediately in the main driver; all pilot attempts remain archived.

Progress reports retain all planned IDs, incorrect/failed evaluations and unrun tasks. The formal CLI additionally requires `--release-formal`; setting a budget ceiling does not itself authorize that phase.

The completed combined pilot contains **103 request attempts, $0.8437521 actual-or-reserved**, including the original one unknown-usage reservation. The simple-task proactive/disabled pair both had zero experience-tool events. A separate preregistered, explicitly engineered compound-task instruction caused one search, one read and one activation of learned entry `skill_c430bdffa19657865bcd82f3`; both requested analyses passed the hidden report-only audit. That probe used eight calls and $0.0705971. It proves the real-model search/read/apply path is connected, not that ordinary execution chooses it autonomously or improves from it. The zero-trigger result is retained alongside it.


## Infrastructure restart and bounded formal concurrency

Main01 was stopped with two independently recorded `PermissionError` receipts; its 74 attempts and $1.0346569 actual-or-reserved cost remain in the experiment accounting. The original records did not retain the operating-system filename, so a ledger replacement lock is a suspected cause, not a proven stack diagnosis. The unchanged production source continues to serve other studies. Capsule02 applies only a bounded retry around atomic file replacement and records secret-free OS error details. A same-prompt, same-tool infrastructure smoke passed in five calls/$0.0207532; this is not proof that every infrastructure failure is eliminated.

Development remains sequential, twenty planned tasks with public-feedback accumulation. The separate online diagnostic starts from an empty bank on `ep_dd66d2a67715afd0` and `ep_89414e78c31e40d1`; the machine preregistration preserves their original order. It never updates the formal bank. Its proactive/disabled executions receive the incoming prior-task bank, and not the lesson generated for that task.

The preserved two-worker preparation passed eight synthetic checks. Before any formal outputs, the coordinator authorized a new **three-worker capsule04**, keeping tasks, arm definitions, models, prompts, candidate trials and scoring unchanged. Nine synthetic tests cover simultaneous hard-cap reservations, unknown-usage retention, thread-local scope attribution, PPC event isolation, resume, independent bank copies, bounded admission, report-write failures and deadline-aware graph import locks. A real Neo4j check passed three concurrent reads/imports of one synthetic snapshot and three distinct synthetic snapshots; only those generated fixtures were removed afterwards. No paid formal calls were used in these checks.

Each task has an independent SQLite/output directory and its own copy of the frozen learned bank. Neo4j projections are shared by immutable dataset identity; they are not per-task mutable graph namespaces. The formal driver serializes first import of the same dataset within its process and honors the original deadline. Different datasets can import concurrently. Formal execution waits for the Core/TrialQA graph producers to close, avoiding simultaneous cross-process first imports. It may overlap NLI, which does not use Neo4j; an unfinished NLI study reserves its complete $20 ceiling in the coordinator’s aggregate budget gate. Global reservations and persistent ledger updates share a lock, while accounting scopes are thread-local. Completion order may differ from the frozen admission order and does not affect the read-only incoming bank.

The source archives and preparations are recorded in `docs/public-evaluation/harness-v3-concurrency-preparation.json`. These earlier preparations preceded formal release. Before release, the formal driver freezes the final development bank, exact forty tasks, worker count, source hashes and immutable prior-cost receipts. Supply only the final cumulative main02 development-plus-online receipt alongside main01's stopped receipt; supplying both overlapping main02 receipts would double count. Pilot and smoke costs remain separately included in the project-wide ledger. Progress must retain failed and unrun planned tasks.


## Closed records and reproducibility

The completed development and independent online stages are preserved in `docs/public-evaluation/harness-v3-archives/index.json`: exact frozen source/protocol pairs, the unchanged development bank, full reports, four nonoverlapping closed ledgers and 642 public execution/proposal/checkpoint JSON files. Archive hashes, membership checks and configured-credential scans were verified. The trace archive explicitly excludes runtime SQLite databases and duplicated raw snapshots; the public frozen ClinicalTrials.gov source archive is distributed separately. This archive does not imply that the formal stage has run.

Development completed all twenty planned tasks with 643 requests and $7.3742907 actual-or-reserved cost. Frozen report-only correctness was 20/20 for H0 and 19/20 for PPC memory, with 154 versus 133 final execution calls; those final-call counts exclude the editor/probe overhead included in the full ledger. Editor retrieval/declarations were nonzero, while executor skill read/activation counts remained zero. The bank has nineteen publicly adopted entries. These are development observations, not heldout transfer evidence.

The separate two-task online stream added 100 requests/$0.9943497. Proactive and disabled arms were each 2/2 correct with fourteen final execution calls, and neither triggered executor experience reading or activation. The final online bank contains one negative entry and no positive component. One public adoption flag selected an empty procedure; this is a no-op, not a reusable learned modification. The completed cumulative development-plus-online receipt costs $8.3686404 and must not be added to the development-only receipt again. Detailed summaries retain all these distinctions.


Before any formal execution, the user explicitly increased concurrency to **four workers**. A new capsule05 preserves the three-worker preparation and changes only bounded worker admission. Nine synthetic tests and real four-thread same/different-snapshot Neo4j checks passed without model calls. The forty tasks, four arms, models, prompts, scoring and budget ceiling remain unchanged. The final formal protocol identifies capsule05 and four workers; earlier preparation records do not represent paid formal runs. See `docs/public-evaluation/harness-v3-four-worker-preparation.json`.


## Formal launch — 2026-11-01

After Core300 and TrialQA closed and their costs were reconciled, the full forty-task/four-arm formal study started with four workers. Its exact source, frozen identity and start receipt are preserved in [the launch record](public-evaluation/harness-v3-formal-launch.json) and the archive index. Main-stage Harness ceiling: $53.33, including main01 and cumulative main02 ($9.4032973), leaving $43.9267027 for formal execution. Pilot/smoke costs are outside that main ceiling but included in the project gate. The gate reserves unfinished NLI at its entire $20 cap and $0.50 for a possible fresh demo; its conservative project upper bound is $99.999400907.

The formal run is ongoing at this checkpoint. No partial correctness scores are used for tuning or presented as final results. The independent reporter will preserve all forty planned tasks, original grades, dependency-linkage audit, per-arm and shared costs, failures and unrun cases.

## Desktop crash and recovery amendment — 2026-11-01

The desktop crash interrupted the producer after six complete task records. All matching producer processes were confirmed absent before recovery. The interrupted ledger contains 704 attempts and $5.7414393 actual-or-reserved cost, including one unknown-usage reservation of $0.1344125. No reservation was released. An immutable backup preserves 932 original records, including the four interrupted run databases; their SQLite integrity checks passed. The original capsule05 source, model settings, bank and protocol identity were revalidated before release.

The original retry loop was not safe to restart blindly: its accumulated budget check could stop after the first cached attempt and omit later already-completed candidates. The external [recovery wrapper](../scripts/resume_harness_v3.py) therefore returns completed task/retry records unchanged and reconstructs every saved candidate in an incomplete retry loop before admitting further work. It retains the original twenty-attempt limit, per-arm USD allowance, public-only selection and global cap. Six no-model tests cover cache reuse, candidate recovery, unknown reservations, pending-run closure, gaps and the attempt limit.

This is a disclosed amendment to the execution/recovery layer, even though the archived inference source and original protocol identity remain unchanged. The wrapper has its own source hash and launch receipt in [the recovery record](public-evaluation/harness-v3-recovery.json) and [restart receipt](public-evaluation/harness-v3-recovery-launch.json). No hidden grades informed the amendment. Original run deadlines and already-recorded failed/partial states remain unchanged; interruption-affected outcomes and latency must be interpreted with this qualification. The full forty-task/four-arm run resumed with four workers at 18:02 local time, using the same remaining study ceiling.
