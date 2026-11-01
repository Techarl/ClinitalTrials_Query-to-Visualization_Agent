"""Official dataset adapters. Runtime inputs and hidden scoring labels are separate."""

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from app.sources import content_hash
from evaluation.runtime import write

NLI_REVISION = "8e8e5fc506b916984011873403b9ea5ed6dd96c2"
TRIALQA_REVISION = "3678e7e38748477d0606407468db9cd7e2f2e667"
FILES = {
    "nli4ct.zip": (
        f"https://raw.githubusercontent.com/ai-systems/nli4ct/{NLI_REVISION}/Complete_dataset.zip",
        "dd972c46e69a5bc5e5fb74c2105b5444722d81879ab96b214545b943e4e7ebbe",
    ),
    "trialqa.parquet": (
        f"https://huggingface.co/datasets/EdisonScientific/labbench2/resolve/{TRIALQA_REVISION}/trialqa/train-00000-of-00001.parquet",
        "b571c93dce7497f678e019c17b1d4bc230da7a4d180c3cb9f22343ecc2efcd42",
    ),
}


def verify_file(root, name):
    path = root / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != FILES[name][1]:
        raise ValueError(f"Official dataset checksum differs: {name}")
    return path


def nli4ct(root, split):
    file = {"train": "train", "dev": "dev", "test": "Gold_test"}[split]
    with zipfile.ZipFile(verify_file(root, "nli4ct.zip")) as archive:
        questions = json.loads(archive.read(f"Complete_dataset/{file}.json"))
        tasks, gold = [], {}
        for identity, item in sorted(questions.items()):
            passages, expected = [], []
            for role in ("Primary", "Secondary"):
                nct = item.get(role + "_id")
                if not nct:
                    continue
                record = json.loads(archive.read(f"Complete_dataset/CT json/{nct}.json"))
                for index, sentence in enumerate(record[item["Section_id"]]):
                    pid = f"{nct}:{item['Section_id']}:{index}"
                    passages.append(
                        {
                            "id": pid,
                            "study": nct,
                            "section": item["Section_id"],
                            "text": sentence,
                            "role": role,
                            "sentence_index": index,
                        }
                    )
                    if index in item[role + "_evidence_index"]:
                        expected.append(pid)
            tasks.append(
                {
                    "id": identity,
                    "split": split,
                    "statement": item["Statement"],
                    "type": item["Type"],
                    "section": item["Section_id"],
                    "passages": passages,
                }
            )
            gold[identity] = {"label": item["Label"], "evidence_ids": expected}
        assert len(tasks) == {"train": 1700, "dev": 200, "test": 500}[split]
        return tasks, gold


def trialqa(root):
    import pyarrow.parquet as parquet

    rows = parquet.read_table(verify_file(root, "trialqa.parquet")).to_pylist()
    tasks, gold = [], {}
    for row in rows:
        sources = row["sources"]
        if isinstance(sources, str):
            try:
                sources = json.loads(sources)
            except ValueError:
                import ast

                sources = ast.literal_eval(sources)
        ncts = sorted(set(re.findall(r"NCT\d{8}", " ".join(sources))))
        category = (
            "ctgov_record_candidate"
            if sources
            and all(
                urlparse(url).netloc in ("clinicaltrials.gov", "www.clinicaltrials.gov")
                and re.fullmatch(r"/(?:study|ct2/show)/NCT\d{8}/?", urlparse(url).path)
                for url in sources
            )
            else "additional_source_required"
        )
        tasks.append(
            {
                "id": row["id"],
                "question": row["question"],
                "sources": sources,
                "nct_ids": ncts,
                "source_audit": category,
                "audit_limit": "Source URL audit only; JSON field coverage needs separate verification.",
            }
        )
        gold[row["id"]] = {"answer": row["ideal"], "key_passage": row["key_passage"]}
    assert len(tasks) == 120
    return tasks, gold


def prepare(root):
    report = {
        "sources": {name: {"url": url, "sha256": digest} for name, (url, digest) in FILES.items()},
        "redistribution": "Raw datasets and answer keys remain in ignored data/; download from original sources.",
        "trialqa_license": "CC-BY-SA-4.0; gated upstream; retain canary and original files",
        "nli4ct_license": "No license file in pinned official repository; no raw redistribution",
        "splits": {},
    }
    for split in ("train", "dev", "test"):
        tasks, gold = nli4ct(root, split)
        write(root / f"nli4ct-{split}-inputs.json", tasks)
        write(root / f"nli4ct-{split}-gold.json", gold)
        report["splits"][split] = {
            "questions": len(tasks),
            "input_sha256": content_hash(tasks),
            "gold_sha256": content_hash(gold),
        }
    tasks, gold = trialqa(root)
    write(root / "trialqa-inputs.json", tasks)
    write(root / "trialqa-gold.json", gold)
    report["trialqa"] = {
        "questions": len(tasks),
        "input_sha256": content_hash(tasks),
        "gold_sha256": content_hash(gold),
        "source_audit": {
            c: sum(t["source_audit"] == c for t in tasks) for c in {t["source_audit"] for t in tasks}
        },
    }
    write(root / "dataset-manifest.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/external-benchmarks"))
    args = parser.parse_args()
    print(json.dumps(prepare(args.root), indent=2))
