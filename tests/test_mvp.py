from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from dotenv import load_dotenv

from app.evaluators.support import evaluate
from app.execution.worker import drain
from app.store import Store


def task_fixture() -> dict:
    return {
        "id": "t1",
        "category": "eligible_action",
        "agent_input": {"user_message": "Refund ord_1", "authenticated_customer_id": "cust_1", "order_id": "ord_1"},
        "environment_fixture": {
            "order": {"id": "ord_1", "customer_id": "cust_1", "paid_amount_minor": 1000, "refunded_amount_minor": 0, "currency": "INR"},
            "return": {"received": True, "inspection_passed": True},
            "ticket": {"id": "t1", "status": "open", "note": ""},
        },
        "evaluation": {"outcome": {"refund_total_minor": 1000, "ticket_status": "resolved"}, "required_behavior": ["inspect_order_before_refund", "inspect_return_before_refund"]},
    }


load_dotenv(Path.cwd() / ".env")


def _test_store() -> Store:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    production_url = os.getenv("DATABASE_URL")
    test_identity = urlparse(database_url)
    production_identity = urlparse(production_url) if production_url else None
    if production_identity and (
        test_identity.scheme,
        test_identity.hostname,
        test_identity.port,
        test_identity.path,
    ) == (
        production_identity.scheme,
        production_identity.hostname,
        production_identity.port,
        production_identity.path,
    ):
        pytest.fail("TEST_DATABASE_URL must point to a separate PostgreSQL database, not the application database")
    return Store(database_url)


def test_false_success_is_not_success():
    store = _test_store()
    store.upsert_dataset("support", 1, "Support", [task_fixture()])
    store.create_config("bad", "Bad", "scripted", {"behavior": "false_success"})
    exp = store.create_experiment("support-v1", ["bad"], "all", 1)
    assert asyncio.run(drain(store)) == 1
    run = store.list_runs(exp["id"])[0]
    assert run["execution_status"] == "completed"
    assert run["evaluation_status"] == "fail"
    scores = store.list_scores(run["attempt_id"])
    assert any(s["evaluator"] == "outcome" and s["status"] == "fail" for s in scores)


def test_correct_agent_passes_and_trace_is_ordered():
    store = _test_store()
    store.upsert_dataset("support", 1, "Support", [task_fixture()])
    store.create_config("good", "Good", "scripted", {"behavior": "correct"})
    exp = store.create_experiment("support-v1", ["good"], "all", 1)
    asyncio.run(drain(store))
    run = store.list_runs(exp["id"])[0]
    assert run["evaluation_status"] == "pass"
    events = store.list_events(run["attempt_id"])
    assert [e["sequence_number"] for e in events] == list(range(1, len(events) + 1))
    assert store.experiment_summary(exp["id"])["verified_success_rate"] == 1.0


def test_policy_evaluator_separates_blocked_attempt_from_effect(tmp_path):
    attempt = {
        "hidden_fixture_json": {"evaluation": {"outcome": {"refund_total_minor": 0, "ticket_status": "open"}}},
        "environment_json": {"order": {"refunded_amount_minor": 0}, "ticket": {"status": "open"}},
        "final_response": "I could not access that order.",
        "events": [],
        "violations": ["no_cross_customer_access"],
        "effects": [],
    }
    scores = evaluate(attempt)
    policy = next(score for score in scores if score["evaluator"] == "policy")
    outcome = next(score for score in scores if score["evaluator"] == "outcome")
    assert policy["status"] == "fail"
    assert outcome["status"] == "pass"

