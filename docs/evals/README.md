# Evaluation artifact index

| File | Scope |
| --- | --- |
| semantic_cases.json | 48 engineer-authored cases / 50 turns, frozen before live evaluation; 36 development / 12 heldout |
| semantic_report.json | Final 5344202 source: 49/50; 37/37 executable plans, 9/9 required clarifications |
| semantic_report_ea65a0a.json | Earlier frozen 50/50 baseline used by both research experiments; preserved unchanged |
| semantic_preliminary_summary.json | Development pass: 49/50; a full-site request safely clarified instead of unsupported |
| decision_report.json | 50 paired post-planner gate replays: B, extra GPT, real Jev; no gold in runtime |
| entity_retrieval_report.json | Source-grounded candidate rankings and citations; exact-name hits and separately disclosed post-hoc mention proxy |
| harness_report.json | 69bd5ea PPC/experience pilot, full streams and ledger; valid comparison is 11 single-turn tasks |
| harness_experience.json | Unlabelled observed contrasts/negative experience, zero adopted reusable components |

Semantic planning, source/number verification, and experimental runtime observations are different checks. No single success percentage represents all three. The final semantic difference remains recorded rather than relabelled: all-site retrieval is outside scope, and the model asks for a narrower scope instead of returning the expected unsupported status.

The decision report's baseline hash refers to the archived semantic report before two archival annotation fields (`source_commit`, `source_code_hashes`) were added. Removing only those fields reproduces the original decision baseline hash. The harness baseline hash matches the full archived file. Contexts, plans and usage are identical; expected answers are only consumed by the scorer.

The final offline checker additionally verifies rendering field mappings, metadata and unreferenced evidence on empty results. Its 26 corruption cases must fail. See [validation](../VALIDATION.json), [design and verification](../DESIGN.md), [experiment methods](../EXPERIMENTS.md), and [recorded API usage](../API_USAGE.json).
