"""SQL oracle checks against explicit raw-record expectations, not product aggregators."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from evaluation.sql_oracle import (
    category_counts,
    expected_scope_ids,
    identity,
    sponsor_edges,
    verify_sql_artifact,
)


@pytest.mark.parametrize(
    "dimension,expected",
    [
        ("phase", {"Phase 1 / Phase 2": 1, "Phase 3": 1, "Not reported": 1}),
        ("status", {"RECRUITING": 2, "COMPLETED": 1}),
        ("study_type", {"INTERVENTIONAL": 2, "OBSERVATIONAL": 1}),
        ("country", {"United States": 2, "Canada": 1, "Not reported": 1}),
    ],
)
def test_sql_category_counts_match_explicit_fixture(dimension, expected, studies):
    assert category_counts(studies, dimension) == expected


def test_country_missing_location_is_not_a_second_category(studies):
    studies[0]["protocolSection"]["contactsLocationsModule"]["locations"].extend(
        [{"facility": "Country omitted"}, {"country": ""}]
    )
    assert category_counts(studies, "country") == {
        "United States": 2,
        "Canada": 1,
        "Not reported": 1,
    }


def test_sql_shared_drug_weight_is_not_trial_pair_count(studies):
    duplicate = deepcopy(studies[1])
    duplicate["protocolSection"]["identificationModule"]["nctId"] = "NCT00000004"
    studies.append(duplicate)
    a = identity("sponsor", "Synthetic Fixture Sponsor A")
    b = identity("sponsor", "Synthetic Fixture Sponsor B")
    assert sponsor_edges(studies) == {tuple(sorted([a, b])): 1}


def test_sql_keeps_drug_and_biological_types_distinct(studies):
    studies[1]["protocolSection"]["armsInterventionsModule"]["interventions"] = [
        {"name": "Synthetic Pembro", "type": "DRUG"}
    ]
    assert sponsor_edges(studies) == {}


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({}, {"NCT00000001", "NCT00000002", "NCT00000003"}),
        ({"phases": ["PHASE2"]}, {"NCT00000001"}),
        ({"statuses": ["COMPLETED"]}, {"NCT00000003"}),
        ({"countries": ["Canada"]}, {"NCT00000001"}),
        ({"enrollment_type": "ESTIMATED"}, {"NCT00000002"}),
        ({"start_year": 2021}, {"NCT00000002"}),
        ({"end_year": 2021}, {"NCT00000001"}),
    ],
)
def test_scope_membership_uses_parent_records(filters, expected, studies):
    assert expected_scope_ids({"filters": filters}, studies) == expected


@pytest.mark.parametrize(
    "lower,upper,expected",
    [
        ("2020-03-01", "2020-03-31", {"NCT00000001"}),
        ("2020-03-02", "2020-03-31", set()),
        ("2022-01-01", "2022-12-31", {"NCT00000002"}),
        ("2022-01-02", "2022-12-31", set()),
    ],
)
def test_scope_date_precision_requires_full_interval(lower, upper, expected, studies):
    scope = {"filters": {}, "date_range": {"field": "start", "lower": lower, "upper": upper}}
    assert expected_scope_ids(scope, studies) == expected


def test_scope_membership_preserves_selected_sponsor_identity(studies):
    scope = {
        "filters": {},
        "lineage": [
            {
                "selection": "top_shared_drug_sponsors",
                "entity_id": identity("sponsor", "Synthetic Fixture Sponsor B"),
            }
        ],
    }
    assert expected_scope_ids(scope, studies) == {"NCT00000002"}


def test_coherent_smaller_chart_cannot_verify_against_wrong_scope(studies):
    scope = {"filters": {}, "label": "Selected", "dataset_id": "source", "nct_ids": ["NCT00000001"]}
    artifact = SimpleNamespace(
        metadata={
            "analysis_parameters": {
                "operation": "category",
                "group_by": "status",
                "labels": [],
                "top_n": 20,
            },
            "root_scopes": ["scope"],
        },
        visualization={
            "data": [
                {"cohort": "Selected", "category": "RECRUITING", "trial_count": 1},
            ]
        },
    )
    registry = SimpleNamespace(
        scope=lambda *_: (scope, None, studies[:1]),
        service=SimpleNamespace(snapshots=SimpleNamespace(load=lambda *_: (None, studies))),
    )
    with pytest.raises(ValueError, match="parent scope membership"):
        verify_sql_artifact(artifact, registry, {})


def test_country_filtered_chart_uses_only_requested_countries(studies):
    scope = {
        "filters": {"countries": ["United States"]},
        "label": "Selected",
        "dataset_id": "source",
        "nct_ids": ["NCT00000001", "NCT00000002"],
    }
    artifact = SimpleNamespace(
        metadata={
            "analysis_parameters": {
                "operation": "category",
                "group_by": "country",
                "labels": [],
                "top_n": 20,
            },
            "root_scopes": ["scope"],
        },
        visualization={
            "data": [
                {"cohort": "Selected", "category": "United States", "trial_count": 2},
            ]
        },
    )
    registry = SimpleNamespace(
        scope=lambda *_: (scope, None, studies[:2]),
        service=SimpleNamespace(snapshots=SimpleNamespace(load=lambda *_: (None, studies))),
    )
    assert verify_sql_artifact(artifact, registry, {})["checked"]
