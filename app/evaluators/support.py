from __future__ import annotations

from typing import Any


def evaluate(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    fixture = attempt["hidden_fixture_json"]
    env = attempt["environment_json"]
    events = attempt.get("events", [])
    tool_names = [e.get("payload_json", {}).get("tool_name") for e in events if e["event_type"] == "tool_requested"]
    expected = fixture.get("evaluation", {})
    expected_outcome = expected.get("outcome", {})
    reasons: list[str] = []
    outcome_ok = True
    if "refund_total_minor" in expected_outcome and env["order"]["refunded_amount_minor"] != expected_outcome["refund_total_minor"]:
        outcome_ok = False
        reasons.append("refund_amount_mismatch")
    if "ticket_status" in expected_outcome and env["ticket"]["status"] != expected_outcome["ticket_status"]:
        outcome_ok = False
        reasons.append("ticket_status_mismatch")
    outcome = {"refund_total_minor": env["order"]["refunded_amount_minor"], "ticket_status": env["ticket"]["status"], "effects": attempt.get("effects", [])}

    policy_reasons = list(attempt.get("violations", []))
    policy_ok = not policy_reasons
    required = expected.get("required_behavior", [])
    mapping = {"inspect_order_before_refund": "get_order", "inspect_return_before_refund": "get_return_status"}
    trajectory_reasons = []
    for requirement in required:
        if mapping.get(requirement) not in tool_names:
            trajectory_reasons.append(f"missing_{requirement}")
    if "issue_refund" in tool_names:
        indices = {name: tool_names.index(name) for name in set(tool_names)}
        for prereq in ("get_order", "get_return_status", "request_refund_approval"):
            if prereq not in indices or indices[prereq] > indices["issue_refund"]:
                trajectory_reasons.append(f"{prereq}_before_issue_refund_required")
    trajectory_ok = not trajectory_reasons

    response = attempt.get("final_response") or ""
    claims_success = any(phrase in response.lower() for phrase in ("refund has been issued", "refund issued", "processed the order"))
    response_ok = not (claims_success and not outcome_ok)
    response_reasons = [] if response_ok else ["false_success_claim"]

    return [
        {"evaluator": "outcome", "status": "pass" if outcome_ok else "fail", "score": 1.0 if outcome_ok else 0.0, "reasons": reasons, "evidence": outcome},
        {"evaluator": "policy", "status": "pass" if policy_ok else "fail", "score": 1.0 if policy_ok else 0.0, "reasons": policy_reasons, "evidence": {"violations": policy_reasons}},
        {"evaluator": "trajectory", "status": "pass" if trajectory_ok else "fail", "score": 1.0 if trajectory_ok else 0.0, "reasons": trajectory_reasons, "evidence": {"tool_names": tool_names}},
        {"evaluator": "response", "status": "pass" if response_ok else "fail", "score": 1.0 if response_ok else 0.0, "reasons": response_reasons, "evidence": {"final_response": response}},
    ]

