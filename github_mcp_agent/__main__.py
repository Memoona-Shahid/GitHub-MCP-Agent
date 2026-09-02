"""Entry point for foundation checks, MCP/LLM smoke tests, and one-shot questions.

Run from the project root:

    python -m github_mcp_agent --config
    python -m github_mcp_agent --chat
    python -m github_mcp_agent --list-tools
    python -m github_mcp_agent --call-tool get_me
    python -m github_mcp_agent --llm-ping
    python -m github_mcp_agent --ask "What is octocat/Hello-World about?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from github_mcp_agent.config import Settings, get_settings
from github_mcp_agent.exceptions import ConfigurationError, GitHubMCPAgentError
from github_mcp_agent.logging_setup import get_logger, setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m github_mcp_agent",
        description="GitHub MCP Agent: explore repositories in plain English via MCP.",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Connect to the GitHub MCP Server and print advertised tools.",
    )
    parser.add_argument(
        "--call-tool",
        metavar="NAME",
        help="Connect and invoke a single MCP tool (JSON args via --args).",
    )
    parser.add_argument(
        "--args",
        default="{}",
        help='JSON object of tool arguments, e.g. {"owner":"octocat","repo":"Hello-World"}',
    )
    parser.add_argument(
        "--llm-ping",
        action="store_true",
        help="Send a tiny prompt to the configured OpenAI-compatible LLM (no GitHub/MCP).",
    )
    parser.add_argument(
        "--ask",
        metavar="QUESTION",
        help="Ask one natural-language question (starts MCP + LLM agent loop).",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Start an interactive chat (default when run in a terminal with no other flags).",
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Print a redacted settings summary and exit.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("bootstrap")

    if args.config:
        return _print_config(settings, log)
    if args.ask:
        return asyncio.run(_run_ask(args.ask, log))
    if args.llm_ping:
        return asyncio.run(_run_llm_ping(log))
    if args.list_tools or args.call_tool:
        return asyncio.run(_run_mcp_command(args, log))
    if args.chat or _should_start_chat():
        from github_mcp_agent.cli import run_chat

        return asyncio.run(run_chat(settings))

    return _print_config(settings, log)


def _should_start_chat() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _print_config(settings: Settings, log: logging.Logger) -> int:
    log.info("GitHub MCP Agent foundation loaded")
    print(json.dumps(settings.summary(), indent=2))

    missing: list[str] = []
    for checker in (
        settings.require_github_token,
        settings.require_llm_key,
        settings.require_binary_path,
    ):
        try:
            checker()
        except ConfigurationError as exc:
            missing.append(str(exc))

    if missing:
        print("\nConfiguration notes (expected until .env is filled in):", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 0

    log.info("Required secrets are present (values are never printed)")
    return 0


async def _run_ask(question: str, log: logging.Logger) -> int:
    from github_mcp_agent.agent import GitHubAgent

    if not question.strip():
        print("error: Question is empty.", file=sys.stderr)
        return 2

    try:
        async with GitHubAgent() as agent:
            answer = await agent.ask(question)
    except GitHubMCPAgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(answer.text)
    if answer.tool_traces:
        print(file=sys.stderr)
        print(
            f"MCP tools used ({len(answer.tool_traces)} call(s), {answer.llm_turns} LLM turn(s)):",
            file=sys.stderr,
        )
        for trace in answer.tool_traces:
            status = "ok" if trace.ok else "error"
            print(f"  [{status}] {trace.name}({_brief_args(trace.arguments)})", file=sys.stderr)
    log.info("Ask complete: %d tool call(s)", len(answer.tool_traces))
    return 0


async def _run_llm_ping(log: logging.Logger) -> int:
    from github_mcp_agent.llm_client import LLMClient

    try:
        async with LLMClient() as llm:
            reply = await llm.ping()
            print(reply)
            log.info("LLM ping ok (model=%s)", llm.model)
    except GitHubMCPAgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


async def _run_mcp_command(args: argparse.Namespace, log: logging.Logger) -> int:
    from github_mcp_agent.mcp_client import GitHubMCPClient

    try:
        tool_args = json.loads(args.args)
        if not isinstance(tool_args, dict):
            raise ValueError("--args must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Invalid --args: {exc}", file=sys.stderr)
        return 2

    try:
        async with GitHubMCPClient() as client:
            if args.list_tools:
                tools = await client.list_tools()
                print(json.dumps([tool.as_dict() for tool in tools], indent=2))
                log.info("Listed %d MCP tool(s)", len(tools))
            if args.call_tool:
                print(await client.call_tool(args.call_tool, tool_args))
    except GitHubMCPAgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _brief_args(arguments: dict) -> str:
    raw = json.dumps(arguments, default=str, ensure_ascii=False)
    if len(raw) <= 140:
        return raw
    return raw[:137] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
