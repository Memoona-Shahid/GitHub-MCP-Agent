"""OpenAI-compatible tool-calling LLM adapter.

Talks to any Chat Completions API that implements function tools
(OpenAI, Azure OpenAI, Groq, OpenRouter, Ollama, …). It does not call GitHub.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)

from github_mcp_agent.config import Settings, get_settings
from github_mcp_agent.exceptions import LLMError
from github_mcp_agent.logging_setup import get_logger
from github_mcp_agent.mcp_client import MCPToolSpec

log = get_logger("llm_client")

_OPENAI_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


@dataclass(frozen=True)
class ToolCallRequest:
    """One function call the model wants the agent to execute via MCP."""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = "{}"


@dataclass(frozen=True)
class LLMResponse:
    """Normalized Chat Completions result for the agent loop."""

    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    assistant_message: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def mcp_tools_to_openai(tools: Sequence[MCPToolSpec]) -> list[dict[str, Any]]:
    """Convert GitHub MCP tool specs into OpenAI `tools` entries."""
    converted: list[dict[str, Any]] = []
    skipped: list[str] = []
    for spec in tools:
        if not _OPENAI_TOOL_NAME.match(spec.name):
            skipped.append(spec.name)
            continue
        parameters = spec.input_schema or {"type": "object", "properties": {}}
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description or spec.name,
                    "parameters": parameters,
                },
            }
        )
    if skipped:
        log.warning("Skipped %d MCP tool(s) with names the LLM cannot call: %s", len(skipped), skipped)
    log.info("Converted %d MCP tool(s) to OpenAI function tools", len(converted))
    return converted


def parse_tool_arguments(raw: str | None) -> dict[str, Any]:
    """Parse a model's tool-argument JSON. Empty input becomes {}."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model returned invalid JSON for tool arguments: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LLMError("Tool arguments must be a JSON object")
    return value


def tool_result_message(tool_call_id: str, content: str) -> dict[str, Any]:
    """Chat message that feeds an MCP tool result back to the model."""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content or "(empty)"}


class LLMClient:
    """Thin async wrapper around an OpenAI-compatible Chat Completions API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._settings.require_llm_key()
        self._model = self._settings.llm_model
        self._client = AsyncOpenAI(
            api_key=self._settings.openai_api_key,
            base_url=self._settings.openai_base_url.rstrip("/") or None,
            timeout=60.0,
            max_retries=2,
        )
        log.info(
            "LLM client ready (model=%s, base_url=%s, key=%s)",
            self._model,
            self._settings.openai_base_url,
            self._settings.masked_openai_key(),
        )

    @property
    def model(self) -> str:
        return self._model

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.close()

    async def ping(self) -> str:
        """Tiny text round-trip used by `python -m github_mcp_agent --llm-ping`."""
        response = await self.complete(
            messages=[
                {"role": "system", "content": "Reply with exactly the word pong and nothing else."},
                {"role": "user", "content": "ping"},
            ]
        )
        text = (response.content or "").strip()
        if not text:
            raise LLMError("LLM ping succeeded but the model returned empty content")
        return text

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: Sequence[MCPToolSpec] | Sequence[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict[str, Any] | None = "auto",
    ) -> LLMResponse:
        """Run one Chat Completions turn. `tools` may be MCP specs or OpenAI dicts."""
        openai_tools = _coerce_openai_tools(tools)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice

        log.info(
            "LLM complete: model=%s messages=%d tools=%d",
            self._model,
            len(messages),
            len(openai_tools),
        )
        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except AuthenticationError as exc:
            raise LLMError(
                "LLM authentication failed. Check OPENAI_API_KEY and OPENAI_BASE_URL."
            ) from exc
        except RateLimitError as exc:
            raise LLMError("LLM rate limit hit. Wait and retry.") from exc
        except APIConnectionError as exc:
            raise LLMError(f"Could not reach the LLM at {self._settings.openai_base_url}: {exc}") from exc
        except APIStatusError as exc:
            raise LLMError(f"LLM API returned HTTP {exc.status_code}: {exc.message}") from exc
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        if not completion.choices:
            raise LLMError("LLM returned no choices")

        choice = completion.choices[0]
        message = choice.message
        tool_calls = _parse_tool_calls(message)
        assistant_message = _assistant_message(message)
        finish = choice.finish_reason or "stop"
        log.info(
            "LLM response: finish_reason=%s tool_calls=%d content_chars=%d",
            finish,
            len(tool_calls),
            len(message.content or ""),
        )
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=finish,
            assistant_message=assistant_message,
        )


def _coerce_openai_tools(
    tools: Sequence[MCPToolSpec] | Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not tools:
        return []
    first = tools[0]
    if isinstance(first, MCPToolSpec):
        return mcp_tools_to_openai(tools)  # type: ignore[arg-type]
    return [tool for tool in tools if isinstance(tool, dict)]  # type: ignore[misc]


def _parse_tool_calls(message: Any) -> list[ToolCallRequest]:
    calls = getattr(message, "tool_calls", None) or []
    parsed: list[ToolCallRequest] = []
    for call in calls:
        function = getattr(call, "function", None)
        name = getattr(function, "name", None) if function is not None else None
        if not name:
            continue
        raw = getattr(function, "arguments", None) or "{}"
        parsed.append(
            ToolCallRequest(
                id=getattr(call, "id", "") or "",
                name=name,
                arguments=parse_tool_arguments(raw),
                raw_arguments=raw if isinstance(raw, str) else json.dumps(raw),
            )
        )
    return parsed


def _assistant_message(message: Any) -> dict[str, Any]:
    """Stable message dict to append to the next `complete()` call."""
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": message.content,
    }
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments or "{}",
                },
            }
            for call in calls
        ]
    return payload
