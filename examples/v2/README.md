# Recorded V2 examples

These are live model executions on frozen ClinicalTrials.gov records, recorded before the current delivery changes. No model was called to export or verify them. They are separate from the historical V1 examples one directory above.

| Example | Capability | Request and complete response |
|---|---|---|
| 01_phase | Phase distribution with complete contributor evidence. | [Turn 1](01_phase/turns/01/request.json) / [result](01_phase/turns/01/response.json) |
| 02_followup | Two turns in one session: change the annual date axis while retaining the population. | [Turn 1](02_followup/turns/01/request.json) / [result](02_followup/turns/01/response.json) · [Turn 2](02_followup/turns/02/request.json) / [result](02_followup/turns/02/response.json) |
| 03_comparison | Asthma drug comparison with independently preserved cohorts. | [Turn 1](03_comparison/turns/01/request.json) / [result](03_comparison/turns/01/response.json) |
| 04_scatter | Enrollment versus duration, with exact-date exclusions. | [Turn 1](04_scatter/turns/01/request.json) / [result](04_scatter/turns/01/response.json) |

Run `python -m scripts.verify_v2_examples` from the repository root after installing Python dependencies. Verification needs no API key, model, Neo4j service, SQLite database or network connection. It checks delivery hashes, response/store binding, complete chart points and contributor sets, raw source fields, and follow-up lineage.

Each turn includes its actual request, full RunResult, state and public event/tool journal. The original recording is preserved byte-for-byte inside original-recording.json.gz. Each example includes complete assets.json.gz and evidence.json.gz; Python's gzip module or an archive utility can open them. The shared sources.zip deduplicates immutable snapshots; sources-index.json lists acquisition timestamps, raw hashes and record counts.

The original environment contains file hashes but no reliable run commit ID. source-identity.json records the exact Git blob/EOL reconstruction, and recorded-source.zip matches all 47 recorded code hashes and the dependency lock hash. This proves file contents, not a clean working-tree commit. verification.json records the current verifier file hashes separately.

The [dependent diagnostic](diagnostics/dependent/diagnostic.json) is excluded from qualified examples: Merck/top-three selection linkage passes, but the original annual trend omits the three 2025 zero buckets. Its complete original trace, evidence and sources remain available; the chart was not patched after execution.
