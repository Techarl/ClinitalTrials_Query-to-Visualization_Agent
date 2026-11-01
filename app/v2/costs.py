"""Versioned short-context standard-rate estimates; unknown usage never becomes free."""

import math

from app.providers import usage_is_complete

PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"
PRICE_VERSION = "2026-11-01-standard-short-context"
# ordinary input, cached input, 30-minute cache write, output; USD per million tokens.
RATES = {"gpt-6.1-sol": (2, 0.1, 2.5, 10), "gpt-6-luna": (0.1, 0.01, 0.125, 0.5)}


def estimate(metadata):
    usage = metadata.get("usage")
    if metadata.get("provider") == "openrouter_decisions" and isinstance(usage, dict):
        cost = usage.get("cost")
        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
            return cost
    if not usage_is_complete(usage):
        return None
    model = metadata.get("requested_model") or metadata.get("reported_model") or "gpt-6.1-sol"
    rates = next((value for key, value in RATES.items() if model == key or model.startswith(key + "-")), None)
    if rates is None:
        return None
    details = usage["input_tokens_details"]
    quantities = (
        usage["input_tokens"] - details["cached_tokens"] - details["cache_write_tokens"],
        details["cached_tokens"],
        details["cache_write_tokens"],
        usage["output_tokens"],
    )
    return sum(q * r for q, r in zip(quantities, rates, strict=True)) / 1_000_000
