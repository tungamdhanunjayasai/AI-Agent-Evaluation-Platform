from __future__ import annotations

from typing import Any

from app.store import Store


class TraceRecorder:
    def __init__(self, store: Store, attempt_id: str):
        self.store = store
        self.attempt_id = attempt_id

    def record(self, event_type: str, status: str = "ok", payload: dict[str, Any] | None = None, duration_ms: float | None = None) -> None:
        self.store.add_event(self.attempt_id, event_type, status, payload or {}, duration_ms)

