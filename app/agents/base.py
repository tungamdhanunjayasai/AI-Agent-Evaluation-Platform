from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AgentResult:
    final_response: str
    stop_reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentAdapter(Protocol):
    async def run(self, task_input: dict[str, Any], tools: Any, events: Any, limits: Any) -> AgentResult:
        ...

