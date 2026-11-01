"""Download a pinned public ONNX cross-encoder; verify bytes before publication."""

import argparse
import hashlib
import json
from pathlib import Path

import httpx

MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
FILES = {
    "config.json": "380e02c93f431831be65d99a4e7e5f67c133985bf2e77d9d4eba46847190bacc",
    "onnx/model.onnx": "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a",
    "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    "tokenizer_config.json": "a5c2e5a7b1a29a0702cd28c08a399b5ecc110c263009d17f7e3b415f25905fd8",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("data/reranker-model"))
    parser.add_argument("--proxy", default=None)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with httpx.Client(proxy=args.proxy, follow_redirects=True, timeout=180) as client:
        for source, expected in FILES.items():
            path = args.directory / Path(source).name
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
                continue
            response = client.get(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{source}")
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != expected:
                raise ValueError(f"Downloaded model hash mismatch: {source}")
            pending = path.with_suffix(".pending")
            pending.write_bytes(response.content)
            pending.replace(path)
    manifest = {
        "model": MODEL,
        "revision": REVISION,
        "license": "Apache-2.0",
        "files": {Path(k).name: v for k, v in FILES.items()},
    }
    (args.directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"model": MODEL, "revision": REVISION, "verified_files": len(FILES)}))


if __name__ == "__main__":
    main()
