from __future__ import annotations

import argparse
import asyncio
import hashlib

from app.config import DATABASE_URL
from app.execution.worker import drain
from app.store import Store


def smoke() -> None:
    store = Store(DATABASE_URL)
    datasets = store.list_datasets()
    configs = store.list_configs()
    if not datasets or not configs:
        raise SystemExit("Run python -m app.seed first")
    exp = store.create_experiment(datasets[0]["dataset_version_id"], [c["id"] for c in configs], "all", 1, "cli-smoke")
    processed = asyncio.run(drain(store))
    print({"experiment_id": exp["id"], "processed_jobs": processed, "summary": store.experiment_summary(exp["id"])})


def run_llm(provider: str, model: str, repetitions: int) -> None:
    store = Store(DATABASE_URL)
    datasets = store.list_datasets()
    if not datasets:
        raise SystemExit("Run python -m app.seed first")
    config_id = f"cfg_llm_{provider}"
    if not store.get_config(config_id):
        store.create_config(config_id, f"{provider} LLM support agent", "llm", {"provider": provider, "model": model, "temperature": 0, "limits": {"max_steps": 8}})
    experiment_key = "cli-llm-" + hashlib.sha256(f"{provider}:{model}:{repetitions}".encode()).hexdigest()[:16]
    exp = store.create_experiment(datasets[0]["dataset_version_id"], [config_id], "all", repetitions, experiment_key)
    processed = asyncio.run(drain(store))
    print({"experiment_id": exp["id"], "processed_jobs": processed, "summary": store.experiment_summary(exp["id"])})


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("smoke")
    llm_parser = subparsers.add_parser("run-llm")
    llm_parser.add_argument("--provider", choices=["gemini", "anthropic"], required=True)
    llm_parser.add_argument("--model", required=True)
    llm_parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    if args.command == "smoke":
        smoke()
    elif args.command == "run-llm":
        run_llm(args.provider, args.model, args.repetitions)


if __name__ == "__main__":
    main()

