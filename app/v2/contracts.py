"""V2 wire and tool contracts. V1 date bounds retain their original meaning."""

from datetime import date
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from app.models import Contract, Filters

DateField = Literal["start", "primary_completion", "completion", "first_posted"]
Operation = Literal["category", "time_series", "histogram", "network", "scatter"]


class DateRange(Contract):
    field: DateField = "start"
    lower: date | None = None
    upper: date | None = None
    precision_policy: Literal["contained"] = "contained"

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower and self.upper and self.lower > self.upper:
            raise ValueError("Date range is reversed")
        return self


class RunRequest(Contract):
    query: str = Field(min_length=1, max_length=6000)
    session_id: str | None = None
    parent_run_id: str | None = None
    filters: Filters | None = None
    date_range: DateRange | None = None
    intervention_scope: Literal["population", "comparison"] | None = None
    source_mode: Literal["full_evidence", "aggregate_overview"] = "full_evidence"
    language: Literal["en", "zh"] = "en"


class GoalParameters(Contract):
    group_by: (
        Literal["phase", "status", "country", "sponsor", "sponsor_class", "intervention_type", "study_type"]
        | None
    ) = None
    time_field: DateField | None = None
    time_grain: Literal["year", "month"] | None = None
    relationship: Literal["sponsor_drug", "drug_cooccurrence", "sponsor_shared_drug"] | None = None
    histogram_bins: int | None = Field(default=None, ge=1, le=50)
    x: Literal["enrollment", "trial_duration", "start_year", "completion_year"] | None = None
    y: Literal["enrollment", "trial_duration", "start_year", "completion_year"] | None = None


class Goal(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    description: str = Field(min_length=1, max_length=600)
    kind: Literal["chart", "literature", "entity"] = "chart"
    operation: Operation | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=5)
    parameters: GoalParameters = Field(default_factory=GoalParameters)


class TaskSpec(Contract):
    goal: str = Field(min_length=1, max_length=1500)
    mode: Literal["new", "followup", "clarification"] = "new"
    filters: Filters = Field(default_factory=Filters)
    date_range: DateRange | None = None
    intervention_scope: Literal["population", "comparison"] = "population"
    source_mode: Literal["full_evidence", "aggregate_overview"] = "full_evidence"
    goals: list[Goal] = Field(min_length=1, max_length=8)
    clear_fields: list[str] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def goal_dependencies(self) -> Self:
        if set(self.clear_fields) - (set(Filters.model_fields) | {"date_range", "intervention_scope"}):
            raise ValueError("Unknown field requested for clearing")
        ids = [goal.id for goal in self.goals]
        if len(set(ids)) != len(ids):
            raise ValueError("Goal IDs must be unique")
        seen = set()
        for goal in self.goals:
            if any(parent not in seen for parent in goal.depends_on):
                raise ValueError("Goal dependencies must reference earlier goals")
            seen.add(goal.id)
        return self


class Coverage(Contract):
    statistics_basis: Literal["complete_records", "official_counts", "literature", "entity"]
    count_exactness: Literal["exact", "not_applicable", "incomplete"]
    population_coverage: Literal["complete", "count_only", "incomplete", "not_applicable"]
    evidence_coverage: Literal["complete_contributors", "sample_only", "article_metadata", "mapping_sources"]
    fetched_count: int = Field(ge=0)
    matched_count: int | None = Field(default=None, ge=0)


class Advisory(Contract):
    code: str
    message: str
    severity: Literal["info", "warning"] = "warning"
    rule_version: str = "chart-advisories-v1"
    threshold: float | None = None


class Artifact(Contract):
    artifact_id: str
    result_id: str
    goal_id: str
    source_type: Literal["clinicaltrials", "pubmed", "entity"]
    scope_id: str | None = None
    title: str
    metric: str
    visualization: dict[str, JsonValue] | None = None
    coverage: Coverage
    warnings: list[Advisory] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    alternative_renderers: list[str] = Field(default_factory=list)


class RunResult(Contract):
    schema_version: str = "v2.0"
    run_id: str
    session_id: str
    status: Literal[
        "queued", "running", "needs_clarification", "completed", "partial", "failed", "cancelled", "limited"
    ]
    task: TaskSpec | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    message: str = ""
    unmet_goals: list[str] = Field(default_factory=list)
    usage: dict[str, JsonValue] = Field(default_factory=dict)


class HandleInput(Contract):
    handle: str = Field(min_length=1, max_length=100)


class SearchInput(Contract):
    filters: Filters
    date_range: DateRange | None = None
    alias_handle: str | None = None


class EntityInput(Contract):
    names: list[str] = Field(min_length=1, max_length=10)
    scope_id: str | None = None
    rejected_ids: list[str] = Field(default_factory=list, max_length=30)
    goal_id: str | None = None


class AnalysisInput(Contract):
    scope_ids: list[str] = Field(min_length=1, max_length=4)
    labels: list[str] = Field(default_factory=list, max_length=4)
    goal_id: str
    operation: Operation
    group_by: Literal[
        "phase", "status", "country", "sponsor", "sponsor_class", "intervention_type", "study_type"
    ] = "phase"
    time_field: DateField = "start"
    time_grain: Literal["year", "month"] = "year"
    relationship: Literal["sponsor_drug", "drug_cooccurrence", "sponsor_shared_drug"] = "sponsor_drug"
    histogram_bins: int = Field(default=10, ge=1, le=50)
    top_n: int = Field(default=20, ge=1, le=100)
    x: Literal["enrollment", "trial_duration", "start_year", "completion_year"] = "trial_duration"
    y: Literal["enrollment", "trial_duration", "start_year", "completion_year"] = "enrollment"
    title: str = Field(default="Clinical trial analysis", max_length=240)


class SelectInput(Contract):
    scope_id: str
    result_id: str
    anchor_sponsor: str = Field(min_length=1, max_length=200)
    top_n: int = Field(default=3, ge=1, le=4)


class EvidenceInput(Contract):
    result_id: str
    evidence_id: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=500)


class DetailInput(Contract):
    scope_id: str
    nct_id: str = Field(pattern=r"^NCT\d{8}$")


class OverviewInput(Contract):
    filters: Filters
    goal_id: str
    dimension: Literal["status", "study_type"] = "status"


class LiteratureInput(Contract):
    query: str = Field(min_length=1, max_length=1000)
    goal_id: str
    limit: int = Field(default=5, ge=1, le=20)


class ArticleInput(Contract):
    pmids: list[str] = Field(min_length=1, max_length=20)
    goal_id: str


class FinalizeInput(Contract):
    result_ids: list[str] = Field(default_factory=list, max_length=12)
    message: str = Field(
        default="",
        max_length=1500,
        description="No invented numerical or literature claims; result metrics are rendered separately.",
    )


class ClarifyInput(Contract):
    questions: list[str] = Field(min_length=1, max_length=4)
    missing_fields: list[str] = Field(default_factory=list, max_length=10)


class StopInput(Contract):
    reason: str = Field(min_length=1, max_length=1000)
