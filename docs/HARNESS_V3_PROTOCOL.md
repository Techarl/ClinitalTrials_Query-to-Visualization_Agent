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

Hidden correctness grades are computed for reporting, never supplied to editors, adoption, retry selection or bank eligibility. Report per-episode paired outcomes, per-family counts, unique questions, both order results, full cost, latency and mechanism counts. Interpret accuracy differences cautiously with only forty unique formal tasks. Do not turn order repetitions or deterministic mutation tests into independent question counts.

The first paid pilot runs all four arms on one development task. Forecast twenty-task development plus forty formal tasks in one and two orders from measured all-arm cost, explicitly warning that a single-step pilot underestimates long dialogues. The original planning ceiling was $40. The completed pilot and existing dialogue/dependency costs forecast $47.47-52.43 for twenty development tasks plus forty formal tasks in one order. The coordinator therefore allocated a new **$55 hard main-study ceiling**, within the project-wide $100 budget. Only development and mechanism work are released; formal execution remains explicitly held. The isolated combined pilot budget stays at most **$1**. Before starting formal runs, review the forecast and preserve forty unique tasks before purchasing a second order. Any changed scale is frozen before formal result inspection.

Commands (formal commands require the coordinating agent's post-pilot authorization):

```powershell
python -m experiments.harness_v3 --phase pilot --output data/harness-v3
python -m experiments.harness_v3 --phase development --output data/harness-v3-main01 --orders 1 --max-usd 55
# Run only after explicit formal release:
python -m experiments.harness_v3 --phase formal --release-formal --output data/harness-v3-main01 --orders 1 --max-usd 55
python -m experiments.harness_v3 --phase mechanism --output data/harness-v3-main01 --orders 1 --max-usd 55
```

The immutable machine manifest and raw ledger, rather than this prose, identify a particular completed run. At the initial protocol stage no mechanism or effectiveness result has yet been observed.

## Pilot outcome and main freeze

The first cost pilot completed 85 ledger requests at $0.7068055 actual-or-reserved cost, including one unknown-usage reservation. H0 used five model calls; both selected PPC procedures used four calls and passed the independent hidden audit, but the incoming memory bank was empty. This is one development question, not evidence of transfer. A separate proactive/disabled mechanism probe uses the resulting public lesson and the same $1 combined pilot ledger.

The initial pilot data enumeration missed nested snapshot paths; a dated supplemental receipt preserves those hashes. The **fresh main protocol** fixes this before any main execution by hashing every JSON source file recursively, including raw studies and snapshot manifests. The main ledger also records per-request durations. Budget-denied zero-request retry loops are stopped immediately in the main driver; all pilot attempts remain archived.

Progress reports retain all planned IDs, incorrect/failed evaluations and unrun tasks. The formal CLI additionally requires `--release-formal`; setting a budget ceiling does not itself authorize that phase.

The completed combined pilot contains **103 request attempts, $0.8437521 actual-or-reserved**, including the original one unknown-usage reservation. The simple-task proactive/disabled pair both had zero experience-tool events. A separate preregistered, explicitly engineered compound-task instruction caused one search, one read and one activation of learned entry `skill_c430bdffa19657865bcd82f3`; both requested analyses passed the hidden report-only audit. That probe used eight calls and $0.0705971. It proves the real-model search/read/apply path is connected, not that ordinary execution chooses it autonomously or improves from it. The zero-trigger result is retained alongside it.
