"""Interactive terminal chat over the GitHub MCP agent."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from github_mcp_agent.agent import AgentAnswer, GitHubAgent
from github_mcp_agent.config import Settings, get_settings
from github_mcp_agent.exceptions import GitHubMCPAgentError
from github_mcp_agent.logging_setup import get_logger, setup_logging

log = get_logger("cli")

_EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit"}
_HELP_TEXT = """[bold]Commands[/bold]
  [cyan]/help[/]    Show this help
  [cyan]/tools[/]   List GitHub MCP tools
  [cyan]/repo[/]    Show the default repository
  [cyan]/reset[/]   Clear conversation history (keep MCP session)
  [cyan]/exit[/]    Leave the chat ([cyan]/quit[/] also works)

Ask in plain English, for example:
  What is octocat/Hello-World about?
  Show open issues in microsoft/vscode
  Summarize the latest pull requests
  What does README.md say?
"""


async def run_chat(settings: Settings | None = None) -> int:
    """Connect once, then chat until the user exits."""
    console = Console()
    cfg = settings or get_settings()
    console.print("[dim]Starting GitHub MCP Agent (this may pull a Docker image on first run)…[/]")
    try:
        async with GitHubAgent(cfg) as agent:
            _print_banner(console, agent)
            return await _repl(console, agent)
    except GitHubMCPAgentError as exc:
        console.print(f"[red]error:[/] {exc}")
        return 1
    except KeyboardInterrupt:
        console.print("\n[dim]bye[/]")
        return 0


async def _repl(console: Console, agent: GitHubAgent) -> int:
    while True:
        try:
            raw = await asyncio.to_thread(Prompt.ask, "[bold cyan]You[/]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            return 0

        line = (raw or "").strip()
        if not line:
            continue

        command = line.lower()
        if command in _EXIT_COMMANDS:
            console.print("[dim]bye[/]")
            return 0
        if command == "/help":
            console.print(_HELP_TEXT)
            continue
        if command == "/reset":
            agent.reset()
            console.print("[green]Conversation cleared.[/] MCP session is still connected.")
            continue
        if command == "/tools":
            _print_tools(console, agent)
            continue
        if command == "/repo":
            repo = agent.default_repo or "(not set — name owner/repo in your question)"
            console.print(f"Default repository: [bold]{repo}[/]")
            continue
        if line.startswith("/"):
            console.print(f"[yellow]Unknown command:[/] {line}  (try [cyan]/help[/])")
            continue

        try:
            with console.status("[dim]Using GitHub MCP tools…[/]", spinner="dots"):
                answer = await agent.ask(line)
        except GitHubMCPAgentError as exc:
            console.print(f"[red]error:[/] {exc}")
            log.warning("Chat turn failed: %s", exc)
            continue
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled this question. MCP session is still connected.[/]")
            continue

        _print_answer(console, answer)


def _print_banner(console: Console, agent: GitHubAgent) -> None:
    repo = agent.default_repo or "name a repo as owner/name in your question"
    console.print(
        Panel.fit(
            "[bold]GitHub MCP Agent[/bold]\n"
            "Explore issues, pull requests, commits, and code in plain English.\n"
            f"Default repo: [cyan]{repo}[/]\n"
            f"MCP tools loaded: [cyan]{len(agent.tools)}[/]\n\n"
            "Type [cyan]/help[/] for commands, [cyan]/exit[/] to quit.",
            border_style="cyan",
            title="chat",
        )
    )


def _print_tools(console: Console, agent: GitHubAgent) -> None:
    table = Table(title="GitHub MCP tools", show_lines=False)
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Description", overflow="fold")
    for spec in agent.tools:
        description = spec.description.replace("\n", " ").strip() or "—"
        if len(description) > 100:
            description = description[:97] + "..."
        table.add_row(spec.name, description)
    console.print(table)


def _print_answer(console: Console, answer: AgentAnswer) -> None:
    console.print()
    console.print("[bold green]Agent[/]")
    console.print(Markdown(answer.text))
    if answer.tool_traces:
        parts = []
        for trace in answer.tool_traces:
            mark = "ok" if trace.ok else "err"
            parts.append(f"{trace.name} [{mark}] {_brief_args(trace.arguments)}")
        console.print()
        console.print("[dim]MCP: " + " · ".join(parts) + "[/]")
    console.print()


def _brief_args(arguments: dict[str, Any]) -> str:
    raw = json.dumps(arguments, default=str, ensure_ascii=False)
    if len(raw) <= 80:
        return raw
    return raw[:77] + "..."


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    return asyncio.run(run_chat(settings))


if __name__ == "__main__":
    raise SystemExit(main())
