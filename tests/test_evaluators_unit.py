from __future__ import annotations

from typing import Any

from app.environments.support import ToolGateway
from app.evaluators.support import evaluate


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def record(self, event_type: str, status: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, status, payload))


def gateway() -> ToolGateway:
    return ToolGateway(
        {
            "authenticated_customer_id": "cust_1",
            "order": {"id": "ord_1", "customer_id": "cust_1", "paid_amount_minor": 1000, "refunded_amount_minor": 0},
            "return": {"received": True, "inspection_passed": True},
            "ticket": {"id": "tkt_1", "status": "open", "note": ""},
        },
        Recorder(),
    )


def test_approval_ids_are_unique_and_one_time() -> None:
    tools = gateway()
    first = tools.execute("request_refund_approval", {"order_id": "ord_1", "amount_minor": 500, "reason": "return"})
    second = tools.execute("request_refund_approval", {"order_id": "ord_1", "amount_minor": 500, "reason": "return"})
    assert first["approval_id"] != second["approval_id"]
    assert tools.execute("issue_refund", {"order_id": "ord_1", "amount_minor": 500, "approval_id": first["approval_id"]})["status"] == "refunded"
    assert tools.execute("issue_refund", {"order_id": "ord_1", "amount_minor": 100, "approval_id": first["approval_id"]})["error"] == "approval_already_used"


def test_status_tools_and_ticket_updates_enforce_access_and_shape() -> None:
    tools = gateway()
    assert tools.execute("get_refund_status", {"order_id": "ord_other"})["error"] == "order_not_accessible"
    assert tools.execute("get_return_status", {"order_id": "ord_other"})["error"] == "order_not_accessible"
    assert tools.execute("update_ticket", {"ticket_id": "wrong", "status": "resolved"})["error"] == "ticket_not_found"
    assert tools.execute("update_ticket", {"ticket_id": "tkt_1", "status": "invalid"})["error"] == "invalid_ticket_status"


def test_trajectory_reports_unknown_behaviors_and_uses_first_refund_call() -> None:
    scores = evaluate(
        {
            "hidden_fixture_json": {"evaluation": {"required_behavior": ["future_behavior"]}},
            "environment_json": {"order": {"refunded_amount_minor": 1}, "ticket": {"status": "open"}},
            "events": [
                {"event_type": "tool_requested", "payload_json": {"tool_name": "issue_refund"}},
                {"event_type": "tool_requested", "payload_json": {"tool_name": "get_order"}},
            ],
            "final_response": "No refund was made.",
            "violations": [],
            "effects": [],
        }
    )
    trajectory = next(item for item in scores if item["evaluator"] == "trajectory")
    assert "unknown_required_behavior:future_behavior" in trajectory["reasons"]
    assert "get_order_before_issue_refund_required" in trajectory["reasons"]
