# NLI4CT interruption recovery and final reporting rules

This reporting amendment belongs to the personal-project phase after submission. It does not modify the submitted ZIP, frozen experimental source, original results or the experimental producer. Final quality reporting requires terminal results for all 500 questions × five arms, the complete common 200-question answer subset, and successful closure of the current recovery process.

## Fixed recovery metadata

- Frozen identity: `476bb042ff5273e16975f4e638aef8ff0c85e62877ca08a6bea540c038ea86ed`, covering 70 source files.
- The pre-resume backup contains 14,983 files. Its ZIP SHA256 is `c9d37015e4becdc20161f4a37ee2de52feb45d3fe7f2492d50158f289587dc66`.
- Filenames alone show activity for 394 questions and saved results for all five arms of 391 questions, totaling 1,964 arm-result files. These counts describe execution progress, not accuracy.
- The first recovery attempt exited during dependency import, before any paid calls according to its launch diagnostics. The effective second attempt uses the original experimental dependency environment and passed frozen-identity verification again. Its closure receipt is `nli-resume-exit-2.json`; the earlier failed `nli-resume-exit.json` must not substitute for it.
- The recovery audit recorded 10 unknown-usage reservations totaling $0.0799175, still charged against the original $20 ceiling. Reporting preparation did not read the active ledger. Final reporting reconciled these amounts against the backup ledger and the complete formal ledger.

## Latency exclusion rule

The rule and exact case/arm list were saved in the [recovery latency plan](public-evaluation/nli-recovery-latency-plan.json) without accessing passage scores, answers or gold labels. Inspection was limited to the recovery manifest, ZIP directory and frozen protocol metadata. Case-result files were not opened.

For a question with files present before recovery, an arm whose result was not yet saved is conservatively excluded from ranking-latency summaries. An existing failure marker also triggers exclusion. This rule can cover later arms that had not yet started; it does not claim that every flagged arm had an in-flight call.

The fixed list contains Luna and full for three questions: six case/arm pairs. Two questions had 12 and eight saved Luna passage scores; the third had none. Full-text arms have no passage-score cache but follow the same conservative rule. Final outcomes or observed latency must not be used to revise the exclusion list.

Quality metrics, failures, confidence intervals, the 500-question primary denominator and the 200-question answer denominator remain unchanged. Original elapsed times remain in sanitized per-case JSON and diagnostic summaries; filtered latency reports show retained and excluded sample counts. A separate common-case summary retains only questions with all five arms completed and no interruption flags, so its timing samples use the same questions across arms. This summary concerns timing only and does not create another primary quality analysis.

Ranking timers exclude shared embedding warmup, initial retrieval, downtime and the full work of interrupted attempts. Jev/Luna client implementations remain asymmetric. These timings therefore cannot establish pure model-serving speed or end-to-end task latency. The independent development-set [controlled latency study](JEV_LATENCY_CONTROL.md) remains separate.

## Cost and recovery evidence

Final reporting verifies the unchanged backup ZIP, its member set and protocol, and that every backup-ledger event is an **exact, unchanged prefix** of the final ledger. Removing an original attempt, zeroing an unknown-usage reservation or changing a prior event blocks publication.

The backup ledger supplies only a pre-resume subtotal and reservation disclosure. It must never be added as a fourth independent cost ledger: `formal_v2` already includes those events and later recovery attempts. Pilot and aborted_v1 remain separate. Snapshot, closure-receipt and previously registered exclusion-plan hashes are retained; raw materials and answers are not exported.

## Closed-run reconciliation

The effective recovery process completed on 2026-11-01 at 12:27:20 UTC. The final report retains all 500 questions, five arms and the shared 200-question answer subset. All arm results completed; recovery added 3,520 attempts. The [results](NLI4CT_RESULTS.md) and machine-readable recovery appendix contain the final costs and timing sample counts.

A strict report-versus-ledger comparison found one non-cost difference: the last Luna ranking event's in-memory metadata contained `elapsed_seconds`, while the persisted ledger lacked that field. Frozen `model_score` writes accounting before appending elapsed time to the same metadata object. The reporter permits only this additional finite, nonnegative telemetry value when it exactly matches the separately saved score metadata. Every other event field, order, usage, cost and status must match. Missing proof, changed existing elapsed time or any other discrepancy rejects publication. Both original digests and the saved proof hash appear in `summary.final_ledger_reconciliation`; neither original file is changed, and accounting uses the persisted ledger.

## Final command

Run the following command from the current repository directory. `$nliOriginal` points to the frozen study workspace; it does not change the producer or its environment. Run only after `report.json` is complete and the second recovery's exit receipt reports `completed`. Progress logs are not evidence of closure.

```powershell
$nliOriginal = 'C:/path/to/frozen-study-workspace'
$nliRecovery = "$nliOriginal/data/review-followup/crash-recovery-20261101-0950"
python -m scripts.report_public_nli `
  --study "$nliOriginal/data/nli4ct-official-test-v2" `
  --inputs "$nliOriginal/data/external-benchmarks/nli4ct-test-inputs.json" `
  --pilot "$nliOriginal/data/public-pilot-nli4ct-v1/ledger.json" `
  --aborted "$nliOriginal/data/nli4ct-official-test-v1/ledger.json" `
  --recovery-manifest "$nliRecovery/nli-recovery.json" `
  --recovery-completion "$nliRecovery/nli-resume-exit-2.json" `
  --recovery-plan docs/public-evaluation/nli-recovery-latency-plan.json
```

Outputs remain `docs/NLI4CT_RESULTS.md` and the summary/cases/accounting JSON files, with an additional `nli4ct-recovery.json`. The known interrupted protocol requires recovery arguments; a failed closure marker causes rejection before final scores are read.
