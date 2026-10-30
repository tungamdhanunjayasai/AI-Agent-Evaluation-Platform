"""Copy an existing SQLite database into PostgreSQL without modifying the source.

The target is append-only: rows that already exist in PostgreSQL are left as-is.
Run with DATABASE_URL set to the Supabase connection URL.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

import psycopg

from app.store import Store

TABLES = {
    "datasets": ["dataset_version_id", "dataset_id", "version", "name", "created_at"],
    "tasks": ["id", "dataset_version_id", "category", "payload_json"],
    "agent_configs": ["id", "name", "adapter", "settings_json", "created_at"],
    "experiments": ["id", "dataset_version_id", "split", "repetitions", "status", "planned_runs", "idempotency_key", "manifest_json", "created_at", "completed_at"],
    "runs": ["id", "experiment_id", "task_id", "agent_config_id", "repetition", "execution_status", "evaluation_status", "attempt_id", "created_at"],
    "attempts": ["id", "run_id", "environment_id", "execution_status", "stop_reason", "final_response", "environment_json", "hidden_fixture_json", "created_at", "finished_at"],
    "events": ["id", "attempt_id", "sequence_number", "event_type", "status", "duration_ms", "payload_json", "created_at"],
    "scores": ["id", "attempt_id", "evaluator", "status", "score", "reason_codes_json", "evidence_json", "created_at"],
    "jobs": ["id", "run_id", "status", "claimed_at", "finished_at", "error"],
}


def copy_table(source: sqlite3.Connection, target: psycopg.Connection, table: str, columns: list[str]) -> int:
    quoted_columns = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    rows = source.execute(f"SELECT {quoted_columns} FROM {table}").fetchall()
    inserted = 0
    with target.transaction():
        for row in rows:
            cursor = target.execute(sql, tuple(row))
            inserted += cursor.rowcount
    return inserted


def copy_runs(source: sqlite3.Connection, target: psycopg.Connection) -> int:
    rows = source.execute("SELECT id, experiment_id, task_id, agent_config_id, repetition, execution_status, evaluation_status, attempt_id, created_at FROM runs").fetchall()
    inserted = 0
    with target.transaction():
        for row in rows:
            experiment = target.execute("SELECT dataset_version_id FROM experiments WHERE id = %s", (row["experiment_id"],)).fetchone()
            if not experiment:
                continue
            cursor = target.execute(
                "INSERT INTO runs (id, experiment_id, dataset_version_id, task_id, agent_config_id, repetition, execution_status, evaluation_status, attempt_id, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (row["id"], row["experiment_id"], experiment["dataset_version_id"], row["task_id"], row["agent_config_id"], row["repetition"], row["execution_status"], row["evaluation_status"], row["attempt_id"], row["created_at"]),
            )
            inserted += cursor.rowcount
    return inserted


def copy_jobs(source: sqlite3.Connection, target: psycopg.Connection) -> int:
    rows = source.execute("SELECT id, run_id, status, claimed_at, finished_at, error FROM jobs").fetchall()
    inserted = 0
    with target.transaction():
        for row in rows:
            cursor = target.execute(
                "INSERT INTO jobs (id, run_id, status, attempts_count, lease_expires_at, claimed_at, finished_at, error) VALUES (%s, %s, %s, 0, NULL, %s, %s, %s) ON CONFLICT DO NOTHING",
                (row["id"], row["run_id"], row["status"], row["claimed_at"], row["finished_at"], row["error"]),
            )
            inserted += cursor.rowcount
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path, help="Existing SQLite file to read")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="PostgreSQL URL; defaults to DATABASE_URL")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("Set DATABASE_URL or pass --database-url")
    if not args.sqlite_path.is_file():
        raise SystemExit(f"SQLite source does not exist: {args.sqlite_path}")

    # SQLite opens in read-only mode. This script never deletes or updates the source file.
    source_uri = f"file:{args.sqlite_path.resolve().as_posix()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    source.row_factory = sqlite3.Row
    try:
        store = Store(args.database_url)
        store.init_db()
        with store.connect() as target:
            for table, columns in TABLES.items():
                if table == "runs":
                    inserted = copy_runs(source, target)
                elif table == "jobs":
                    inserted = copy_jobs(source, target)
                else:
                    inserted = copy_table(source, target, table, columns)
                print(f"{table}: inserted {inserted} row(s)")
    finally:
        source.close()


if __name__ == "__main__":
    main()
