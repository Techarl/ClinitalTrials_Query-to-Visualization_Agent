"""Conservative opt-in research ledger; estimates are not provider invoices."""

from app.providers import OpenAIGenerationClient

PRICE_SOURCE = "https://developers.openai.com/api/docs/models/gpt-6.1-sol"


def estimate_cost(usage):
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0)
    written = details.get("cache_write_tokens", 0)
    ordinary = max(0, usage.get("input_tokens", 0) - cached - written)
    return (ordinary * 2 + cached * 0.1 + written * 2.5 + usage.get("output_tokens", 0) * 10) / 1_000_000


class Budget:
    def __init__(self, max_usd=20, max_calls=500):
        self.max_usd, self.max_calls = max_usd, max_calls
        self.events = []
        self.estimated_usd = 0

    def before(self, context_bytes, output_tokens=4096):
        # UTF-8 byte count is a conservative token bound; cache writes are the
        # highest standard input rate. Reserve even failed calls as unknown usage.
        upper = (context_bytes * 2.5 + output_tokens * 10) / 1_000_000
        if len(self.events) >= self.max_calls or self.estimated_usd + upper > self.max_usd:
            raise RuntimeError("Research model-call budget exhausted")
        self.estimated_usd += upper
        event = {"status": "attempted_usage_unknown", "reserved_upper_estimate_usd": upper}
        self.events.append(event)
        return event

    def after(self, event, metadata):
        usage = metadata.get("usage") or {}
        estimate = estimate_cost(usage)
        self.estimated_usd += estimate - event["reserved_upper_estimate_usd"]
        event.update(status="completed", metadata=metadata, estimated_cost_usd=estimate)

    def report(self):
        return {
            "attempts": len(self.events),
            "estimated_or_reserved_usd": self.estimated_usd,
            "max_estimated_usd": self.max_usd,
            "price_source": PRICE_SOURCE,
            "price_note": "Standard API list-rate estimate, not invoice; unknown calls retain conservative byte-based reservations.",
            "events": self.events,
        }


class BudgetedGenerator:
    def __init__(self, settings, budget):
        self.client, self.budget = OpenAIGenerationClient(settings), budget

    def close(self):
        self.client.close()

    def generate(self, context, deadline):
        event = self.budget.before(len((context["system"] + context["payload"]).encode("utf-8")))
        decision, metadata = self.client.generate(context, deadline)
        self.budget.after(event, metadata)
        return decision, metadata
