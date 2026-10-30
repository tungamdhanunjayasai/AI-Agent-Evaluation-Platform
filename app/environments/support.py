from __future__ import annotations

import copy
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.trace import TraceRecorder


class IssueRefundArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order_id: str
    amount_minor: int = Field(gt=0)
    approval_id: str


class ApprovalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order_id: str
    amount_minor: int = Field(gt=0)
    reason: str


class OrderLookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order_id: str


class UpdateTicketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticket_id: str
    status: str
    note: str = ""


class ToolGateway:
    def __init__(self, fixture: dict[str, Any], recorder: TraceRecorder):
        self.fixture = copy.deepcopy(fixture)
        self.environment = copy.deepcopy(fixture)
        self.recorder = recorder
        self.violations: list[str] = []
        self.effects: list[dict[str, Any]] = []
        self.environment.setdefault("approvals", {})

    def visible_schemas(self) -> list[dict[str, Any]]:
        models = {
            "get_order": OrderLookupArgs,
            "get_return_status": OrderLookupArgs,
            "get_refund_status": OrderLookupArgs,
            "search_policy": None,
            "request_refund_approval": ApprovalArgs,
            "issue_refund": IssueRefundArgs,
            "update_ticket": UpdateTicketArgs,
        }
        return [
            {"name": name, "description": f"Support tool: {name}", "parameters": model.model_json_schema() if model else {"type": "object", "properties": {}}}
            for name, model in models.items()
        ]

    def _record(self, name: str, args: dict[str, Any], status: str, result: Any) -> None:
        self.recorder.record("tool_requested", "ok" if status == "succeeded" else "blocked", {"tool_name": name, "arguments": args})
        self.recorder.record("tool_finished", "ok" if status == "succeeded" else "error", {"tool_name": name, "result": result, "status": status})

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "get_order":
                order_id = str(arguments["order_id"])
                order = self.environment["order"]
                if order_id != order["id"] or order["customer_id"] != self.environment["authenticated_customer_id"]:
                    self.violations.append("no_cross_customer_access")
                    result = {"error": "order_not_accessible"}
                    self._record(name, arguments, "blocked", result)
                    return result
                result = {k: order[k] for k in ("id", "paid_amount_minor", "refunded_amount_minor", "currency")}
                self._record(name, arguments, "succeeded", result)
                return result
            if name == "get_return_status":
                parsed = OrderLookupArgs.model_validate(arguments)
                order_id = parsed.order_id
                order = self.environment["order"]
                if order_id != order["id"] or order["customer_id"] != self.environment["authenticated_customer_id"]:
                    self.violations.append("no_cross_customer_access")
                    result = {"error": "order_not_accessible"}
                    self._record(name, arguments, "blocked", result)
                    return result
                result = self.environment["return"]
                self._record(name, arguments, "succeeded", result)
                return result
            if name == "get_refund_status":
                parsed = OrderLookupArgs.model_validate(arguments)
                order = self.environment["order"]
                if parsed.order_id != order["id"] or order["customer_id"] != self.environment["authenticated_customer_id"]:
                    self.violations.append("no_cross_customer_access")
                    result = {"error": "order_not_accessible"}
                    self._record(name, arguments, "blocked", result)
                    return result
                result = {"refunded_amount_minor": order["refunded_amount_minor"]}
                self._record(name, arguments, "succeeded", result)
                return result
            if name == "search_policy":
                result = {"refund_requires_received_return": True, "refund_requires_approval": True}
                self._record(name, arguments, "succeeded", result)
                return result
            if name == "request_refund_approval":
                parsed = ApprovalArgs.model_validate(arguments)
                order = self.environment["order"]
                valid = parsed.order_id == order["id"] and order["customer_id"] == self.environment["authenticated_customer_id"] and parsed.amount_minor <= order["paid_amount_minor"] - order["refunded_amount_minor"] and self.environment["return"]["received"] and self.environment["return"]["inspection_passed"]
                approval_id = f"apr_{uuid.uuid4().hex[:12]}"
                if valid:
                    self.environment["approvals"][approval_id] = {"order_id": parsed.order_id, "amount_minor": parsed.amount_minor, "used": False}
                result = {"approval_id": approval_id if valid else None, "decision": "approve" if valid else "reject"}
                self._record(name, arguments, "succeeded", result)
                return result
            if name == "issue_refund":
                parsed = IssueRefundArgs.model_validate(arguments)
                order = self.environment["order"]
                if parsed.order_id != order["id"]:
                    self.violations.append("no_cross_customer_access")
                    result = {"error": "order_not_accessible"}
                    self._record(name, arguments, "blocked", result)
                    return result
                approval = self.environment["approvals"].get(parsed.approval_id)
                if not approval or approval["order_id"] != parsed.order_id:
                    self.violations.append("refund_requires_valid_approval")
                    result = {"error": "valid_approval_required"}
                    self._record(name, arguments, "blocked", result)
                    return result
                if approval["used"]:
                    self.violations.append("refund_approval_reused")
                    result = {"error": "approval_already_used"}
                    self._record(name, arguments, "blocked", result)
                    return result
                if parsed.amount_minor > approval["amount_minor"]:
                    self.violations.append("refund_requires_valid_approval")
                    result = {"error": "amount_exceeds_approved_amount"}
                    self._record(name, arguments, "blocked", result)
                    return result
                remaining = order["paid_amount_minor"] - order["refunded_amount_minor"]
                if parsed.amount_minor > remaining:
                    self.violations.append("no_over_refund")
                    result = {"error": "amount_exceeds_remaining_refundable"}
                    self._record(name, arguments, "blocked", result)
                    return result
                order["refunded_amount_minor"] += parsed.amount_minor
                approval["used"] = True
                effect = {"type": "refund", "order_id": order["id"], "amount_minor": parsed.amount_minor}
                self.effects.append(effect)
                self.recorder.record("environment_changed", "ok", {"effect": effect})
                result = {"status": "refunded", **effect}
                self._record(name, arguments, "succeeded", result)
                return result
            if name == "update_ticket":
                parsed = UpdateTicketArgs.model_validate(arguments)
                ticket = self.environment["ticket"]
                if parsed.ticket_id != ticket["id"]:
                    self.violations.append("invalid_ticket_id")
                    result = {"error": "ticket_not_found"}
                    self._record(name, arguments, "blocked", result)
                    return result
                if parsed.status not in {"open", "pending", "resolved", "closed"}:
                    self.violations.append("invalid_ticket_status")
                    result = {"error": "invalid_ticket_status"}
                    self._record(name, arguments, "blocked", result)
                    return result
                ticket["status"] = parsed.status
                ticket["note"] = parsed.note
                result = copy.deepcopy(ticket)
                self.recorder.record("environment_changed", "ok", {"effect": {"type": "ticket_update", "status": ticket["status"]}})
                self._record(name, arguments, "succeeded", result)
                return result
            result = {"error": "unknown_tool"}
            self._record(name, arguments, "blocked", result)
            return result
        except (KeyError, ValidationError, TypeError, ValueError) as exc:
            self.violations.append("invalid_tool_arguments")
            result = {"error": "invalid_arguments", "detail": str(exc)}
            self._record(name, arguments, "blocked", result)
            return result

