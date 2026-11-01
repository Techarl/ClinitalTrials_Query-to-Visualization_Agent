"""OpenAI Responses integration; generation and future decisions have separate interfaces."""

import time
from typing import Protocol

from openai import APIError, OpenAI
from pydantic import ValidationError

from app.config import Settings
from app.models import PlannerDecision


class GenerationFailure(RuntimeError):
    def __init__(self, kind: str, repairable: bool = False, errors: list[str] | None = None):
        super().__init__(kind)
        self.kind, self.repairable, self.errors = kind, repairable, errors or []


def usage_is_complete(usage) -> bool:
    """Only settle known, consistent token counts, including cache billing data."""

    def count(value):
        return type(value) is int and value >= 0

    if not isinstance(usage, dict) or not all(
        count(usage.get(key)) for key in ("input_tokens", "output_tokens", "total_tokens")
    ):
        return False
    if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
        return False
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict) or not all(
        count(details.get(key)) for key in ("cached_tokens", "cache_write_tokens")
    ):
        return False
    if details["cached_tokens"] + details["cache_write_tokens"] > usage["input_tokens"]:
        return False
    output_details = usage.get("output_tokens_details")
    return output_details is None or (
        isinstance(output_details, dict)
        and count(output_details.get("reasoning_tokens", 0))
        and output_details.get("reasoning_tokens", 0) <= usage["output_tokens"]
    )


class GenerationClient(Protocol):
    def generate(self, context: dict, deadline: float) -> tuple[PlannerDecision, dict]: ...


class DecisionClient(Protocol):
    def decide(self, state: dict, questions: list[dict]) -> dict: ...


class OpenAIGenerationClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.openai_api_key.get_secret_value():
            raise ValueError("OPENAI_API_KEY is required for natural-language planning")
        self.client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=str(settings.openai_base_url),
            max_retries=0,
        )

    def close(self):
        self.client.close()

    def generate(self, context: dict, deadline: float) -> tuple[PlannerDecision, dict]:
        started = time.monotonic()
        remaining = deadline - started
        if remaining <= 0:
            raise GenerationFailure("run_budget_exhausted")
        try:
            response = self.client.responses.parse(
                model=self.settings.openai_model,
                input=[
                    {"role": "system", "content": context["system"]},
                    {"role": "user", "content": context["payload"]},
                ],
                reasoning={"effort": self.settings.openai_reasoning_effort},
                text_format=PlannerDecision,
                max_output_tokens=self.settings.openai_max_output_tokens,
                timeout=min(self.settings.openai_timeout_seconds, remaining),
                store=False,
            )
            metadata = {
                "provider": "openai",
                "requested_model": self.settings.openai_model,
                "reported_model": response.model,
                "reasoning_effort": self.settings.openai_reasoning_effort,
                "response_id": response.id,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": response.usage.model_dump() if response.usage else None,
                "actual_cost": None,
                "cost_note": "Provider cost not returned; token usage retained.",
            }
            if response.output_parsed is None:
                raise GenerationFailure("model_refusal_or_missing_structured_output")
            return response.output_parsed, metadata
        except ValidationError as error:
            errors = [
                "/".join(map(str, item["loc"])) + ": " + item["msg"]
                for item in error.errors(include_input=False)
            ][:5]
            raise GenerationFailure("invalid_structured_plan", True, errors) from error
        except APIError as error:
            # Error bodies can echo credentials; never propagate them to traces or HTTP responses.
            raise GenerationFailure(type(error).__name__) from error
