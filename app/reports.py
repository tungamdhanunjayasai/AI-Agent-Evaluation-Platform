from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.store import Store


def export_json(store: Store, experiment_id: str) -> dict[str, Any]:
    experiment = store.get_experiment(experiment_id)
    if not experiment:
        raise KeyError(experiment_id)
    return {"experiment": experiment, "summary": store.experiment_summary(experiment_id), "runs": store.export_rows(experiment_id)}


def export_csv(store: Store, experiment_id: str) -> str:
    rows = store.export_rows(experiment_id)
    output = io.StringIO()
    fields = ["id", "task_id", "category", "agent_config_id", "repetition", "execution_status", "evaluation_status", "final_response", "stop_reason"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})
    return output.getvalue()

