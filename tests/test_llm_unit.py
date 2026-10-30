from __future__ import annotations

import asyncio
from typing import Any

from app.agents.llm import LLMAdapter
from app.agents.providers import ProviderResponse, ToolCall
from app.environments.support import ToolGateway
from app.evaluators.support import evaluate


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event_type: str, status: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": event_type, "status": status, "payload_json": payload})


class FakeProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = responses
        self.calls = 0

    async def complete(self, **_: Any) -> ProviderResponse:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def make_gateway(recorder: Recorder) -> ToolGateway:
    return ToolGateway(
        {
            "authenticated_customer_id": "cust_1",
            "order": {"id": "ord_1", "customer_id": "cust_1", "paid_amount_minor": 1000, "refunded_amount_minor": 0, "currency": "INR"},
            "return": {"received": True, "inspection_passed": True},
            "ticket": {"id": "tkt_1", "status": "open", "note": ""},
        },
        recorder,
    )


def test_llm_adapter_executes_tool_calls_and_finishes() -> None:
    responses = [
        ProviderResponse(tool_calls=[ToolCall("1", "get_order", {"order_id": "ord_1"})]),
        ProviderResponse(tool_calls=[ToolCall("2", "get_return_status", {"order_id": "ord_1"})]),
        ProviderResponse(tool_calls=[ToolCall("3", "request_refund_approval", {"order_id": "ord_1", "amount_minor": 1000, "reason": "return"})]),
        ProviderResponse(tool_calls=[ToolCall("4", "issue_refund", {"order_id": "ord_1", "amount_minor": 1000, "approval_id": "apr_missing"})]),
        ProviderResponse(text="I could not complete the refund."),
    ]
    recorder = Recorder()
    gateway = make_gateway(recorder)
    result = asyncio.run(LLMAdapter(FakeProvider(responses), "fake").run({"user_message": "Refund ord_1", "authenticated_customer_id": "cust_1"}, gateway, recorder, None))
    assert result.stop_reason == "model_final"
    assert gateway.environment["order"]["refunded_amount_minor"] == 0


def test_llm_false_success_fails_outcome_and_response() -> None:
    recorder = Recorder()
    gateway = make_gateway(recorder)
    result = asyncio.run(LLMAdapter(FakeProvider([ProviderResponse(text="Your refund has been issued successfully.")]), "fake").run({"user_message": "Refund ord_1", "authenticated_customer_id": "cust_1"}, gateway, recorder, None))
    scores = evaluate({"hidden_fixture_json": {"evaluation": {"outcome": {"refund_total_minor": 1000, "ticket_status": "resolved"}}}, "environment_json": gateway.environment, "final_response": result.final_response, "events": recorder.events, "violations": gateway.violations, "effects": gateway.effects})
    assert {score["evaluator"] for score in scores if score["status"] == "fail"} == {"outcome", "response"}


def test_llm_wrong_order_records_policy_violation() -> None:
    recorder = Recorder()
    gateway = make_gateway(recorder)
    asyncio.run(LLMAdapter(FakeProvider([ProviderResponse(tool_calls=[ToolCall("1", "get_order", {"order_id": "ord_other"})]), ProviderResponse(text="I cannot access that order.")]), "fake").run({"user_message": "Check an order", "authenticated_customer_id": "cust_1"}, gateway, recorder, None))
    assert "no_cross_customer_access" in gateway.violations


def test_llm_max_steps_is_enforced() -> None:
    provider = FakeProvider([ProviderResponse(tool_calls=[ToolCall("1", "get_order", {"order_id": "ord_1"})])])
    recorder = Recorder()
    result = asyncio.run(LLMAdapter(provider, "fake", max_steps=2).run({"user_message": "Check", "authenticated_customer_id": "cust_1"}, make_gateway(recorder), recorder, None))
    assert result.stop_reason == "max_steps"
    assert provider.calls == 2
