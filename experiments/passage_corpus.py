"""Freeze a small, auditable eligibility comparison benchmark before model scoring."""

import hashlib
import json
from pathlib import Path

from app.v2.evidence_selection import eligibility_passages
from evaluation.runtime import write

# These anchors are author annotations of the original source, never model inputs.
# A requirement may have multiple alternative passages (e.g. repeated exclusions).
CASES = [
    (
        "asthma_ige",
        "development",
        "Compare the total serum IgE eligibility ranges in omalizumab asthma trials NCT00046748 and NCT00079937. Quote each trial's criterion.",
        [("NCT00046748", ["total serum IgE"]), ("NCT00079937", ["Total serum IgE"])],
    ),
    (
        "asthma_smoking",
        "development",
        "Compare smoking-related exclusions in dupilumab asthma trials NCT01312961 and NCT01854047, including current/recent smoking and historical pack-years.",
        [
            ("NCT01312961", ["Current smoker"]),
            ("NCT01312961", ["Previous smoking"]),
            ("NCT01854047", ["Current smoker"]),
            ("NCT01854047", ["Previous smoker"]),
        ],
    ),
    (
        "melanoma_function",
        "development",
        "Compare the permitted ECOG performance status in NCT00441337 and NCT00730639, which include melanoma. Quote both ranges.",
        [("NCT00441337", ["ECOG PS"]), ("NCT00730639", ["Eastern Cooperative Oncology Group"])],
    ),
    (
        "melanoma_prior",
        "development",
        "Do NCT01704287 and NCT00441337 both permit patients previously treated with CTLA-4 blockade such as ipilimumab? Contrast the relevant inclusion or exclusion criteria; preserve evidence against this premise.",
        [("NCT01704287", ["refractory to ipilimumab"]), ("NCT00441337", ["Prior therapy with an anti-PD-1"])],
    ),
    (
        "psoriasis_severity",
        "heldout",
        "Compare required plaque severity in NCT00669916 and NCT00770965: body surface area, IGA and PASI thresholds. Quote the supporting criteria.",
        [
            ("NCT00669916", ["Diagnosis of plaque psoriasis"]),
            ("NCT00669916", ["PASI score"]),
            ("NCT00770965", ["Diagnosis of plaque psoriasis"]),
        ],
    ),
    (
        "psoriasis_women",
        "heldout",
        "Both NCT00669916 and NCT00770965 allow women of childbearing potential, correct? Check the actual eligibility text and retain evidence contradicting that statement.",
        [
            ("NCT00669916", ["Women of child-bearing potential", "Post menopausal"]),
            ("NCT00770965", ["Women of childbearing potential"]),
        ],
    ),
    (
        "psoriasis_washout",
        "heldout",
        "Compare prior biologic or immunosuppressant washout requirements in NCT00669916 and NCT00770965. Which passage specifies actual washout durations and which only says periods are required?",
        [
            ("NCT00669916", ["Recent previous treatment"]),
            ("NCT00770965", ["Recent use of investigational drugs"]),
        ],
    ),
    (
        "psoriasis_consent",
        "heldout",
        "Compare informed-consent requirements in NCT00508547 and NCT00669916. Cite the positive consent requirements rather than unrelated drug restrictions.",
        [("NCT00508547", ["Ability to understand and sign"]), ("NCT00669916", ["Able to communicate well"])],
    ),
    (
        "ra_duration",
        "heldout",
        "Compare the required duration since rheumatoid arthritis diagnosis in NCT00049751 and NCT00124982. Give the two minimum durations with sources.",
        [
            ("NCT00049751", ["Subjects must meet ACR criteria"]),
            ("NCT00124982", ["Rheumatoid arthritis (RA) for greater"]),
        ],
    ),
    (
        "ra_prior",
        "heldout",
        "Compare the prior DMARD or anti-TNF treatment criteria in NCT00049751 and NCT00124982. Preserve the exact wording even if it seems surprising.",
        [
            ("NCT00049751", ["satisfactory response or intolerance"]),
            ("NCT00124982", ["Subjects currently or previously received"]),
        ],
    ),
    (
        "ra_pregnancy",
        "heldout",
        "Do NCT00049751 and NCT00195650 admit pregnant or breastfeeding participants? Cite the exclusions, including any intention to become pregnant.",
        [("NCT00049751", ["women can not be pregnant"]), ("NCT00195650", ["female subject who is pregnant"])],
    ),
    (
        "ra_cancer",
        "heldout",
        "Compare cancer-history exclusions in NCT00049751 and NCT00124982, including lymphoma, time limits and stated skin-cancer exceptions.",
        [
            ("NCT00049751", ["history of malignant lymphoma"]),
            ("NCT00049751", ["no previous history of cancer"]),
            ("NCT00124982", ["History of cancer within the last"]),
        ],
    ),
]


def build(output=Path("evaluation/passage_selection.json")):
    if output.exists():
        raise FileExistsError(
            "Frozen corpus already exists; choose a new version, never overwrite heldout data"
        )
    passages, records, sources = [], {}, []
    for path in sorted(Path("examples/snapshots").glob("*/studies.json")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sources.append({"path": path.as_posix(), "sha256": digest})
        for study in json.loads(path.read_text(encoding="utf-8"))[:4]:
            nct = study["protocolSection"]["identificationModule"]["nctId"]
            if nct not in records:
                records[nct] = study
                passages.extend(eligibility_passages(study, path.as_posix(), digest))
    cases = []
    for identity, split, query, requirements in CASES:
        groups = []
        for index, (nct, anchors) in enumerate(requirements):
            alternatives = [
                p.id
                for p in passages
                if p.nct_id == nct and any(anchor.casefold() in p.text.casefold() for anchor in anchors)
            ]
            if not alternatives:
                raise ValueError(f"Missing annotation {identity}: {nct}, {anchors}")
            groups.append(
                {
                    "id": f"requirement_{index + 1}",
                    "study": nct,
                    "anchors": anchors,
                    "passage_ids": alternatives,
                }
            )
        cases.append(
            {
                "id": identity,
                "split": split,
                "query": query,
                "requirements": groups,
                "counterevidence": identity in {"melanoma_prior", "psoriasis_women", "ra_pregnancy"},
            }
        )
    value = {
        "version": "eligibility-passages-v1",
        "annotation": "Author-selected exact source anchors; no independent human annotation. Binary relevance and alternative evidence groups; not clinical efficacy labels.",
        "sources": sources,
        "passages": [p.model_dump() for p in passages],
        "cases": cases,
        "protocol": {
            "pool": "BM25+dense reciprocal rank fusion top 12; identical candidates for all arms; no gold injection",
            "context_tokens": 350,
            "tokenizer": "cl100k_base",
            "ranking": "descending score, ties preserve initial hybrid order; no relevance threshold tuning",
            "arms": ["hybrid", "cross_encoder", "jev", "luna"],
            "answer_model": "gpt-6.1-sol",
            "answer_mode": "extractive evidence answer with exact quotes, not clinical recommendation",
            "scope": "Four development and eight disease-disjoint heldout queries; known trial IDs; bounded 24-record corpus; not full-registry recall",
        },
    }
    write(output, value)
    print(
        json.dumps(
            {"path": str(output), "passages": len(passages), "records": len(records), "cases": len(cases)}
        )
    )


if __name__ == "__main__":
    build()
