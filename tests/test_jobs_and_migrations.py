from __future__ import annotations

import os

import pytest

from app.store import Store

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL to run database tests")


def make_store(max_attempts: int = 3) -> Store:
    return Store(os.environ["TEST_DATABASE_URL"], max_attempts=max_attempts)


def make_task() -> dict:
    return {"id": "job_task", "agent_input": {"user_message": "check", "authenticated_customer_id": "cust", "order_id": "ord"}, "environment_fixture": {"order": {"id": "ord", "customer_id": "cust", "paid_amount_minor": 1, "refunded_amount_minor": 0}, "return": {"received": False, "inspection_passed": False}, "ticket": {"id": "ticket", "status": "open", "note": ""}}, "evaluation": {}}


def test_migrations_are_idempotent_and_stale_leases_reclaim() -> None:
    store = make_store()
    store.init_db()
    store.init_db()
    with store.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS count FROM schema_migrations").fetchone()["count"]
    assert count >= 2
    store.upsert_dataset("jobs", 1, "Jobs", [make_task()])
    store.create_config("jobs_cfg", "Jobs", "scripted", {"behavior": "correct"})
    experiment = store.create_experiment("jobs-v1", ["jobs_cfg"], "all", 1)
    first = store.claim_job()
    assert first is not None
    with store.connect() as conn:
        conn.execute("UPDATE jobs SET lease_expires_at = %s WHERE id = %s", ("2000-01-01T00:00:00+00:00", first["id"]))
    reclaimed = store.claim_job()
    assert reclaimed is not None
    assert reclaimed["attempts_count"] == 2
    assert experiment["id"]


def test_infrastructure_failures_retry_then_mark_job_failed() -> None:
    store = make_store(max_attempts=2)
    store.upsert_dataset("retry", 1, "Retry", [make_task()])
    store.create_config("retry_cfg", "Retry", "scripted", {"behavior": "correct"})
    experiment = store.create_experiment("retry-v1", ["retry_cfg"], "all", 1)
    first = store.claim_job()
    assert first is not None
    run_id = first["run_id"]
    store.finish_run(run_id, "infrastructure_error", "pending", error="first failure")
    second = store.claim_job()
    assert second is not None
    store.finish_run(run_id, "infrastructure_error", "pending", error="second failure")
    with store.connect() as conn:
        job = conn.execute("SELECT status, error FROM jobs WHERE run_id = %s", (run_id,)).fetchone()
    assert job["status"] == "failed"
    assert job["error"] == "second failure"
    assert experiment["id"]
