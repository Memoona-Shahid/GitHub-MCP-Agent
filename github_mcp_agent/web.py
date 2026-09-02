"""Simple FastAPI web UI for the GitHub MCP Agent."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from github_mcp_agent.agent import GitHubAgent
from github_mcp_agent.config import Settings, get_settings
from github_mcp_agent.exceptions import GitHubMCPAgentError
from github_mcp_agent.logging_setup import get_logger, setup_logging

log = get_logger("web")
_STATIC_DIR = Path(__file__).resolve().parent / "static"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class ToolTraceOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    ok: bool


class ChatResponse(BaseModel):
    text: str
    traces: list[ToolTraceOut]
    llm_turns: int


class HealthResponse(BaseModel):
    ready: bool
    error: str | None = None
    default_repo: str = ""
    tool_count: int = 0
    tool_names: list[str] = Field(default_factory=list)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.lock = asyncio.Lock()
        app.state.agent = None
        app.state.start_error = None
        setup_logging(cfg.log_level)
        log.info("Web UI starting on %s:%s", cfg.web_host, cfg.web_port)
        try:
            agent = GitHubAgent(cfg)
            await agent.__aenter__()
            app.state.agent = agent
            log.info("Web UI connected to GitHub MCP (%d tools)", len(agent.tools))
        except GitHubMCPAgentError as exc:
            app.state.start_error = str(exc)
            log.error("Web UI started without MCP: %s", exc)
        except Exception as exc:
            app.state.start_error = str(exc)
            log.exception("Web UI failed to start the agent")
        yield
        agent = getattr(app.state, "agent", None)
        if agent is not None:
            await agent.__aexit__(None, None, None)
            log.info("Web UI MCP session closed")

    application = FastAPI(
        title="GitHub MCP Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get("/")
    async def index() -> FileResponse:
        page = _STATIC_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(status_code=500, detail="Web UI file is missing: static/index.html")
        return FileResponse(page)

    @application.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        agent: GitHubAgent | None = application.state.agent
        error: str | None = application.state.start_error
        if agent is None:
            return HealthResponse(ready=False, error=error or "Agent is not connected.")
        return HealthResponse(
            ready=True,
            default_repo=agent.default_repo,
            tool_count=len(agent.tools),
            tool_names=[spec.name for spec in agent.tools],
        )

    @application.post("/api/reset")
    async def reset() -> dict[str, str]:
        agent = _require_agent(application)
        async with application.state.lock:
            agent.reset()
        return {"status": "cleared"}

    @application.post("/api/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest) -> ChatResponse:
        agent = _require_agent(application)
        async with application.state.lock:
            try:
                answer = await agent.ask(payload.message)
            except GitHubMCPAgentError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ChatResponse(
            text=answer.text,
            llm_turns=answer.llm_turns,
            traces=[
                ToolTraceOut(name=trace.name, arguments=trace.arguments, ok=trace.ok)
                for trace in answer.tool_traces
            ],
        )

    return application


def _require_agent(application: FastAPI) -> GitHubAgent:
    agent = application.state.agent
    if agent is None:
        detail = application.state.start_error or "GitHub MCP Agent is not connected."
        raise HTTPException(status_code=503, detail=detail)
    return agent


app = create_app()


def run_web(settings: Settings | None = None) -> int:
    """Block on Uvicorn. Used by `python -m github_mcp_agent --web`."""
    import uvicorn

    cfg = settings or get_settings()
    setup_logging(cfg.log_level)
    uvicorn.run(
        app,
        host=cfg.web_host,
        port=cfg.web_port,
        log_level=cfg.log_level.lower(),
    )
    return 0
