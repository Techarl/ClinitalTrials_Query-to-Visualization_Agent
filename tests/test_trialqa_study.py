import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.sources import SnapshotStore
from app.storage import StateStore
from app.v2.agent import V2Agent
from evaluation.runtime import PersistentBudget
from evaluation.trialqa_study import TaggedBudget, grouped_split, run_case, summarize


def fixture_data():
    raw = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Demo treatment"},
            "eligibilityModule": {"eligibilityCriteria": "Age at least 18 years."},
        }
    }
    task = {
        "id": "test-question",
        "question": "What is the minimum age for Demo treatment?",
        "nct_ids": ["NCT00000001"],
    }
    return task, {"NCT00000001": raw}


def service(config):
    return SimpleNamespace(
        store=StateStore(config.data_directory),
        snapshots=SnapshotStore(config.data_directory),
        close=lambda: None,
    )


class ReaderPlanner:
    payloads = []

    def __init__(self, settings, budget):
        self.budget = budget

    def choose(self, context, definitions, deadline):
        payload = json.loads(context["payload"])
        self.payloads.append(payload)
        observations = payload["observations"]
        if not payload["task"]:
            action = {
                "name": "define_task",
                "arguments": {
                    "goal": "Answer minimum age",
                    "goals": [{"id": "answer", "description": "Cited minimum age", "kind": "answer"}],
                },
            }
        elif not any(o.get("matches") for o in observations):
            action = {"name": "lookup_trials", "arguments": {"query": "Demo treatment"}}
        elif not any(o.get("tool") == "read_source" for o in observations):
            handle = next(o["matches"][0]["source_handle"] for o in observations if o.get("matches"))
            action = {
                "name": "read_source",
                "arguments": {
                    "source_handle": handle,
                    "pointer": "/protocolSection/eligibilityModule/eligibilityCriteria",
                },
            }
        elif not payload["result_ids"]:
            read = next(o for o in observations if o.get("tool") == "read_source")
            action = {
                "name": "publish_text_answer",
                "arguments": {
                    "goal_id": "answer",
                    "answer": "Minimum age is 18 years.",
                    "findings": [
                        {
                            "source_handle": read["source_handle"],
                            "pointer": read["pointer"],
                            "quotation": read["text"],
                        }
                    ],
                },
            }
        else:
            action = {"name": "finalize_analysis", "arguments": {"result_ids": payload["result_ids"]}}
        return action, {"usage": None}

    def close(self):
        pass


def test_group_split_is_transitive_and_order_independent():
    tasks = [
        {"id": "a", "nct_ids": ["one", "two"]},
        {"id": "b", "nct_ids": ["two", "three"]},
        {"id": "c", "nct_ids": ["three"]},
        {"id": "d", "nct_ids": ["four"]},
    ]
    split = grouped_split(tasks)
    assert split == grouped_split(list(reversed(tasks)))
    assert split["a"] == split["b"] == split["c"]
    assert split["d"]["group"] != split["a"]["group"]


@pytest.mark.parametrize("arm,calls,lookups", [("given_sources", 4, 0), ("frozen_retrieval", 5, 1)])
def test_actual_agent_reads_and_publishes_without_gold(tmp_path, arm, calls, lookups):
    task, records = fixture_data()
    config = Settings(_env_file=None, app_data_dir=tmp_path, v2_max_estimated_usd=5)
    budget = PersistentBudget(tmp_path / "ledger.json", 1.5)
    ReaderPlanner.payloads = []
    row = run_case(task, arm, records, config, tmp_path / arm, budget, "fixed", service, ReaderPlanner)
    assert row["status"] == "completed"
    assert row["answer"] == "Minimum age is 18 years."
    assert row["quote_integrity"]
    assert row["source_reads"] == 1
    assert len(row["lookup_queries"]) == lookups
    assert len(ReaderPlanner.payloads) == calls
    assert "reference_answer" not in json.dumps(ReaderPlanner.payloads)
    first = ReaderPlanner.payloads[0]
    if arm == "frozen_retrieval":
        assert "NCT00000001" not in json.dumps(first)
    repeated = run_case(task, arm, records, config, tmp_path / arm, budget, "fixed", service, ReaderPlanner)
    assert repeated["state"]["run_id"] == row["state"]["run_id"]
    assert len(ReaderPlanner.payloads) == calls
    with pytest.raises(ValueError, match="identity changed"):
        run_case(
            {**task, "question": "Different question"},
            arm,
            records,
            config,
            tmp_path / arm,
            budget,
            "fixed",
            service,
            ReaderPlanner,
        )


