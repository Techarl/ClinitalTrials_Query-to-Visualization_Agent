# A three-minute recorded-run demonstration

Use [the curated V2 recordings](../examples/v2/README.md) and state clearly that they are recorded runs on dated, frozen registry records. Do not present manual tool smoke outputs as autonomous Agent answers. Verify the chosen exports and current browser behavior before recording. The index identifies each exact request, result, complete evidence, model calls and source version.

For a reproducible walkthrough on a fresh checkout, install the Python requirements, build the frontend, and start the separate read-only recording viewer:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
python -m uvicorn scripts.view_recorded_examples:create_app --factory --host 127.0.0.1 --port 8013
```

Open <http://127.0.0.1:8013/>. Startup verifies all 57,805 source fields before serving the five saved turns through the real UI. No `.env`, database or model key is needed. The index provides each original result, trace and complete evidence download; chart clicks open citations. Follow-up versions are separate index links. This viewer cannot run new queries or demonstrate live execution, history editing or share creation; use the ordinary service for those interactions. Its landing page clearly labels the historical recordings.

| Time | Show | Suggested narration |
| --- | --- | --- |
| 0:00–0:20 | Workspace and the saved question | “TrialScope turns questions about registered clinical studies into charts with traceable evidence. The model chooses typed tools; statistics are calculated from retrieved records.” |
| 0:20–0:50 | Phase chart, scope and count caption | “This chart counts distinct study identifiers in the stated population. Combined phases and missing values remain explicit. Coverage distinguishes a complete population from sampled citations.” |
| 0:50–1:15 | Click a phase bar and inspect a source field | “Every bar links to its contributing studies, including the original field value and source pointer. Its count can be checked against the saved records.” |
| 1:15–1:45 | Recorded enrollment-versus-duration scatter | “Duration requires exact start and completion dates. Missing precision is disclosed instead of replaced with invented dates. Each point retains its own source.” |
| 1:45–2:15 | Recorded follow-up, execution steps and version switch | “The Agent reads observations before choosing its next step. Follow-ups retain an explicit parent and scope. Different versions remain inspectable, and JSON exports preserve the result contract.” |
| 2:15–2:45 | Final experiment table with limits | “Experiments are isolated from the service. We compare simple baselines, dedicated ranking and decision models under recorded protocols. We report failures, costs and negative findings as well as improvements.” |

For a network demonstration, describe the edge as distinct shared interventions within the source population, not a collaboration. Show the dependent top-sponsor example only after its actual anchor, selected IDs and downstream scopes pass the linkage audit.

Before recording: open each chosen run, click the current history item twice without losing the result, switch versions, inspect a chart citation, check the export, and confirm the browser contains no exposed credentials. A demonstration is a product walkthrough, not a substitute for the complete evaluation report.
