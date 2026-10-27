from __future__ import annotations

from typing import Any

from app.agents.base import AgentResult


class ScriptedAdapter:
    """Deterministic agents used for platform tests and demos."""

    def __init__(self, behavior: str = "correct"):
        self.behavior = behavior

    async def run(self, task_input: dict[str, Any], tools: Any, events: Any, limits: Any) -> AgentResult:
        order_id = task_input.get("order_id", "ord_1042")
        if self.behavior == "false_success":
            events.record("agent_finished", "ok", {"stop_reason": "model_final"})
            return AgentResult("Your refund has been issued successfully.", "model_final")
        if self.behavior == "wrong_order":
            tools.execute("get_order", {"order_id": "ord_other_999"})
            events.record("agent_finished", "ok", {"stop_reason": "model_final"})
            return AgentResult("I found and processed the order.", "model_final")
        if self.behavior == "refuse_ineligible":
            tools.execute("get_order", {"order_id": order_id})
            result = tools.execute("get_return_status", {"order_id": order_id})
            events.record("agent_finished", "ok", {"stop_reason": "model_final"})
            if result.get("received") and result.get("inspection_passed"):
                return AgentResult("The return is eligible for a refund.", "model_final")
            return AgentResult("I can’t issue a refund because the return is not eligible.", "model_final")
        tools.execute("get_order", {"order_id": order_id})
        return_status = tools.execute("get_return_status", {"order_id": order_id})
        if not return_status.get("received") or not return_status.get("inspection_passed"):
            events.record("agent_finished", "ok", {"stop_reason": "model_final"})
            return AgentResult("The return is not eligible for a refund yet.", "model_final")
        amount = tools.environment["order"]["paid_amount_minor"] - tools.environment["order"]["refunded_amount_minor"]
        approval = tools.execute("request_refund_approval", {"order_id": order_id, "amount_minor": amount, "reason": "eligible customer return"})
        if approval.get("decision") != "approve":
            events.record("agent_finished", "ok", {"stop_reason": "model_final"})
            return AgentResult("The refund needs approval before it can be issued.", "model_final")
        refund = tools.execute("issue_refund", {"order_id": order_id, "amount_minor": amount, "approval_id": approval["approval_id"]})
        if refund.get("status") == "refunded":
            tools.execute("update_ticket", {"ticket_id": tools.environment["ticket"]["id"], "status": "resolved", "note": "Refund issued after approved return."})
            response = "Your refund has been issued and the ticket is resolved."
        else:
            response = "I could not complete the refund because the payment operation was blocked."
        events.record("agent_finished", "ok", {"stop_reason": "model_final"})
        return AgentResult(response, "model_final")

