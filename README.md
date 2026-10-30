# Agent Evaluation Platform

[![CI](https://github.com/OWNER/REPOSITORY/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPOSITORY/actions/workflows/ci.yml)

This repository is a runnable first version of the project described in the accompanying guide. It evaluates scripted, tool-using customer-support agents against isolated fixtures and records the evidence needed to explain failures.

The platform uses scripted and optional LLM adapters with PostgreSQL persistence. Runtime storage is PostgreSQL-only; SQLite files are not used by the application.

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
pip install -e ".[llm]"  # optional Gemini/Anthropic adapter dependencies
Copy `.env.example` to `.env`, replace `YOUR_DB_PASSWORD` with your Supabase database password locally, and keep that file private.
python -m app.seed
python -m app.execution.worker --loop
# Point database-dependent tests at a dedicated test Supabase database (not production).
# Or set TEST_DATABASE_URL in the shell before pytest.
pytest
uvicorn app.main:app --reload
```

CI runs `python -m ruff check app tests scripts` followed by `python -m pytest` against PostgreSQL 16.

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

The worker is intentionally separate from the API. `python -m app.execution.worker --drain` processes the current queue and `python -m app.execution.worker --loop --poll-interval 2` continuously polls with graceful shutdown. Jobs use leases and bounded retries.

To run an LLM configuration after seeding and installing the optional extra:

```powershell
python -m app.cli run-llm --provider gemini --model gemini-2.5-flash --repetitions 1
```

The LLM adapter sends only the customer message and authenticated customer context to the provider. It derives tool schemas from the environment gateway, executes calls through that gateway, records model and token-usage metadata, and stops on a final answer or its configured step limit. API keys are read only from `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`.

## Supabase PostgreSQL configuration

1. Create or select a Supabase project and copy its PostgreSQL connection string from **Dashboard -> Connect**. Use the direct connection or the session pooler; keep the password out of source control.
2. Copy `.env.example` to `.env` and replace `YOUR_DB_PASSWORD` locally. The application loads `.env` automatically; alternatively, export `DATABASE_URL` in the shell. URL-encode special characters in the database password.
3. Install dependencies with `pip install -e ".[test]"`; add `.[llm]` for provider SDKs. The project uses `psycopg` and `psycopg_pool`.
4. Run `python -m app.seed`. `Store.init_db()` applies the ordered files in `app/migrations/` once and records them in `schema_migrations`.

Useful environment variables are `TEST_DATABASE_URL` (a separate test database), `DB_POOL_MIN_SIZE`, `DB_POOL_MAX_SIZE`, `JOB_LEASE_SECONDS`, `MAX_ATTEMPTS`, `GEMINI_API_KEY`, and `ANTHROPIC_API_KEY`. The example file contains placeholders only.

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

## Results

| Model | Tasks passed | Failure reasons |
|---|---:|---|
| TODO — no model run recorded | TODO | TODO |

## Design decisions

- Execution status and evaluation status are separate, so infrastructure failures do not look like agent failures.
- Evaluators read hidden fixture state that the agent never receives.
- Dataset versions are immutable once created; changing task content requires a new version.
- Jobs use leases and bounded retries so a crashed worker can be recovered without losing the run.

