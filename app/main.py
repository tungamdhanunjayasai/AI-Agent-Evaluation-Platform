from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.config import DATABASE_URL, DATASET_PATH
from app.reports import export_csv, export_json
from app.schemas import AgentConfigCreate, ComparisonRequest, DatasetImportRequest, ExperimentCreate
from app.store import Store, new_id, utc_now

store = Store(DATABASE_URL)
app = FastAPI(title="Agent Evaluation Platform", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    datasets = store.list_datasets()
    configs = store.list_configs()
    return f"""<!doctype html><html><head><title>Agent Evaluation Platform</title><style>body{{font:16px system-ui;max-width:900px;margin:40px auto;padding:0 20px}}code{{background:#f1f5f9;padding:2px 5px}}.card{{border:1px solid #ddd;border-radius:10px;padding:16px;margin:12px 0}}</style></head><body><h1>Agent Evaluation Platform</h1><p>Deterministic support-agent evaluation dashboard.</p><div class='card'><b>Datasets</b><p>{len(datasets)} version(s)</p><pre>{json.dumps(datasets, indent=2)}</pre></div><div class='card'><b>Agent configurations</b><p>{len(configs)} config(s)</p><pre>{json.dumps(configs, indent=2)}</pre></div><p>Use <a href='/docs'>/docs</a> to create an experiment, then run <code>python -m app.execution.worker --drain</code>.</p></body></html>"""


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    store.init_db()
    return {"status": "ready"}


@app.post("/datasets/import")
def import_dataset(request: DatasetImportRequest) -> dict[str, str]:
    try:
        version_id = store.upsert_dataset(request.dataset_id, request.version, request.name, request.tasks)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"dataset_version_id": version_id, "task_count": str(len(request.tasks))}


@app.get("/datasets")
def datasets() -> list[dict]:
    return store.list_datasets()


@app.post("/agent-configs")
def create_config(request: AgentConfigCreate) -> dict:
    config_id = request.id or new_id("cfg")
    try:
        return store.create_config(config_id, request.name, request.adapter, request.settings)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/agent-configs")
def configs() -> list[dict]:
    return store.list_configs()


@app.post("/experiments")
def create_experiment(request: ExperimentCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict:
    try:
        return store.create_experiment(request.dataset_version_id, request.agent_config_ids, request.split, request.repetitions, idempotency_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/experiments/{experiment_id}")
def experiment(experiment_id: str) -> dict:
    result = store.get_experiment(experiment_id)
    if not result:
        raise HTTPException(404, "experiment not found")
    result["summary"] = store.experiment_summary(experiment_id)
    return result


@app.get("/experiments/{experiment_id}/runs")
def experiment_runs(experiment_id: str) -> list[dict]:
    if not store.get_experiment(experiment_id):
        raise HTTPException(404, "experiment not found")
    return store.list_runs(experiment_id)


@app.post("/experiments/{experiment_id}/cancel")
def cancel_experiment(experiment_id: str) -> dict[str, str]:
    with store.connect() as conn:
        with conn.transaction():
            changed = conn.execute("UPDATE jobs SET status = 'cancelled', finished_at = %s WHERE run_id IN (SELECT id FROM runs WHERE experiment_id = %s) AND status = 'queued'", (utc_now(), experiment_id)).rowcount
            conn.execute("UPDATE runs SET execution_status = 'cancelled' WHERE experiment_id = %s AND execution_status = 'queued'", (experiment_id,))
            conn.execute("UPDATE experiments SET status = 'cancelled' WHERE id = %s", (experiment_id,))
    return {"status": "cancelled", "queued_jobs_cancelled": str(changed)}


@app.get("/runs/{run_id}")
def run(run_id: str) -> dict:
    rows = [r for r in store.list_runs(store.get_run_context(run_id)[0]["experiment_id"]) if r["id"] == run_id]
    if not rows:
        raise HTTPException(404, "run not found")
    result = rows[0]
    if result.get("attempt_id"):
        result["attempt"] = store.get_attempt(result["attempt_id"])
    return result


@app.get("/attempts/{attempt_id}/events")
def attempt_events(attempt_id: str) -> list[dict]:
    if not store.get_attempt(attempt_id):
        raise HTTPException(404, "attempt not found")
    return store.list_events(attempt_id)


@app.get("/experiments/{experiment_id}/export")
def export_experiment(experiment_id: str, format: str = Query("json", pattern="^(json|csv)$")):
    try:
        if format == "csv":
            return PlainTextResponse(export_csv(store, experiment_id), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={experiment_id}.csv"})
        return export_json(store, experiment_id)
    except KeyError as exc:
        raise HTTPException(404, "experiment not found") from exc


@app.post("/comparisons")
def compare(request: ComparisonRequest) -> dict:
    experiments = []
    for experiment_id in request.experiment_ids:
        if not store.get_experiment(experiment_id):
            raise HTTPException(404, f"experiment not found: {experiment_id}")
        runs = store.list_runs(experiment_id)
        experiments.append({"experiment_id": experiment_id, "summary": store.experiment_summary(experiment_id), "runs": runs})
    by_task: dict[str, list[dict]] = {}
    for experiment in experiments:
        for run in experiment["runs"]:
            by_task.setdefault(run["task_id"], []).append({"experiment_id": experiment["experiment_id"], "evaluation_status": run["evaluation_status"], "agent_config_id": run["agent_config_id"]})
    return {"experiments": [{"experiment_id": item["experiment_id"], "summary": item["summary"]} for item in experiments], "same_task_results": by_task}

