from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import main
from app.store import Store

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL to run API tests")


def task(task_id: str = "t1") -> dict:
    return {
        "id": task_id,
        "agent_input": {"user_message": "Check order ord_1", "authenticated_customer_id": "cust_1", "order_id": "ord_1"},
        "environment_fixture": {"order": {"id": "ord_1", "customer_id": "cust_1", "paid_amount_minor": 100, "refunded_amount_minor": 0}, "return": {"received": False, "inspection_passed": False}, "ticket": {"id": "t1", "status": "open", "note": ""}},
        "evaluation": {"outcome": {"refund_total_minor": 0, "ticket_status": "open"}},
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    store = Store(os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(main, "store", store)
    return TestClient(main.app)


def test_import_immutability_idempotency_cancel_exports_and_404s(client: TestClient) -> None:
    imported = client.post("/datasets/import", json={"dataset_id": "api", "version": 1, "name": "API", "tasks": [task()]})
    assert imported.status_code == 200
    mutated = client.post("/datasets/import", json={"dataset_id": "api", "version": 1, "name": "API", "tasks": [task("different")]})
    assert mutated.status_code == 409
    config = client.post("/agent-configs", json={"id": "api_cfg", "name": "Scripted", "adapter": "scripted", "settings": {"behavior": "correct"}})
    assert config.status_code == 200
    payload = {"dataset_version_id": "api-v1", "agent_config_ids": ["api_cfg"], "split": "all", "repetitions": 1}
    first = client.post("/experiments", json=payload, headers={"Idempotency-Key": "api-key"})
    second = client.post("/experiments", json=payload, headers={"Idempotency-Key": "api-key"})
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    conflict = client.post("/experiments", json={**payload, "repetitions": 2}, headers={"Idempotency-Key": "api-key"})
    assert conflict.status_code == 400
    experiment_id = first.json()["id"]
    cancelled = client.post(f"/experiments/{experiment_id}/cancel")
    assert cancelled.status_code == 200
    assert isinstance(cancelled.json()["queued_jobs_cancelled"], int)
    assert client.get("/runs/unknown").status_code == 404
    assert client.get("/experiments/unknown").status_code == 404
    assert client.get(f"/experiments/{experiment_id}/export?format=json").status_code == 200
    assert client.get(f"/experiments/{experiment_id}/export?format=csv").status_code == 200
