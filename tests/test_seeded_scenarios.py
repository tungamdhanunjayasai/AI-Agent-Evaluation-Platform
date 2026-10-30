from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.agents.scripted import ScriptedAdapter
from app.environments.support import ToolGateway
from app.evaluators.support import evaluate


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event_type: str, status: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": event_type, "status": status, "payload_json": payload})


def test_seeded_behavior_matrix() -> None:
    tasks = json.loads((Path(__file__).parents[1] / "datasets" / "support" / "tasks.json").read_text(encoding="utf-8"))
    expected_policy_failures = {"wrong_order": len(tasks), "correct": 1, "false_success": 0, "refuse_ineligible": 1}
    for behavior in ("correct", "false_success", "wrong_order", "refuse_ineligible"):
        policy_failures = 0
        for task in tasks:
            recorder = Recorder()
            fixture = dict(task["environment_fixture"], authenticated_customer_id=task["agent_input"]["authenticated_customer_id"])
            gateway = ToolGateway(fixture, recorder)
            result = asyncio.run(ScriptedAdapter(behavior).run(task["agent_input"], gateway, recorder, None))
            scores = evaluate({"hidden_fixture_json": {"evaluation": task["evaluation"]}, "environment_json": gateway.environment, "final_response": result.final_response, "events": recorder.events, "violations": gateway.violations, "effects": gateway.effects})
            policy_failures += next(score for score in scores if score["evaluator"] == "policy")["status"] == "fail"
        assert policy_failures == expected_policy_failures[behavior]
