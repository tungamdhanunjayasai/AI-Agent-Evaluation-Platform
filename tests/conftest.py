from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from dotenv import load_dotenv

from app.store import Store


load_dotenv(Path.cwd() / ".env")


def _database_identity(url: str) -> tuple[str, str | None, int | None, str]:
    parsed = urlparse(url)
    return parsed.scheme, parsed.hostname, parsed.port, parsed.path


def _test_database_url() -> str | None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        return None
    production_url = os.getenv("DATABASE_URL")
    if production_url and _database_identity(database_url) == _database_identity(production_url):
        pytest.fail("TEST_DATABASE_URL must point to a separate PostgreSQL database, not the application database")
    return database_url


@pytest.fixture(autouse=True)
def clean_test_database():
    database_url = _test_database_url()
    if database_url:
        store = Store(database_url)
        with store.connect() as conn:
            with conn.transaction():
                conn.execute("TRUNCATE TABLE jobs, scores, events, attempts, runs, experiments, tasks, agent_configs, datasets CASCADE")
    yield
