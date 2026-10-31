"""Shared contracts. Models propose plans; only validated operations are executable."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Phase = Literal["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]
Status = Literal[
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "ENROLLING_BY_INVITATION",
    "COMPLETED",
    "SUSPENDED",
    "TERMINATED",
    "WITHDRAWN",
    "UNKNOWN",
    "AVAILABLE",
    "NO_LONGER_AVAILABLE",
    "TEMPORARILY_NOT_AVAILABLE",
    "APPROVED_FOR_MARKETING",
    "WITHHELD",
]
Dimension = Literal[
    "phase", "status", "country", "sponsor", "sponsor_class", "intervention_type", "study_type"
]
FilterField = Literal[
    "condition",
    "interventions",
    "intervention_match",
    "sponsor",
    "phases",
    "statuses",
    "countries",
    "start_year",
    "end_year",
    "enrollment_type",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Filters(Contract):
    condition: str | None = Field(default=None, max_length=200)
    interventions: list[str] = Field(default_factory=list, max_length=10)
    intervention_match: Literal["any", "all"] = "any"
    sponsor: str | None = Field(default=None, max_length=200)
    phases: list[Phase] = Field(default_factory=list)
    statuses: list[Status] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list, max_length=20)
    start_year: int | None = Field(default=None, ge=1800, le=2200)
    end_year: int | None = Field(default=None, ge=1800, le=2200)
    enrollment_type: Literal["ACTUAL", "ESTIMATED"] | None = None

    @model_validator(mode="after")
    def validate_filters(self) -> Self:
        if self.start_year is not None and self.end_year is not None and self.start_year > self.end_year:
            raise ValueError("start_year must not be later than end_year")
        for name in ("interventions", "countries"):
            if any(not item or len(item) > 200 for item in getattr(self, name)):
                raise ValueError(f"{name} must contain nonempty names of at most 200 characters")
        return self


class Cohort(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    label: str = Field(min_length=1, max_length=200)
    filters: Filters


class AnalysisPlan(Contract):
    operation: Literal["category", "time_series", "histogram", "network"]
    cohorts: list[Cohort] = Field(min_length=1, max_length=4)
    group_by: Dimension = "phase"
    time_field: Literal["start_year", "first_posted_year"] = "start_year"
    relationship: Literal["sponsor_drug", "drug_cooccurrence", "sponsor_shared_drug"] = "sponsor_drug"
    histogram_bins: int = Field(default=10, ge=1, le=50)
    top_n: int = Field(default=20, ge=1, le=100)
    title: str = Field(default="Clinical trial analysis", max_length=240)
    assumptions: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if len({item.id for item in self.cohorts}) != len(self.cohorts):
            raise ValueError("cohort IDs must be distinct")
        if len({item.label.casefold() for item in self.cohorts}) != len(self.cohorts):
            raise ValueError("cohort labels must be distinct for chart series")
        if self.operation == "network" and len(self.cohorts) != 1:
            raise ValueError("network analysis supports exactly one cohort")
        if any(
            not (c.filters.condition or c.filters.interventions or c.filters.sponsor) for c in self.cohorts
        ):
            raise ValueError(
                "Each cohort needs a condition, intervention, or sponsor to bound source retrieval"
            )
        return self


class FilterPatch(Contract):
    condition: str | None = None
    interventions: list[str] | None = None
    intervention_match: Literal["any", "all"] | None = None
    sponsor: str | None = None
    phases: list[Phase] | None = None
    statuses: list[Status] | None = None
    countries: list[str] | None = None
    start_year: int | None = None
    end_year: int | None = None
    enrollment_type: Literal["ACTUAL", "ESTIMATED"] | None = None


class PlanPatch(Contract):
    # None means all cohorts. Explicit clear_fields differs from omitted/None values.
    cohort_id: str | None = None
    set_filters: FilterPatch = Field(default_factory=FilterPatch)
    clear_fields: list[FilterField] = Field(default_factory=list)
    operation: Literal["category", "time_series", "histogram", "network"] | None = None
    group_by: Dimension | None = None
    time_field: Literal["start_year", "first_posted_year"] | None = None
    relationship: Literal["sponsor_drug", "drug_cooccurrence", "sponsor_shared_drug"] | None = None
    title: str | None = None
    top_n: int | None = Field(default=None, ge=1, le=100)
    histogram_bins: int | None = Field(default=None, ge=1, le=50)
    cohort_label: str | None = Field(default=None, min_length=1, max_length=200)
    assumptions: list[str] | None = Field(default=None, max_length=20)

    def apply(self, previous: AnalysisPlan) -> AnalysisPlan:
        updated = previous.model_dump()
        if self.cohort_id and not any(c.id == self.cohort_id for c in previous.cohorts):
            raise ValueError("Patch refers to an unknown cohort")
        changes = self.set_filters.model_dump(exclude_none=True)
        if set(changes) & set(self.clear_fields):
            raise ValueError("A filter cannot be set and cleared in the same patch")
        defaults = Filters().model_dump()
        for cohort in updated["cohorts"]:
            if self.cohort_id is None or cohort["id"] == self.cohort_id:
                cohort["filters"].update({key: defaults[key] for key in self.clear_fields})
                cohort["filters"].update(changes)
                if self.cohort_label is not None:
                    cohort["label"] = self.cohort_label
        # Narrative assumptions describe the old population/operation and cannot be inherited.
        updated["assumptions"] = self.assumptions or []
        if self.title is None:
            updated["title"] = "Clinical trial analysis"
        for key in (
            "operation",
            "group_by",
            "time_field",
            "relationship",
            "title",
            "top_n",
            "histogram_bins",
        ):
            if getattr(self, key) is not None:
                updated[key] = getattr(self, key)
        return AnalysisPlan.model_validate(updated)


class PlannerDecision(Contract):
    action: Literal["execute", "clarify", "unsupported"]
    mode: Literal["new", "refine", "clarification"]
    plan: AnalysisPlan | None = None
    patch: PlanPatch | None = None
    questions: list[str] = Field(default_factory=list, max_length=5)
    explanation: str = Field(default="", max_length=1500)


class AnalysisRequest(Contract):
    query: str = Field(min_length=1, max_length=4000)
    filters: Filters | None = None
    intervention_scope: Literal["population", "comparison"] = "population"
    session_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    parent_run_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    language: Literal["zh", "en"] = "en"


class SourceField(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    path: str
    value: JsonValue


class Citation(Contract):
    dataset_id: str
    nct_id: str
    url: str
    fields: list[SourceField]
    missing_fields: list[str] = Field(default_factory=list)


class ChartRow(Contract):
    id: str
    cohort: str
    category: str | None = None
    year: int | None = None
    bin_start: float | None = None
    bin_end: float | None = None
    trial_count: int = Field(ge=0)
    evidence_set_id: str
    contributor_count: int = Field(ge=0)


class Encoding(Contract):
    field: str
    type: Literal["nominal", "ordinal", "quantitative", "temporal", "identifier"]
    title: str


class TabularVisualization(Contract):
    type: Literal["bar_chart", "grouped_bar", "time_series", "histogram"]
    title: str
    encoding: dict[str, Encoding]
    data: list[ChartRow]


class NetworkNode(Contract):
    id: str
    label: str
    entity_type: Literal["sponsor", "intervention"]
    trial_count: int
    evidence_set_id: str


class NetworkEdge(Contract):
    id: str
    source: str
    target: str
    weight: int
    weight_metric: Literal["distinct_trial_count", "shared_intervention_count"]
    shared_interventions: list[str] = Field(default_factory=list)
    evidence_set_id: str
    contributor_count: int


class NetworkData(Contract):
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]


class NetworkVisualization(Contract):
    type: Literal["network_graph"] = "network_graph"
    title: str
    encoding: dict[str, Encoding]
    data: NetworkData


Visualization = Annotated[TabularVisualization | NetworkVisualization, Field(discriminator="type")]


class AnalysisResponse(Contract):
    # Request-oriented trimming must not alter verbatim provenance values.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    status: Literal["ok", "needs_clarification", "unsupported", "no_data", "incomplete", "failed"]
    run_id: str
    session_id: str
    visualization: Visualization | None = None
    meta: dict[str, JsonValue] = Field(default_factory=dict)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)
    message: str = ""
