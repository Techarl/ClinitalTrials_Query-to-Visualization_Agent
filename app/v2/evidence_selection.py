"""Read-only passage selection. These objects cannot replace statistical scope handles."""

import hashlib
import json
import re
from collections.abc import Callable

from pydantic import BaseModel, Field


class Passage(BaseModel):
    id: str
    nct_id: str
    title: str
    text: str
    section: str
    source_path: str
    source_sha256: str
    field: str = "/protocolSection/eligibilityModule/eligibilityCriteria"
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    def context(self):
        return {"id": self.id, "study": self.nct_id, "section": self.section, "text": self.text}


def eligibility_passages(study, source_path, source_sha256):
    """Preserve exact character spans and inclusion/exclusion headers, including negation."""
    protocol = study["protocolSection"]
    nct_id = protocol["identificationModule"]["nctId"]
    title = protocol["identificationModule"]["briefTitle"]
    raw = protocol.get("eligibilityModule", {}).get("eligibilityCriteria", "")
    boundaries = list(
        re.finditer(r"(?m)^\s*(?:\* |(?:Inclusion|Exclusion)(?: Criteria)?\s*:?[ \t]*$)", raw, re.I)
    )
    section, result = "unspecified", []
    for index, match in enumerate(boundaries):
        start = match.start()
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(raw)
        while start < end and raw[start].isspace():
            start += 1
        while end > start and raw[end - 1].isspace():
            end -= 1
        value = raw[start:end]
        if re.match(r"^(Inclusion|Exclusion)\b", value, re.I):
            section = "exclusion" if value.lower().startswith("exclusion") else "inclusion"
            continue
        if not value:
            continue
        identity = hashlib.sha256(f"{nct_id}:{source_sha256}:{start}:{end}".encode()).hexdigest()[:16]
        result.append(
            Passage(
                id=f"p_{identity}",
                nct_id=nct_id,
                title=title,
                text=value,
                section=section,
                source_path=source_path,
                source_sha256=source_sha256,
                start=start,
                end=end,
            )
        )
    return result


def pack_context(passages, ranked_ids, token_limit: int, count_tokens: Callable[[str], int]):
    """Pack complete passages under one serialized-context cap; no partial quotation cuts."""
    if token_limit < 1:
        raise ValueError("Context budget must be positive")
    if count_tokens("[]") > token_limit:
        raise ValueError("Context budget cannot fit the empty serialized container")
    by_id = {p.id: p for p in passages}
    if len(by_id) != len(passages):
        raise ValueError("Duplicate passage IDs")
    selected, seen_ids, seen_text, skipped = [], set(), set(), []
    for identity in ranked_ids:
        if identity not in by_id:
            raise ValueError("Ranker returned an unknown passage")
        passage = by_id[identity]
        key = (passage.nct_id, passage.section, re.sub(r"\s+", " ", passage.text).strip())
        if identity in seen_ids or key in seen_text:
            continue
        seen_ids.add(identity)
        seen_text.add(key)
        candidate = [*selected, passage.context()]
        if count_tokens(json.dumps(candidate, ensure_ascii=False)) <= token_limit:
            selected = candidate
        else:
            skipped.append(identity)
    payload = json.dumps(selected, ensure_ascii=False)
    return {
        "passages": selected,
        "context": payload,
        "tokens": count_tokens(payload),
        "token_limit": token_limit,
        "skipped_for_budget": skipped,
        "purpose": "explanation_only_not_statistical_population",
    }
