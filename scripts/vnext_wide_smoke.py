"""Live wide-source acquisition acceptance; no model calls or statistics from samples."""

import json
import time
from pathlib import Path

from app.config import Settings
from app.sources import ClinicalTrialsClient, SnapshotStore
from app.v2.acquisition import fetch_partitioned


def main():
    root = Path("data/vnext-wide")
    root.mkdir(exist_ok=True, parents=True)
    settings = Settings(max_source_records=50000, max_source_pages=100, ctgov_page_size=1000)
    client = ClinicalTrialsClient(settings)
    try:
        manifest, records, audit = fetch_partitioned(client, {"query.cond": "asthma"}, time.monotonic() + 300)
        SnapshotStore(root).save(manifest, records)
        report = {
            "manifest": manifest.model_dump(),
            "audit": audit,
            "model_calls": 0,
            "unique_records": len({r["protocolSection"]["identificationModule"]["nctId"] for r in records}),
            "complete": manifest.complete,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "complete": report["complete"],
                    "records": len(records),
                    "partition_count": len(audit.get("partitions", [])),
                }
            )
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
