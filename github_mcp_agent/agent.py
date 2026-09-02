"""Natural-language agent loop: LLM tool calls executed only through GitHub MCP."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from github_mcp_agent.config import Settings, get_settings
from github_mcp_agent.exceptions import AgentError, MCPToolError
from github_mcp_agent.llm_client import LLMClient, tool_result_message
from github_mcp_agent.logging_setup import get_logger
from github_mcp_agent.mcp_client import GitHubMCPClient, MCPToolSpec

log = get_logger("agent")

_MAX_TOOL_RESULT_CHARS = 24_000

_SYSTEM_PROMPT = """You are a GitHub repository explorer.

You answer questions in plain English about GitHub repositories: issues, pull
requests, commits, repository metadata, directory trees, and file contents.

You MUST use the provided GitHub MCP tools to fetch facts. Never invent issue
numbers, PR titles, commit SHAs, file contents, star counts, or other GitHub data.
If a tool fails, explain the error and try a different tool when one fits.

When the user names a repository as owner/name or a github.com URL, use that.
If they do not name one, use this default repository: {default_repo}

Pick the smallest set of tool calls that answers the question. After tools
return, write a clear, concise answer. Quote paths, issue numbers, and SHAs
when they help. If the question is ambiguous, ask a short clarifying question
instead of guessing.
"""


@dataclass(frozen=True)
class ToolTrace:
    """One MCP tool invocation made while answering a question."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    result_preview: str


@dataclass(frozen=True)
class AgentAnswer:
    """Final reply plus the MCP tools that produced it."""

    text: str
    tool_traces: list[ToolTrace] = field(default_factory=list)
    llm_turns: int = 0


class GitHubAgent:
    """Owns one MCP session and one LLM client for a conversation."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._stack: AsyncExitStack | None = None
        self._mcp: GitHubMCPClient | None = None
        self._llm: LLMClient | None = None
        self._tools: list[MCPToolSpec] = []
        self._messages: list[dict[str, Any]] = []

    @property
    def tools(self) -> list[MCPToolSpec]:
        return list(self._tools)

    @property
    def default_repo(self) -> str:
        return self._settings.default_repo

    async def __aenter__(self) -> GitHubAgent:
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            mcp = await stack.enter_async_context(GitHubMCPClient(self._settings))
            llm = await stack.enter_async_context(LLMClient(self._settings))
            tools = await mcp.list_tools()
        except Exception:
            await stack.aclose()
            raise
        if not tools:
            await stack.aclose()
            raise AgentError("GitHub MCP Server advertised no tools. Check GITHUB_TOOLSETS.")
        self._stack = stack
        self._mcp = mcp
        self._llm = llm
        self._tools = tools
        log.info(
            "Agent ready: %d MCP tool(s), model=%s, default_repo=%s, max_turns=%d",
            len(tools),
            llm.model,
            self._settings.default_repo or "(none)",
            self._settings.agent_max_turns,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._mcp = None
        self._llm = None
        self._tools = []
        log.info("Agent shut down")

    def reset(self) -> None:
        """Drop conversation history. MCP connection stays open."""
        self._messages.clear()
        log.info("Conversation history cleared")

    async def ask(self, question: str) -> AgentAnswer:
        """Answer one user question, using MCP tools as needed."""
        text = question.strip()
        if not text:
            raise AgentError("Question is empty.")
        mcp, llm = self._require_clients()
        self._messages.append({"role": "user", "content": text})
        messages: list[dict[str, Any]] = [self._system_message(), *self._messages]
        traces: list[ToolTrace] = []
        max_turns = self._settings.agent_max_turns

        for turn in range(1, max_turns + 1):
            log.info("Agent LLM turn %d/%d", turn, max_turns)
            response = await llm.complete(messages, self._tools)
            messages.append(response.assistant_message)

            if not response.has_tool_calls:
                answer = (response.content or "").strip()
                self._messages = messages[1:]
                if not answer:
                    raise AgentError(
                        "The model returned an empty answer after "
                        f"{len(traces)} MCP tool call(s)."
                    )
                log.info("Agent finished in %d LLM turn(s) with %d MCP call(s)", turn, len(traces))
                return AgentAnswer(text=answer, tool_traces=traces, llm_turns=turn)

            for call in response.tool_calls:
                trace = await self._run_mcp_tool(mcp, call.name, call.arguments)
                traces.append(trace)
                messages.append(tool_result_message(call.id, trace.result_preview))

        self._messages = messages[1:]
        names = ", ".join(trace.name for trace in traces) or "(none)"
        raise AgentError(
            f"Stopped after {max_turns} LLM turns without a final answer. "
            f"MCP tools used: {names}. Raise AGENT_MAX_TURNS or narrow the question."
        )

    async def _run_mcp_tool(
        self,
        mcp: GitHubMCPClient,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolTrace:
        log.info("Agent invoking MCP tool %s", name)
        try:
            result = await mcp.call_tool(name, arguments)
            return ToolTrace(
                name=name,
                arguments=arguments,
                ok=True,
                result_preview=_clip(result),
            )
        except MCPToolError as exc:
            log.warning("MCP tool %s failed: %s", name, exc)
            return ToolTrace(
                name=name,
                arguments=arguments,
                ok=False,
                result_preview=_clip(f"ERROR: {exc}"),
            )

    def _system_message(self) -> dict[str, str]:
        default_repo = self._settings.default_repo or "(none — ask the user which owner/repo to use)"
        return {"role": "system", "content": _SYSTEM_PROMPT.format(default_repo=default_repo)}

    def _require_clients(self) -> tuple[GitHubMCPClient, LLMClient]:
        if self._mcp is None or self._llm is None:
            raise AgentError("GitHubAgent is not started. Use: async with GitHubAgent() as agent")
        return self._mcp, self._llm


def _clip(text: str, limit: int = _MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n\n[truncated {omitted} characters from MCP tool result]"
