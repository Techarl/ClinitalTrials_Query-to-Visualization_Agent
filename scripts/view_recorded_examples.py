"""Serve verified historical examples in the real read-only UI, without credentials."""

import html
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from scripts.verify_v2_examples import read, verify

ROOT = Path(__file__).resolve().parents[1]


def create_app(examples: Path | None = None, frontend: Path | None = None):
    examples = examples or ROOT / "examples/v2"
    frontend = frontend or ROOT / "frontend/dist"
    if not (frontend / "index.html").is_file():
        raise RuntimeError("Build the UI first: npm --prefix frontend run build")
    verification = verify(examples)
    app = FastAPI(title="TrialScope recorded examples — read only")
    shares, downloads, links = {}, {}, []
    for entry in read(examples / "index.json")["examples"]:
        folder = examples / entry["directory"]
        evidence = {row["id"]: row["contents"] for row in read(folder / "evidence.json.gz")}
        for number, turn in enumerate(entry["turns"], 1):
            token = entry["directory"] + "-" + str(number)
            directory = folder / turn["directory"]
            result = read(directory / "response.json")
            selected = {}
            for artifact in result["artifacts"]:
                for key in artifact["evidence_ids"]:
                    item = evidence[key]
                    selected[key] = {
                        "evidence_set_id": key, "rule": item["rule"],
                        "total_contributors": len(item["citations"]),
                        "offset": 0, "limit": len(item["citations"]),
                        "citations": item["citations"], "next_offset": None,
                    }
            shares[token] = {"result": result, "evidence": selected, "readonly": True}
            query = read(directory / "request.json")["query"]
            downloads[token + "-result"] = directory / "response.json"
            downloads[token + "-trace"] = directory / "trace.json"
            downloads[token + "-evidence"] = folder / "evidence.json.gz"
            links.append(
                f'<li><a href="/s/{token}">{html.escape(query)}</a>'
                f' — recorded turn {number} · <a href="/recordings/{token}-result">JSON</a>'
                f' · <a href="/recordings/{token}-trace">trace</a>'
                f' · <a href="/recordings/{token}-evidence">complete evidence (gzip)</a></li>'
            )

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>TrialScope recorded examples</title><style>'
            'body{max-width:850px;margin:60px auto;padding:0 24px;font:18px/1.6 system-ui;'
            'color:#183b35;background:#f4f7f5}li{margin:24px 0}a{color:#087866}</style>'
            '<h1>TrialScope · recorded examples</h1>'
            '<p>Five historical model turns on frozen registry records. These are preserved '
            'results, not new executions by the current release. This viewer is read-only: '
            'no database, API key, model calls or new analysis. Chart clicks open full evidence.</p>'
            '<p>Use your browser’s Back button to return to this index. The two follow-up '
            'versions are separate links; their original traces retain the parent relationship.</p>'
            '<ul>' + ''.join(links) + '</ul>'
            f'<p>Startup verification: {verification["exact_fields_checked"]:,} exact source fields. '
            'The failed dependent-query diagnostic remains in the archive and is excluded here.</p></html>'
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "recorded_readonly"}

    @app.get("/v2/shares/{token}")
    def share(token: str):
        if token not in shares:
            raise HTTPException(404, "Recording not found")
        return shares[token]

    @app.get("/s/{token}")
    def ui(token: str):
        if token not in shares:
            raise HTTPException(404, "Recording not found")
        return FileResponse(frontend / "index.html")

    @app.get("/recordings/{name}")
    def download(name: str):
        if name not in downloads:
            raise HTTPException(404, "Recording not found")
        path = downloads[name]
        return FileResponse(path, filename=name + (".json.gz" if path.suffix == ".gz" else ".json"))

    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")
    return app
