from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agents.base import AgentResult
from app.agents.providers import (
    Provider,
    ProviderError,
    ProviderResponse,
    ToolCall,
    provider_for,
)


class LLMAdapter:
    def __init__(self, provider: Provider, model: str, *, temperature: float = 0.0, max_steps: int = 8, max_retries: int = 2, timeout_seconds: float = 60.0):
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_steps = max_steps
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> LLMAdapter:
        provider_name = settings.get("provider", "gemini")
        limits = settings.get("limits", {})
        return cls(
            provider_for(provider_name),
            settings.get("model", "gemini-2.5-flash"),
            temperature=float(settings.get("temperature", 0)),
            max_steps=int(limits.get("max_steps", settings.get("max_steps", 8))),
            max_retries=int(limits.get("max_retries", 2)),
            timeout_seconds=float(limits.get("timeout_seconds", 60)),
        )

    async def run(self, task_input: dict[str, Any], tools: Any, events: Any, limits: Any) -> AgentResult:
        max_steps = self.max_steps
        if isinstance(limits, dict):
            max_steps = int(limits.get("max_steps", max_steps))
        tool_schemas = self._tool_schemas(tools)
        customer_id = task_input.get("authenticated_customer_id", "")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "You are a customer-support agent. Use the available tools to verify facts and perform only authorized actions. You can see only the customer message and authenticated customer context; never ask for or infer hidden fixture or evaluation data."},
            {"role": "user", "content": json.dumps({"customer_message": task_input.get("user_message", ""), "authenticated_customer_id": customer_id}, ensure_ascii=False)},
        ]
        usage: dict[str, Any] = {}
        last_text = ""
        for step in range(max_steps):
            response = await self._complete(messages, tool_schemas, events, step)
            if response.error:
                events.record("agent_finished", "error", {"stop_reason": "model_error", "model": self.model, "error": response.error, "usage": usage})
                return AgentResult("", "model_error", {"model": self.model, "usage": usage, "error": response.error})
            last_text = response.text or last_text
            usage.update(response.usage)
            if response.tool_calls:
                messages.append({"role": "assistant", "content": response.text, "tool_calls": [self._call_dict(call) for call in response.tool_calls]})
                for call in response.tool_calls:
                    result = tools.execute(call.name, call.arguments)
                    messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": json.dumps(result, ensure_ascii=False)})
                continue
            events.record("agent_finished", "ok", {"stop_reason": "model_final", "model": self.model, "usage": usage})
            return AgentResult(last_text, "model_final", {"model": self.model, "usage": usage})
        events.record("agent_finished", "ok", {"stop_reason": "max_steps", "model": self.model, "usage": usage})
        return AgentResult(last_text, "max_steps", {"model": self.model, "usage": usage})

    async def _complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], events: Any, step: int) -> ProviderResponse:
        events.record("model_request", "ok", {"model": self.model, "step": step})
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw_response = await asyncio.wait_for(self.provider.complete(messages=messages, tools=tools, model=self.model, temperature=self.temperature), timeout=self.timeout_seconds)
                response = _coerce_response(raw_response)
                events.record("model_response", "ok", {"model": response.model or self.model, "step": step, "usage": response.usage})
                return response
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.05 * (attempt + 1))
        error = str(last_error or ProviderError("model provider failed"))
        events.record("model_response", "error", {"model": self.model, "step": step, "error": error})
        return ProviderResponse(text="", usage={}, model=self.model, error=error)

    @staticmethod
    def _tool_schemas(tools: Any) -> list[dict[str, Any]]:
        schemas = tools.visible_schemas()
        return [schema if isinstance(schema, dict) else {"name": schema, "description": "Support tool", "parameters": {"type": "object", "properties": {}}} for schema in schemas]

    @staticmethod
    def _call_dict(call: ToolCall) -> dict[str, Any]:
        return {"id": call.id, "name": call.name, "arguments": call.arguments}


def _coerce_response(response: ProviderResponse | dict[str, Any]) -> ProviderResponse:
    if isinstance(response, ProviderResponse):
        return response
    calls = [ToolCall(item.get("id", f"call_{index}"), item["name"], item.get("arguments", {})) for index, item in enumerate(response.get("tool_calls", []))]
    return ProviderResponse(text=response.get("text", ""), tool_calls=calls, usage=response.get("usage", {}), model=response.get("model"), error=response.get("error"))
