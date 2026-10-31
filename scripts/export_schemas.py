"""Write public model schemas without reading credentials or contacting services."""

import json
from pathlib import Path

from app.models import AnalysisPlan, AnalysisRequest, AnalysisResponse, Citation
from app.sources import Snapshot

root = Path(__file__).resolve().parents[1] / "docs" / "schemas"
root.mkdir(parents=True, exist_ok=True)
for model in (AnalysisRequest, AnalysisPlan, AnalysisResponse, Citation, Snapshot):
    target = root / f"{model.__name__}.schema.json"
    target.write_text(
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(target.name)
