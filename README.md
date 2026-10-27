# Agent Evaluation Platform

This repository is a runnable first version of the project described in the accompanying guide. It evaluates scripted, tool-using customer-support agents against isolated fixtures and records the evidence needed to explain failures.

The platform uses a scripted adapter and Supabase-hosted PostgreSQL. The existing API, worker, evaluator, and dashboard behavior are unchanged; only the persistence driver and database configuration use PostgreSQL now.

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
Copy `.env.example` to `.env`, replace `YOUR_DB_PASSWORD` with your Supabase database password locally, and keep that file private.
python -m app.seed
python -m app.execution.worker --drain
# Point database-dependent tests at a dedicated test Supabase database (not production).
# Or set TEST_DATABASE_URL in the shell before pytest.
pytest
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/` or `http://127.0.0.1:8000/docs`.

The smoke seed creates five support tasks and three configurations: a correct agent, a convincing false-success agent, and an agent that attempts to use another customer's order. Create an experiment through the API or use:

```powershell
python -m app.cli smoke
```

## Core API

- `POST /datasets/import` and `GET /datasets`
- `POST /agent-configs` and `GET /agent-configs`
- `POST /experiments`
- `GET /experiments/{id}`
- `GET /experiments/{id}/runs`
- `GET /runs/{id}`
- `GET /attempts/{id}/events`
- `GET /experiments/{id}/export?format=csv`
- `GET /health/live` and `/health/ready`

The worker is intentionally separate from the API. `python -m app.execution.worker --drain` claims queued jobs, executes attempts, scores them, and updates the experiment summary.

## Supabase PostgreSQL configuration

1. Create or select a Supabase project and copy its PostgreSQL connection string from **Dashboard -> Connect**. Use the direct connection or the session pooler; keep the password out of source control.
2. Copy `.env.example` to `.env` and replace `YOUR_DB_PASSWORD` locally. The application loads `.env` automatically; alternatively, export `DATABASE_URL` in the shell. URL-encode special characters in the database password.
3. Install dependencies with `pip install -e .`. The project uses the PostgreSQL `psycopg` driver.
4. Run `python -m app.seed`. `Store.init_db()` applies `app/migrations/001_initial.sql` with `CREATE TABLE IF NOT EXISTS`, so this creates the existing tables and constraints without a second migration system.

Supabase PostgreSQL is the sole application runtime database. The application requires `DATABASE_URL`, rejects SQLite paths, and never silently falls back to a local SQLite database. The original `.db` files and the migration helper are offline backup/import tooling only; they are not used for ongoing application storage and are not modified or deleted.

### Migrating existing SQLite data

The repository includes a safe, append-only migration helper. It opens the source SQLite file read-only, initializes the PostgreSQL schema, and inserts rows in foreign-key order. Existing target rows are left unchanged when a primary-key or unique conflict is encountered.

```powershell
Copy `.env.example` to `.env` and set `DATABASE_URL` before running the migration command.
python scripts/migrate_sqlite_to_postgres.py .\agent_eval.db
```

Run the command against a copy or backup when possible, verify the printed row counts and application behavior, and keep the original SQLite file. The available source files are `agent_eval.db`, `api-fixed.db`, and `smoke-fixed.db`; choose only the one whose data you intend to migrate. Do not run multiple overlapping source migrations into the same target unless you have reviewed duplicate IDs and conflicts.

### Docker Compose

Set `DATABASE_URL` in the shell or a local Compose environment file, then run:

```powershell
Copy `.env.example` to `.env` and set `DATABASE_URL` before starting Compose.
docker compose up --build
```

Both the API and worker use the same Supabase PostgreSQL URL. No local database volume is required.

## Evaluation model

Each attempt receives a fresh support environment. The agent sees only the customer request and authenticated customer context. Evaluators read the hidden fixture and final environment state independently. Scores cover outcome, policy, trajectory, and response accuracy; execution status is kept separate from evaluation status.

The deliberately broken `false_success` configuration demonstrates the central failure mode: a helpful final answer does not count as success when the environment shows that no refund occurred.