def test_interrupted_run_resumes_same_persisted_run(tmp_path, monkeypatch):
    task, records = fixture_data()
    config = Settings(_env_file=None, app_data_dir=tmp_path, v2_max_estimated_usd=5)
    budget = PersistentBudget(tmp_path / "ledger.json", 1.5)

    class CrashAgent(V2Agent):
        def run(self, rid):
            self.choose({"run_id": rid})
            raise KeyboardInterrupt("simulated process loss after durable decision")

    monkeypatch.setattr("evaluation.trialqa_study.V2Agent", CrashAgent)
    directory = tmp_path / "resume"
    with pytest.raises(KeyboardInterrupt):
        run_case(task, "given_sources", records, config, directory, budget, "fixed", service, ReaderPlanner)
    run_id = json.loads((directory / "run.json").read_text())["run_id"]
    monkeypatch.setattr("evaluation.trialqa_study.V2Agent", V2Agent)
    row = run_case(task, "given_sources", records, config, directory, budget, "fixed", service, ReaderPlanner)
    assert row["status"] == "completed"
    assert row["state"]["run_id"] == run_id
    assert sum(e["input"].get("name") == "read_source" for e in row["journal"]) == 1


def test_failure_rows_remain_in_denominator():
    tasks = [{"id": "a"}, {"id": "b"}]
    splits = {"a": {"split": "heldout"}, "b": {"split": "heldout"}}
    rows = [
        {
            "id": "a",
            "arm": "given_sources",
            "status": "completed",
            "quote_integrity": True,
            "judgment": {"correct": True, "citations_support_answer": True},
        },
        {"id": "b", "arm": "given_sources", "status": "runner_failed"},
    ]
    report = summarize(rows, tasks, splits)
    assert report["given_sources"]["planned"] == report["frozen_retrieval"]["planned"] == 2
    assert report["given_sources"]["correct"] == 1
    assert report["given_sources"]["judge_failed_or_missing"] == 1
    assert report["frozen_retrieval"]["judge_failed_or_missing"] == 2


def test_unknown_usage_retains_attributed_reservation(tmp_path):
    shared = PersistentBudget(tmp_path / "ledger.json", 1.5)
    tagged = TaggedBudget(shared, case_id="a", arm="given_sources", stage="runtime")
    event = tagged.before(1000, 50)
    reserved = shared.estimated_usd
    tagged.after(event, {"usage": None})
    saved = json.loads(shared.path.read_text())
    assert saved["estimated_or_reserved_usd"] == reserved
    assert saved["events"][0]["case_id"] == "a"
    assert saved["events"][0]["status"] == "completed_usage_unknown"


def test_tagged_budget_supports_production_planner_interface(tmp_path, monkeypatch):
    from evaluation.runtime import BudgetedPlanner

    class Native:
        def __init__(self, settings):
            pass

        def choose(self, context, definitions, deadline):
            return {"kind": "finish"}, {"usage": None}

    monkeypatch.setattr("evaluation.runtime.NativePlanner", Native)
    shared = PersistentBudget(tmp_path / "ledger.json", 1.5)
    planner = BudgetedPlanner(None, TaggedBudget(shared, case_id="a", arm="given_sources", stage="runtime"))
    planner.arm, planner.episode_id = "given_sources", "a"
    planner.choose({"system": "s", "payload": "p", "run_id": "r"}, [], 999)
    event = json.loads(shared.path.read_text())["events"][0]
    assert event["case_id"] == "a" and event["arm"] == "given_sources"
    assert event["stage"] == "runtime" and event["run_id"] == "r"
