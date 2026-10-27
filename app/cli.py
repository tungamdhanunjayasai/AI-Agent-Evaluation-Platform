from __future__ import annotations

import argparse
import asyncio

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["smoke"])
    args = parser.parse_args()
    if args.command == "smoke":
        smoke()


if __name__ == "__main__":
    main()

