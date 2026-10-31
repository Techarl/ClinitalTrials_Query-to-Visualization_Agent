"""Replaceable extension boundaries. Experimental implementations are intentionally absent."""

from dataclasses import dataclass
from typing import Protocol

from app.models import AnalysisPlan, PlannerDecision


@dataclass(frozen=True)
class HarnessConfig:
    version: str = "baseline-v1"
    prompt_version: str = "planner-v1"
    field_catalog_version: str = "ctgov-fields-v1"
    system_instructions: str | None = None
    context_char_limit: int | None = None


class HarnessProvider(Protocol):
    def load(self, version: str) -> HarnessConfig: ...


class BaselineHarnessProvider:
    def load(self, version: str) -> HarnessConfig:
        if version != "baseline-v1":
            raise ValueError("Only baseline-v1 is implemented")
        return HarnessConfig()


class ExperienceStore(Protocol):
    def retrieve(self, context: dict) -> list[dict]: ...


class DisabledExperienceStore:
    def retrieve(self, context: dict) -> list[dict]:
        return []


class TraceSink(Protocol):
    def record(self, event: dict) -> None: ...


class ListTraceSink:
    def __init__(self, events: list):
        self.events = events

    def record(self, event: dict) -> None:
        self.events.append(event)


class DecisionPolicy(Protocol):
    def evaluate(self, decision: PlannerDecision, context: dict) -> PlannerDecision: ...


class BaselineDecisionPolicy:
    def evaluate(self, decision: PlannerDecision, context: dict) -> PlannerDecision:
        # No additional model call. Future policies can change the typed action here.
        return decision


def resolve_plan(decision: PlannerDecision, previous: AnalysisPlan | None) -> AnalysisPlan:
    """Fixed plan resolution is outside the replaceable decision policy."""
    if decision.action != "execute":
        raise ValueError("Execution requires an execute decision")
    if decision.mode == "refine":
        if previous is None or decision.patch is None or decision.plan is not None:
            raise ValueError("Refinement requires a previous plan and a patch, not a replacement plan")
        return decision.patch.apply(previous)
    if decision.plan is None or decision.patch is not None:
        raise ValueError("A new or clarified analysis needs a complete plan and no patch")
    return decision.plan
