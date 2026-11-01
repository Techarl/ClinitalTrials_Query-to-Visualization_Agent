"""Fetch exact official dataset bytes; HF_TOKEN is used only with Hugging Face."""

from pathlib import Path

import httpx
from dotenv import dotenv_values

from evaluation.public_datasets import FILES, verify_file


def main():
    root = Path("data/external-benchmarks")
    root.mkdir(parents=True, exist_ok=True)
    token = dotenv_values(".env").get("HF_TOKEN")
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for name, (url, _) in FILES.items():
            if (root / name).exists():
                verify_file(root, name)
                continue
            headers = (
                {"Authorization": "Bearer " + token}
                if token and url.startswith("https://huggingface.co/")
                else {}
            )
            response = client.get(url, headers=headers)
            if response.status_code != 200:
                raise RuntimeError(
                    f"{name}: HTTP {response.status_code}; gated data requires approved HF_TOKEN"
                )
            (root / name).write_bytes(response.content)
            verify_file(root, name)
            print(name, "verified")


if __name__ == "__main__":
    main()
