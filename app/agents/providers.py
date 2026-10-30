from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ProviderResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    error: str | None = None


class Provider(Protocol):
    async def complete(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str, temperature: float) -> ProviderResponse:
        ...


class ProviderError(RuntimeError):
    pass


class GeminiProvider:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ProviderError("GEMINI_API_KEY is not configured")
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderError("install the llm extra to use Gemini") from exc
        self.client = genai.Client(api_key=key)

    async def complete(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str, temperature: float) -> ProviderResponse:
        try:
            response = await self.client.aio.models.generate_content(
                model=model,
                contents=messages,
                config={"temperature": temperature, "tools": [{"function_declarations": tools}]},
            )
        except Exception as exc:
            raise ProviderError(str(exc)) from exc
        text = ""
        calls: list[ToolCall] = []
        for candidate in getattr(response, "candidates", []) or []:
            for part in getattr(getattr(candidate, "content", None), "parts", []) or []:
                if getattr(part, "text", None):
                    text += part.text
                function_call = getattr(part, "function_call", None)
                if function_call:
                    args = dict(getattr(function_call, "args", {}) or {})
                    calls.append(ToolCall(getattr(function_call, "id", f"call_{len(calls)}"), function_call.name, args))
        usage = _usage_dict(getattr(response, "usage_metadata", None))
        return ProviderResponse(text=text, tool_calls=calls, usage=usage, model=model)


class AnthropicProvider:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not configured")
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise ProviderError("install the llm extra to use Anthropic") from exc
        self.client = AsyncAnthropic(api_key=key)

    async def complete(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str, temperature: float) -> ProviderResponse:
        system = "\n".join(message["content"] for message in messages if message["role"] == "system")
        user_messages = [message for message in messages if message["role"] != "system"]
        anthropic_tools = [{"name": tool["name"], "description": tool.get("description", ""), "input_schema": tool.get("parameters", {})} for tool in tools]
        try:
            response = await self.client.messages.create(model=model, max_tokens=2048, temperature=temperature, system=system, messages=user_messages, tools=anthropic_tools)
        except Exception as exc:
            raise ProviderError(str(exc)) from exc
        text = ""
        calls: list[ToolCall] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text += getattr(block, "text", "")
            elif getattr(block, "type", None) == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input or {})))
        return ProviderResponse(text=text, tool_calls=calls, usage=_usage_dict(getattr(response, "usage", None)), model=model)


def provider_for(name: str) -> Provider:
    if name == "gemini":
        return GeminiProvider()
    if name == "anthropic":
        return AnthropicProvider()
    raise ProviderError(f"unknown LLM provider: {name}")


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return dict(usage)
    return {key: getattr(usage, key) for key in ("prompt_token_count", "candidates_token_count", "input_tokens", "output_tokens") if getattr(usage, key, None) is not None}
