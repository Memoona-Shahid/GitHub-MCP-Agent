"""Entry point for foundation checks and MCP client smoke tests.

Run from the project root:

    python -m github_mcp_agent
    python -m github_mcp_agent --list-tools
    python -m github_mcp_agent --call-tool get_me
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
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("bootstrap")

    if args.list_tools or args.call_tool:
        return asyncio.run(_run_mcp_command(args, log))

    return _print_config(settings, log)


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


if __name__ == "__main__":
    raise SystemExit(main())
