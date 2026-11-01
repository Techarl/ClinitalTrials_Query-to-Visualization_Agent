"""Audit historical versus corrected extraction without rewriting old study results."""

import hashlib
import json
import re
from pathlib import Path

from app.v2.evidence_selection import eligibility_passages
from evaluation.runtime import write


def main():
    old = json.loads(Path("evaluation/passage_selection.json").read_text(encoding="utf-8"))
    old_ids = {p["nct_id"] for p in old["passages"]}
    records = {}
    rows = []
    for source in old["sources"]:
        path = Path(source["path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise ValueError("Frozen source changed")
        for raw in json.loads(path.read_text(encoding="utf-8"))[:4]:
            nct = raw["protocolSection"]["identificationModule"]["nctId"]
            if nct in records:
                continue
            records[nct] = raw
            text = raw["protocolSection"].get("eligibilityModule", {}).get("eligibilityCriteria", "")
            passages = eligibility_passages(raw, source["path"], digest)
            covered = {i for passage in passages for i in range(passage.start, passage.end)}
            heading = set()
            for match in re.finditer(r"(?mi)^[ \t]*(?:\\?[-*•] )?(?:Inclusion|Exclusion)(?: Criteria)?[ \t]*:?[ \t]*$", text):
                heading.update(range(match.start(), match.end()))
            meaningful = {i for i, char in enumerate(text) if not char.isspace() and i not in heading}
            missing = meaningful - covered
            if missing or text.strip() and not passages:
                raise ValueError(f"Nonempty criterion text omitted: {nct}")
            rows.append({"nct_id": nct, "historical_passages_present": nct in old_ids,
                         "current_passage_count": len(passages), "source_characters": len(text),
                         "criterion_characters": len(meaningful), "covered_criterion_characters": len(meaningful & covered),
                         "pure_heading_characters": len(heading), "extraction_warning": None})
    report = {"historical": {"scanned_records": len(records), "represented_records": len(old_ids), "passages": len(old["passages"])},
              "corrected": {"scanned_records": len(records), "represented_records": sum(r["current_passage_count"] > 0 for r in rows),
                            "passages": sum(r["current_passage_count"] for r in rows), "criterion_character_coverage": 1.0},
              "policy": "All non-whitespace criterion characters retained as exact spans. Pure section headings are represented by the section property, separately counted. Historical result files unchanged.",
              "records": rows}
    write(Path("docs/public-evaluation/passage-extraction-audit.json"), report)
    print(json.dumps({k:v for k,v in report.items() if k != "records"}, indent=2))


if __name__ == "__main__":
    main()
