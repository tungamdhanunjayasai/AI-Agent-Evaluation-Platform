from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.main as main


def test_dashboard_escapes_dataset_and_config_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main,
        "store",
        SimpleNamespace(
            list_datasets=lambda: [{"name": "<script>alert(1)</script>"}],
            list_configs=lambda: [{"name": "<script>alert(1)</script>"}],
        ),
    )

    rendered = main.dashboard()

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered


def test_ready_returns_service_unavailable_when_database_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenStore:
        def connect(self):
            raise RuntimeError("connection failed")

    monkeypatch.setattr(main, "store", BrokenStore())

    with pytest.raises(HTTPException) as raised:
        main.ready()

    assert raised.value.status_code == 503
