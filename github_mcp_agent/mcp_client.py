"""Client for the official GitHub MCP Server.

This module is the only place the agent talks to GitHub, and it does so
exclusively through MCP (stdio Docker, remote HTTP, or a local binary).
There are no `api.github.com` REST/GraphQL calls here.
"""

from __future__ import annotations

import json
import shutil
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from github_mcp_agent.config import McpMode, Settings, get_settings
from github_mcp_agent.exceptions import ConfigurationError, MCPConnectionError, MCPToolError
from github_mcp_agent.logging_setup import get_logger

log = get_logger("mcp_client")

_STDIO_READ_TIMEOUT = 180.0
_REMOTE_READ_TIMEOUT = 60.0


@dataclass(frozen=True)
class MCPToolSpec:
    """One tool advertised by the GitHub MCP Server, ready for an LLM."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class GitHubMCPClient:
    """Async context manager around an MCP session to GitHub's official server."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._stack: AsyncExitStack | None = None
        self._mcp: Client | None = None

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def connected(self) -> bool:
        return self._mcp is not None

    async def __aenter__(self) -> GitHubMCPClient:
        self._settings.require_github_token()
        self._settings.require_binary_path()
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            self._mcp = await stack.enter_async_context(self._build_client())
        except (ConfigurationError, MCPConnectionError):
            await stack.aclose()
            raise
        except Exception as exc:
            await stack.aclose()
            raise MCPConnectionError(_connection_message(self._settings, exc)) from exc
        self._stack = stack
        info = self._mcp.server_info
        label = f"{info.name} {info.version}" if info else "GitHub MCP Server"
        log.info(
            "Connected to %s via %s (%d toolset(s): %s)",
            label,
            self._settings.github_mcp_mode.value,
            len(self._settings.toolset_list),
            self._settings.github_toolsets,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._mcp = None
        log.info("Disconnected from GitHub MCP Server")

    async def list_tools(self) -> list[MCPToolSpec]:
        """Return every tool the server currently exposes (paginated)."""
        client = self._require_session()
        specs: list[MCPToolSpec] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            for tool in page.tools:
                specs.append(
                    MCPToolSpec(
                        name=tool.name,
                        description=(tool.description or "").strip(),
                        input_schema=_schema_as_dict(getattr(tool, "input_schema", None)),
                    )
                )
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        log.info("Listed %d GitHub MCP tool(s)", len(specs))
        return specs

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Invoke an MCP tool and return its text (or JSON) payload."""
        client = self._require_session()
        payload = arguments or {}
        log.info("Calling MCP tool %s with keys %s", name, sorted(payload.keys()))
        try:
            result = await client.call_tool(name, payload)
        except MCPToolError:
            raise
        except Exception as exc:
            raise MCPToolError(f"MCP tool {name!r} did not complete: {exc}") from exc

        text = _result_text(result)
        if _is_tool_error(result):
            raise MCPToolError(f"MCP tool {name!r} returned an error: {text}")
        return text

    def _require_session(self) -> Client:
        if self._mcp is None:
            raise MCPConnectionError("GitHub MCP client is not connected. Use: async with GitHubMCPClient() as client")
        return self._mcp

    def _build_client(self) -> Client:
        mode = self._settings.github_mcp_mode
        if mode is McpMode.remote:
            return self._build_remote_client()
        if mode is McpMode.docker:
            _assert_docker_available()
            return Client(_docker_parameters(self._settings), read_timeout_seconds=_STDIO_READ_TIMEOUT)
        return Client(_binary_parameters(self._settings), read_timeout_seconds=_STDIO_READ_TIMEOUT)

    def _build_remote_client(self) -> Client:
        import httpx2

        url = self._settings.github_mcp_remote_url.strip()
        http_client = httpx2.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._settings.github_personal_access_token}",
                "X-MCP-Toolsets": self._settings.github_toolsets,
            },
            timeout=httpx2.Timeout(30.0, read=300.0),
            follow_redirects=True,
        )
        return Client(_RemoteTransport(http_client, url), read_timeout_seconds=_REMOTE_READ_TIMEOUT)


class _RemoteTransport:
    """Streamable HTTP transport that owns the authenticated httpx client."""

    def __init__(self, http_client: Any, url: str) -> None:
        self._http = http_client
        self._url = url
        self._cm: Any = None

    async def __aenter__(self) -> Any:
        await self._http.__aenter__()
        self._cm = streamable_http_client(self._url, http_client=self._http)
        try:
            return await self._cm.__aenter__()
        except BaseException:
            await self._http.__aexit__(*sys.exc_info())
            raise

    async def __aexit__(self, *exc: object) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(*exc)
        await self._http.__aexit__(*exc)


def _docker_parameters(settings: Settings) -> StdioServerParameters:
    env = {
        "GITHUB_PERSONAL_ACCESS_TOKEN": settings.github_personal_access_token,
        "GITHUB_TOOLSETS": settings.github_toolsets,
    }
    docker_env_flags = ["-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "-e", "GITHUB_TOOLSETS"]
    if settings.github_host:
        env["GITHUB_HOST"] = settings.github_host
        docker_env_flags.extend(["-e", "GITHUB_HOST"])

    args = [
        "run",
        "-i",
        "--rm",
        *docker_env_flags,
        settings.github_mcp_image,
    ]
    log.info(
        "Launching GitHub MCP Server via Docker image %s (first run may pull the image)",
        settings.github_mcp_image,
    )
    return StdioServerParameters(command="docker", args=args, env=env)


def _binary_parameters(settings: Settings) -> StdioServerParameters:
    env = {
        "GITHUB_PERSONAL_ACCESS_TOKEN": settings.github_personal_access_token,
        "GITHUB_TOOLSETS": settings.github_toolsets,
    }
    if settings.github_host:
        env["GITHUB_HOST"] = settings.github_host
    log.info("Launching GitHub MCP Server binary at %s", settings.github_mcp_binary_path)
    return StdioServerParameters(
        command=settings.github_mcp_binary_path,
        args=["stdio"],
        env=env,
    )


def _assert_docker_available() -> None:
    if shutil.which("docker") is None:
        raise MCPConnectionError(
            "Docker was not found on PATH. Install Docker Desktop and start it, "
            "or set GITHUB_MCP_MODE=remote / GITHUB_MCP_MODE=binary in .env."
        )


def _schema_as_dict(schema: Any) -> dict[str, Any]:
    if schema is None:
        return {"type": "object", "properties": {}}
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_dump"):
        dumped = schema.model_dump(by_alias=True, exclude_none=True)
        return dumped if isinstance(dumped, dict) else {"type": "object", "properties": {}}
    if hasattr(schema, "dict"):
        dumped = schema.dict()
        return dumped if isinstance(dumped, dict) else {"type": "object", "properties": {}}
    return {"type": "object", "properties": {}}


def _is_tool_error(result: Any) -> bool:
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", False))


def _result_text(result: Any) -> str:
    pieces: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            pieces.append(str(text))
        elif isinstance(block, dict) and block.get("text"):
            pieces.append(str(block["text"]))
    if pieces:
        return "\n".join(pieces).strip()
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return json.dumps(structured, indent=2, default=str)
    return "(empty MCP tool result)"


def _connection_message(settings: Settings, exc: BaseException) -> str:
    mode = settings.github_mcp_mode.value
    hint = ""
    if mode == "docker":
        hint = (
            " Confirm Docker Desktop is running, then retry "
            "(the first launch pulls ghcr.io/github/github-mcp-server)."
        )
    elif mode == "remote":
        hint = " Check GITHUB_MCP_REMOTE_URL and that the PAT can access Copilot MCP."
    elif mode == "binary":
        hint = " Check GITHUB_MCP_BINARY_PATH points at a github-mcp-server executable."
    return f"Failed to connect to GitHub MCP Server ({mode}): {exc}.{hint}"
