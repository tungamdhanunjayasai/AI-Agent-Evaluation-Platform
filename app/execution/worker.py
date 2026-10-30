from __future__ import annotations

import argparse
import asyncio
import copy
import signal
from typing import Any

from app.agents.providers import ProviderError
from app.agents.scripted import ScriptedAdapter
from app.config import DATABASE_URL
from app.environments.support import ToolGateway
from app.evaluators.support import evaluate
from app.store import Store, new_id
from app.trace import TraceRecorder


async def process_job(store: Store, job: dict[str, Any]) -> str:
    run: dict[str, Any] | None = None
    task: dict[str, Any] | None = None
    gateway: ToolGateway | None = None
    recorder: TraceRecorder | None = None
    attempt_id: str | None = None
    result = None
    try:
        run, task, config, _experiment = store.get_run_context(job["run_id"])
        fixture = copy.deepcopy(task["environment_fixture"])
        fixture["authenticated_customer_id"] = task["agent_input"]["authenticated_customer_id"]
        hidden_fixture = {"environment_fixture": copy.deepcopy(fixture), "evaluation": copy.deepcopy(task.get("evaluation", {}))}
        attempt_id = store.create_attempt(run["id"], new_id("env"), fixture, hidden_fixture)
        recorder = TraceRecorder(store, attempt_id)
        recorder.record("attempt_started", "ok", {"run_id": run["id"], "task_id": task["id"]})
        gateway = ToolGateway(fixture, recorder)
        settings = config.get("settings_json", {}) if config else {}
        if config and config.get("adapter") == "llm":
            from app.agents.llm import LLMAdapter

            adapter = LLMAdapter.from_settings(settings)
        else:
            adapter = ScriptedAdapter(settings.get("behavior", "correct"))
        result = await adapter.run(task["agent_input"], gateway, recorder, None)
        if result.stop_reason == "model_error":
            raise RuntimeError(result.metadata.get("error", "model provider error"))
        attempt = {"hidden_fixture_json": hidden_fixture, "environment_json": gateway.environment, "final_response": result.final_response, "events": store.list_events(attempt_id), "violations": gateway.violations, "effects": gateway.effects}
        store.finish_attempt(attempt_id, "completed", result.stop_reason, result.final_response, gateway.environment)
        recorder.record("attempt_finished", "ok", {"execution_status": "completed", "stop_reason": result.stop_reason})
        scores = evaluate(attempt)
        for score in scores:
            store.add_score(attempt_id, score["evaluator"], score["status"], score["score"], score["reasons"], score["evidence"])
        evaluation_status = "pass" if all(score["status"] == "pass" for score in scores) else "fail"
        store.finish_run(run["id"], "completed", evaluation_status)
        return attempt_id
    except Exception as exc:
        stop_reason = "model_error" if isinstance(exc, ProviderError) or (result is not None and result.stop_reason == "model_error") else "worker_exception"
        if attempt_id and gateway:
            store.finish_attempt(attempt_id, "infrastructure_error", stop_reason, str(exc), gateway.environment)
            store.add_score(attempt_id, "system", "evaluation_error", None, ["worker_exception"], {"error": str(exc)})
        store.finish_run(job["run_id"], "infrastructure_error", "pending", error=str(exc))
        return attempt_id or ""


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


async def run_loop(store: Store, poll_interval: float = 2.0) -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            pass
    processed = 0
    while not stop_event.is_set():
        count = await drain(store, once=True)
        processed += count
        if count == 0:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            except TimeoutError:
                pass
    return processed


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--drain", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args()
    worker_store = Store(DATABASE_URL)
    if args.loop:
        count = asyncio.run(run_loop(worker_store, args.poll_interval))
    else:
        count = asyncio.run(drain(worker_store, once=args.once))
    worker_store.close()
    print(f"processed_jobs={count}")


if __name__ == "__main__":
    main()

