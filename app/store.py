from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"
SCHEMA = (MIGRATIONS_PATH / "001_initial.sql").read_text(encoding="utf-8")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    """PostgreSQL-backed persistence for the platform.

    A filesystem path is intentionally not treated as a request to fall back
    to SQLite.
    """

    def __init__(self, database_url: str | None = None, *, lease_seconds: int | None = None, max_attempts: int | None = None):
        if isinstance(database_url, Path):
            raise ValueError("SQLite filesystem paths are no longer supported; pass a PostgreSQL DATABASE_URL")
        self.database_url = database_url
        self.lease_seconds = lease_seconds or int(os.getenv("JOB_LEASE_SECONDS", "300"))
        self.max_attempts = max_attempts or int(os.getenv("MAX_ATTEMPTS", "3"))
        self.pool_min_size = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
        self.pool_max_size = int(os.getenv("DB_POOL_MAX_SIZE", "10"))
        self._pool: ConnectionPool[Any] | None = None
        self._pool_lock = threading.Lock()
        if self.database_url and urlparse(self.database_url).scheme.lower() not in {"postgres", "postgresql"}:
            raise ValueError("DATABASE_URL must use the postgresql:// or postgres:// scheme")

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection[Any]]:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required; configure a PostgreSQL connection URL")
        with self._pool_connection() as conn:
            yield conn

    @contextmanager
    def _pool_connection(self) -> Iterator[psycopg.Connection[Any]]:
        pool = self._get_pool()
        with pool.connection() as conn:
            yield conn

    def _get_pool(self) -> ConnectionPool[Any]:
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    self._pool = ConnectionPool(
                        conninfo=self.database_url,
                        min_size=self.pool_min_size,
                        max_size=self.pool_max_size,
                        kwargs={"autocommit": True, "row_factory": dict_row},
                        open=False,
                    )
                    self._pool.open(wait=True)
        assert self._pool is not None
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def init_db(self) -> None:
        with self.connect() as conn:
            with conn.transaction():
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                for path in sorted(MIGRATIONS_PATH.glob("*.sql")):
                    version = path.name
                    applied = conn.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,)).fetchone()
                    if applied:
                        continue
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)", (version, utc_now()))

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
        incoming = {task["id"]: json.loads(json.dumps(task, sort_keys=True)) for task in tasks}
        if len(incoming) != len(tasks):
            raise ValueError("task ids must be unique within a dataset version")
        with self.connect() as conn:
            with conn.transaction():
                existing = conn.execute("SELECT * FROM datasets WHERE dataset_version_id = %s FOR UPDATE", (version_id,)).fetchone()
                if existing:
                    rows = conn.execute("SELECT id, payload_json FROM tasks WHERE dataset_version_id = %s", (version_id,)).fetchall()
                    current = {row["id"]: json.loads(row["payload_json"]) for row in rows}
                    if current != incoming:
                        raise ValueError("dataset version is immutable; bump the version")
                    conn.execute("UPDATE datasets SET name = %s WHERE dataset_version_id = %s", (name, version_id))
                    return version_id
                conn.execute(
                    "INSERT INTO datasets (dataset_version_id, dataset_id, version, name, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (version_id, dataset_id, version, name, utc_now()),
                )
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
                                "INSERT INTO runs (id, experiment_id, dataset_version_id, task_id, agent_config_id, repetition, execution_status, evaluation_status, attempt_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                (run_id, experiment_id, dataset_version_id, task["id"], config_id, rep, "queued", "pending", None, utc_now()),
                            )
                            conn.execute(
                                "INSERT INTO jobs (id, run_id, status, attempts_count, lease_expires_at, claimed_at, finished_at, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                                (new_id("job"), run_id, "queued", 0, None, None, None, None),
                            )
        return self.get_experiment(experiment_id)  # type: ignore[return-value]

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = self.row(conn.execute("SELECT * FROM experiments WHERE id = %s", (experiment_id,)).fetchone())
            return self.decode(row, "manifest_json")

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return self.row(conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone())

    def list_runs(self, experiment_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM runs WHERE experiment_id = %s ORDER BY created_at", (experiment_id,)).fetchall()
            return [self.row(r) for r in rows]  # type: ignore[list-item]

    def claim_job(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            with conn.transaction():
                now = datetime.now(UTC)
                now_text = now.isoformat()
                stale = conn.execute(
                    "UPDATE jobs SET status = 'queued', lease_expires_at = NULL, claimed_at = NULL WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= %s RETURNING run_id",
                    (now_text,),
                ).fetchall()
                for item in stale:
                    conn.execute("UPDATE runs SET execution_status = 'queued' WHERE id = %s AND execution_status = 'running'", (item["run_id"],))
                row = conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
                if not row:
                    return None
                lease_expires_at = (now + timedelta(seconds=self.lease_seconds)).isoformat()
                conn.execute("UPDATE jobs SET status = 'running', attempts_count = attempts_count + 1, claimed_at = %s, lease_expires_at = %s WHERE id = %s", (now_text, lease_expires_at, row["id"]))
                conn.execute("UPDATE runs SET execution_status = 'running' WHERE id = %s", (row["run_id"],))
                return self.row(conn.execute("SELECT * FROM jobs WHERE id = %s", (row["id"],)).fetchone())

    def get_run_context(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        with self.connect() as conn:
            run = self.row(conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone())
            if not run:
                raise KeyError(run_id)
            task_row = self.row(conn.execute("SELECT payload_json FROM tasks WHERE dataset_version_id = %s AND id = %s", (run["dataset_version_id"], run["task_id"])).fetchone())
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

    def finish_run(self, run_id: str, execution_status: str, evaluation_status: str, *, error: str | None = None) -> None:
        with self.connect() as conn:
            with conn.transaction():
                job = conn.execute("SELECT * FROM jobs WHERE run_id = %s FOR UPDATE", (run_id,)).fetchone()
                if not job:
                    raise KeyError(run_id)
                retry = execution_status == "infrastructure_error" and job["attempts_count"] < self.max_attempts
                run_status = "queued" if retry else execution_status
                job_status = "queued" if retry else ("failed" if execution_status == "infrastructure_error" else "completed")
                conn.execute("UPDATE runs SET execution_status = %s, evaluation_status = %s WHERE id = %s", (run_status, evaluation_status, run_id))
                conn.execute("UPDATE jobs SET status = %s, finished_at = %s, lease_expires_at = NULL, error = %s WHERE run_id = %s", (job_status, None if retry else utc_now(), error, run_id))
                exp_row = conn.execute("SELECT experiment_id FROM runs WHERE id = %s", (run_id,)).fetchone()
                assert exp_row is not None
                exp_id = exp_row["experiment_id"]
                remaining_row = conn.execute("SELECT COUNT(*) AS remaining FROM runs WHERE experiment_id = %s AND execution_status IN ('queued', 'running')", (exp_id,)).fetchone()
                assert remaining_row is not None
                experiment = conn.execute("SELECT status FROM experiments WHERE id = %s", (exp_id,)).fetchone()
                if experiment and experiment["status"] != "cancelled":
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
            rows = conn.execute("SELECT r.*, t.category, t.payload_json, a.final_response, a.stop_reason FROM runs r JOIN tasks t ON t.dataset_version_id = r.dataset_version_id AND t.id = r.task_id LEFT JOIN attempts a ON a.id = r.attempt_id WHERE r.experiment_id = %s ORDER BY r.created_at", (experiment_id,)).fetchall()
            return [self.row(r) for r in rows]  # type: ignore[list-item]
