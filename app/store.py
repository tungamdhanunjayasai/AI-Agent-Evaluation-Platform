from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row


SCHEMA_PATH = Path(__file__).resolve().parent / "migrations" / "001_initial.sql"
SCHEMA = SCHEMA_PATH.read_text(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    """PostgreSQL-backed persistence for the platform.

    A filesystem path is intentionally not treated as a request to fall back
    to SQLite.
    """

    def __init__(self, database_url: str | None = None):
        if isinstance(database_url, Path):
            raise ValueError("SQLite filesystem paths are no longer supported; pass a PostgreSQL DATABASE_URL")
        self.database_url = database_url
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required; configure a Supabase PostgreSQL connection URL")
        scheme = urlparse(self.database_url).scheme.lower()
        if scheme not in {"postgres", "postgresql"}:
            raise ValueError("DATABASE_URL must use the postgresql:// or postgres:// scheme")
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection[Any]]:
        conn = psycopg.connect(self.database_url, autocommit=True, row_factory=dict_row)
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(SCHEMA)

    @staticmethod
    def row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def decode(row: dict[str, Any] | None, *fields: str) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in fields:
            if field in result and result[field] is not None and isinstance(result[field], str):
                result[field] = json.loads(result[field])
        return result

    def upsert_dataset(self, dataset_id: str, version: int, name: str, tasks: list[dict[str, Any]]) -> str:
        version_id = f"{dataset_id}-v{version}"
        with self.connect() as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO datasets (dataset_version_id, dataset_id, version, name, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (dataset_version_id) DO UPDATE SET
                      dataset_id = EXCLUDED.dataset_id,
                      version = EXCLUDED.version,
                      name = EXCLUDED.name,
                      created_at = EXCLUDED.created_at
                    """,
                    (version_id, dataset_id, version, name, utc_now()),
                )
                conn.execute("DELETE FROM tasks WHERE dataset_version_id = %s", (version_id,))
                for task in tasks:
                    conn.execute(
                        "INSERT INTO tasks (id, dataset_version_id, category, payload_json) VALUES (%s, %s, %s, %s)",
                        (task["id"], version_id, task.get("category", "general"), json.dumps(task)),
                    )
        return version_id

    def list_datasets(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM datasets ORDER BY created_at DESC").fetchall()
            return [self.row(r) for r in rows]  # type: ignore[list-item]

    def get_dataset(self, version_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.row(conn.execute("SELECT * FROM datasets WHERE dataset_version_id = %s", (version_id,)).fetchone())

    def list_tasks(self, version_id: str, split: str = "all") -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tasks WHERE dataset_version_id = %s ORDER BY id", (version_id,)).fetchall()
            tasks = []
            for row in rows:
                item = self.row(row)
                assert item is not None
                payload = json.loads(item.pop("payload_json"))
                if split != "all" and payload.get("split") != split:
                    continue
                payload.setdefault("category", item["category"])
                tasks.append(payload)
            return tasks

    def create_config(self, config_id: str, name: str, adapter: str, settings: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO agent_configs (id, name, adapter, settings_json, created_at) VALUES (%s, %s, %s, %s, %s)",
                (config_id, name, adapter, json.dumps(settings), utc_now()),
            )
        return self.get_config(config_id)  # type: ignore[return-value]

    def get_config(self, config_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.decode(self.row(conn.execute("SELECT * FROM agent_configs WHERE id = %s", (config_id,)).fetchone()), "settings_json")

    def list_configs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [self.decode(self.row(r), "settings_json") for r in conn.execute("SELECT * FROM agent_configs ORDER BY created_at").fetchall()]  # type: ignore[list-item]

    def create_experiment(self, dataset_version_id: str, config_ids: list[str], split: str, repetitions: int, idempotency_key: str | None = None) -> dict[str, Any]:
        manifest = {"dataset_version_id": dataset_version_id, "agent_config_ids": config_ids, "split": split, "repetitions": repetitions}
        if idempotency_key:
            with self.connect() as conn:
                existing = conn.execute("SELECT * FROM experiments WHERE idempotency_key = %s", (idempotency_key,)).fetchone()
                if existing:
                    existing_manifest = json.loads(existing["manifest_json"])
                    if existing_manifest != manifest:
                        raise ValueError("Idempotency-Key was reused with a different payload")
                    return self.decode(self.row(existing), "manifest_json")  # type: ignore[return-value]
        tasks = self.list_tasks(dataset_version_id, split)
        if not tasks:
            raise ValueError("dataset version has no tasks for the requested split")
        for config_id in config_ids:
            if not self.get_config(config_id):
                raise ValueError(f"unknown agent config: {config_id}")
        experiment_id = new_id("exp")
        planned = len(tasks) * len(config_ids) * repetitions
        with self.connect() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO experiments (id, dataset_version_id, split, repetitions, status, planned_runs, idempotency_key, manifest_json, created_at, completed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (experiment_id, dataset_version_id, split, repetitions, "queued", planned, idempotency_key, json.dumps(manifest), utc_now(), None),
                )
                for rep in range(1, repetitions + 1):
                    for config_id in config_ids:
                        for task in tasks:
                            run_id = new_id("run")
                            conn.execute(
                                "INSERT INTO runs (id, experiment_id, task_id, agent_config_id, repetition, execution_status, evaluation_status, attempt_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                (run_id, experiment_id, task["id"], config_id, rep, "queued", "pending", None, utc_now()),
                            )
                            conn.execute(
                                "INSERT INTO jobs (id, run_id, status, claimed_at, finished_at, error) VALUES (%s, %s, %s, %s, %s, %s)",
                                (new_id("job"), run_id, "queued", None, None, None),
                            )
        return self.get_experiment(experiment_id)  # type: ignore[return-value]

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = self.row(conn.execute("SELECT * FROM experiments WHERE id = %s", (experiment_id,)).fetchone())
            return self.decode(row, "manifest_json")

    def list_runs(self, experiment_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM runs WHERE experiment_id = %s ORDER BY created_at", (experiment_id,)).fetchall()
            return [self.row(r) for r in rows]  # type: ignore[list-item]

    def claim_job(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
                if not row:
                    return None
                now = utc_now()
                conn.execute("UPDATE jobs SET status = 'running', claimed_at = %s WHERE id = %s", (now, row["id"]))
                conn.execute("UPDATE runs SET execution_status = 'running' WHERE id = %s", (row["run_id"],))
                return self.row(conn.execute("SELECT * FROM jobs WHERE id = %s", (row["id"],)).fetchone())

    def get_run_context(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        with self.connect() as conn:
            run = self.row(conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone())
            if not run:
                raise KeyError(run_id)
            task_row = self.row(conn.execute("SELECT payload_json FROM tasks WHERE id = %s", (run["task_id"],)).fetchone())
            assert task_row is not None
            task = json.loads(task_row["payload_json"])
            config = self.decode(self.row(conn.execute("SELECT * FROM agent_configs WHERE id = %s", (run["agent_config_id"],)).fetchone()), "settings_json")
            experiment = self.row(conn.execute("SELECT * FROM experiments WHERE id = %s", (run["experiment_id"],)).fetchone())
            assert config is not None and experiment is not None
            return run, task, config, experiment

    def create_attempt(self, run_id: str, environment_id: str, fixture: dict[str, Any], hidden_fixture: dict[str, Any] | None = None) -> str:
        attempt_id = new_id("att")
        with self.connect() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO attempts (id, run_id, environment_id, execution_status, stop_reason, final_response, environment_json, hidden_fixture_json, created_at, finished_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (attempt_id, run_id, environment_id, "running", None, None, json.dumps(fixture), json.dumps(hidden_fixture or fixture), utc_now(), None),
                )
                conn.execute("UPDATE runs SET attempt_id = %s WHERE id = %s", (attempt_id, run_id))
        return attempt_id

    def add_event(self, attempt_id: str, event_type: str, status: str, payload: dict[str, Any], duration_ms: float | None = None) -> str:
        event_id = new_id("evt")
        with self.connect() as conn:
            with conn.transaction():
                conn.execute("SELECT id FROM attempts WHERE id = %s FOR UPDATE", (attempt_id,))
                seq_row = conn.execute("SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence FROM events WHERE attempt_id = %s", (attempt_id,)).fetchone()
                assert seq_row is not None
                conn.execute(
                    "INSERT INTO events (id, attempt_id, sequence_number, event_type, status, duration_ms, payload_json, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (event_id, attempt_id, seq_row["next_sequence"], event_type, status, duration_ms, json.dumps(payload), utc_now()),
                )
        return event_id

    def finish_attempt(self, attempt_id: str, status: str, stop_reason: str, final_response: str, environment: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE attempts SET execution_status = %s, stop_reason = %s, final_response = %s, environment_json = %s, finished_at = %s WHERE id = %s", (status, stop_reason, final_response, json.dumps(environment), utc_now(), attempt_id))

    def add_score(self, attempt_id: str, evaluator: str, status: str, score: float | None, reasons: list[str], evidence: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO scores (id, attempt_id, evaluator, status, score, reason_codes_json, evidence_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (attempt_id, evaluator) DO UPDATE SET
                  id = EXCLUDED.id,
                  status = EXCLUDED.status,
                  score = EXCLUDED.score,
                  reason_codes_json = EXCLUDED.reason_codes_json,
                  evidence_json = EXCLUDED.evidence_json,
                  created_at = EXCLUDED.created_at
                """,
                (new_id("score"), attempt_id, evaluator, status, score, json.dumps(reasons), json.dumps(evidence), utc_now()),
            )

    def finish_run(self, run_id: str, execution_status: str, evaluation_status: str) -> None:
        with self.connect() as conn:
            with conn.transaction():
                conn.execute("UPDATE runs SET execution_status = %s, evaluation_status = %s WHERE id = %s", (execution_status, evaluation_status, run_id))
                conn.execute("UPDATE jobs SET status = 'completed', finished_at = %s WHERE run_id = %s", (utc_now(), run_id))
                exp_row = conn.execute("SELECT experiment_id FROM runs WHERE id = %s", (run_id,)).fetchone()
                assert exp_row is not None
                exp_id = exp_row["experiment_id"]
                remaining_row = conn.execute("SELECT COUNT(*) AS remaining FROM runs WHERE experiment_id = %s AND execution_status IN ('queued', 'running')", (exp_id,)).fetchone()
                assert remaining_row is not None
                if remaining_row["remaining"] == 0:
                    conn.execute("UPDATE experiments SET status = 'completed', completed_at = %s WHERE id = %s", (utc_now(), exp_id))
                else:
                    conn.execute("UPDATE experiments SET status = 'running' WHERE id = %s", (exp_id,))

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = self.decode(self.row(conn.execute("SELECT * FROM attempts WHERE id = %s", (attempt_id,)).fetchone()), "environment_json", "hidden_fixture_json")
            if row:
                row["events"] = self.list_events(attempt_id)
                row["scores"] = self.list_scores(attempt_id)
            return row

    def list_events(self, attempt_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [self.decode(self.row(r), "payload_json") for r in conn.execute("SELECT * FROM events WHERE attempt_id = %s ORDER BY sequence_number", (attempt_id,)).fetchall()]  # type: ignore[list-item]

    def list_scores(self, attempt_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [self.decode(self.row(r), "reason_codes_json", "evidence_json") for r in conn.execute("SELECT * FROM scores WHERE attempt_id = %s ORDER BY evaluator", (attempt_id,)).fetchall()]  # type: ignore[list-item]

    def experiment_summary(self, experiment_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            total_row = conn.execute("SELECT COUNT(*) AS value FROM runs WHERE experiment_id = %s", (experiment_id,)).fetchone()
            completed_row = conn.execute("SELECT COUNT(*) AS value FROM runs WHERE experiment_id = %s AND execution_status = 'completed'", (experiment_id,)).fetchone()
            passed_row = conn.execute("SELECT COUNT(*) AS value FROM runs WHERE experiment_id = %s AND evaluation_status = 'pass'", (experiment_id,)).fetchone()
            failed_row = conn.execute("SELECT COUNT(*) AS value FROM runs WHERE experiment_id = %s AND evaluation_status = 'fail'", (experiment_id,)).fetchone()
            unsafe_row = conn.execute("SELECT COUNT(DISTINCT a.run_id) AS value FROM attempts a JOIN scores s ON s.attempt_id = a.id WHERE s.evaluator = 'policy' AND s.status = 'fail' AND a.run_id IN (SELECT id FROM runs WHERE experiment_id = %s)", (experiment_id,)).fetchone()
            assert total_row and completed_row and passed_row and failed_row and unsafe_row
            total = total_row["value"]
            completed = completed_row["value"]
            passed = passed_row["value"]
            failed = failed_row["value"]
            unsafe = unsafe_row["value"]
            return {"planned_runs": total, "completed_runs": completed, "scorable_runs": completed, "passed_runs": passed, "failed_runs": failed, "verified_success_rate": (passed / completed if completed else None), "evaluation_coverage": (completed / total if total else 0), "unsafe_attempt_runs": unsafe}

    def export_rows(self, experiment_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT r.*, t.category, t.payload_json, a.final_response, a.stop_reason FROM runs r JOIN tasks t ON t.id = r.task_id LEFT JOIN attempts a ON a.id = r.attempt_id WHERE r.experiment_id = %s ORDER BY r.created_at", (experiment_id,)).fetchall()
            return [self.row(r) for r in rows]  # type: ignore[list-item]
