from copy import deepcopy

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--live-neo4j",
        action="store_true",
        default=False,
        help="Run isolated temporary graph fixtures against configured Neo4j",
    )


@pytest.fixture
def studies():
    """Synthetic records, never presented as ClinicalTrials.gov query results."""
    first = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Synthetic fixture 1"},
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE1", "PHASE2"],
                "enrollmentInfo": {"count": 100, "type": "ACTUAL"},
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "startDateStruct": {"date": "2020-03", "type": "ACTUAL"},
                "studyFirstPostDateStruct": {"date": "2019-12-01"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Synthetic Fixture Sponsor A", "class": "INDUSTRY"}
            },
            "conditionsModule": {"conditions": ["Synthetic condition"]},
            "armsInterventionsModule": {
                "interventions": [
                    {"name": "Synthetic Pembro", "type": "BIOLOGICAL"},
                    {"name": "Synthetic Drug A", "type": "DRUG"},
                    {"name": "Synthetic Drug A", "type": "DRUG"},
                ]
            },
            "contactsLocationsModule": {
                "locations": [
                    {"country": "United States"},
                    {"country": "United States"},
                    {"country": "Canada"},
                ]
            },
        }
    }
    second = deepcopy(first)
    second["protocolSection"]["identificationModule"]["nctId"] = "NCT00000002"
    second["protocolSection"]["designModule"]["phases"] = ["PHASE3"]
    second["protocolSection"]["designModule"]["enrollmentInfo"] = {"count": 0, "type": "ESTIMATED"}
    second["protocolSection"]["statusModule"]["startDateStruct"] = {"date": "2022", "type": "ESTIMATED"}
    second["protocolSection"]["sponsorCollaboratorsModule"]["leadSponsor"]["name"] = (
        "Synthetic Fixture Sponsor B"
    )
    second["protocolSection"]["armsInterventionsModule"]["interventions"] = [
        {"name": "Synthetic Pembro", "type": "BIOLOGICAL"}
    ]
    second["protocolSection"]["contactsLocationsModule"]["locations"] = [{"country": "United States"}]
    third = deepcopy(first)
    third["protocolSection"]["identificationModule"]["nctId"] = "NCT00000003"
    third["protocolSection"]["designModule"] = {"studyType": "OBSERVATIONAL"}
    third["protocolSection"]["statusModule"] = {"overallStatus": "COMPLETED"}
    third["protocolSection"]["armsInterventionsModule"]["interventions"] = [
        {"name": "Synthetic Drug C", "type": "DRUG"}
    ]
    third["protocolSection"]["contactsLocationsModule"] = {}
    return [first, second, third]
