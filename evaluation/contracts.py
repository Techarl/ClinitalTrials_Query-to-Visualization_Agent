"""Evaluator-only episode labels; never passed to the planner or editor."""

from typing import Literal

from pydantic import BaseModel, Field


class Turn(BaseModel):
    query: str
    expected: dict
    parent_turn: int | None = None


class Episode(BaseModel):
    id: str
    family: str
    entity_group: str
    split: Literal["development", "calibration", "heldout"]
    origin: Literal["authored", "natural_error", "synthetic_mutation"] = "authored"
    turns: list[Turn] = Field(min_length=1)
