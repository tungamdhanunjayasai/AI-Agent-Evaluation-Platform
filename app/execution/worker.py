from __future__ import annotations

import argparse
import asyncio
import copy
from typing import Any

from app.agents.scripted import ScriptedAdapter
from app.config import DATABASE_URL
from app.evaluators.support import evaluate
from app.environments.support import ToolGateway
from app.store import Store, new_id
from app.trace import TraceRecorder


async def process_job(store: Store, job: dict[str, Any]) -> str:
    run, task, config, _experiment = store.get_run_context(job["run_id"])
    fixture = copy.deepcopy(task["environment_fixture"])
    fixture["authenticated_customer_id"] = task["agent_input"]["authenticated_customer_id"]
    hidden_fixture = {"environment_fixture": copy.deepcopy(fixture), "evaluation": copy.deepcopy(task.get("evaluation", {}))}
    attempt_id = store.create_attempt(run["id"], new_id("env"), fixture, hidden_fixture)
    recorder = TraceRecorder(store, attempt_id)
    recorder.record("attempt_started", "ok", {"run_id": run["id"], "task_id": task["id"]})
    gateway = ToolGateway(fixture, recorder)
    behavior = config.get("settings_json", {}).get("behavior", "correct") if config else "correct"
    adapter = ScriptedAdapter(behavior)
    try:
        result = await adapter.run(task["agent_input"], gateway, recorder, None)
        attempt = {"hidden_fixture_json": hidden_fixture, "environment_json": gateway.environment, "final_response": result.final_response, "events": store.list_events(attempt_id), "violations": gateway.violations, "effects": gateway.effects}
        store.finish_attempt(attempt_id, "completed", result.stop_reason, result.final_response, gateway.environment)
        recorder.record("attempt_finished", "ok", {"execution_status": "completed", "stop_reason": result.stop_reason})
        for score in evaluate(attempt):
            store.add_score(attempt_id, score["evaluator"], score["status"], score["score"], score["reasons"], score["evidence"])
        scores = evaluate(attempt)
        evaluation_status = "pass" if all(score["status"] == "pass" for score in scores) else "fail"
        store.finish_run(run["id"], "completed", evaluation_status)
        return attempt_id
    except Exception as exc:
        store.finish_attempt(attempt_id, "infrastructure_error", "worker_exception", str(exc), gateway.environment)
        store.add_score(attempt_id, "system", "evaluation_error", None, ["worker_exception"], {"error": str(exc)})
        store.finish_run(run["id"], "infrastructure_error", "pending")
        return attempt_id


async def drain(store: Store, once: bool = False) -> int:
    processed = 0
    while True:
        job = store.claim_job()
        if not job:
            break
        await process_job(store, job)
        processed += 1
        if once:
            break
    return processed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--drain", action="store_true")
    args = parser.parse_args()
    count = asyncio.run(drain(Store(DATABASE_URL), once=args.once and not args.drain))
    print(f"processed_jobs={count}")


if __name__ == "__main__":
    main()

