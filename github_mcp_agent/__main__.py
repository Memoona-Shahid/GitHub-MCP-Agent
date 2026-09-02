"""Smoke-check Module 1: load config, configure logging, print a redacted summary.

Run from the project root:

    python -m github_mcp_agent
"""

from __future__ import annotations

import json
import sys

from github_mcp_agent.config import get_settings
from github_mcp_agent.exceptions import ConfigurationError
from github_mcp_agent.logging_setup import get_logger, setup_logging


def main() -> int:
    settings = get_settings()
    logger = setup_logging(settings.log_level)
    log = get_logger("bootstrap")

    log.info("GitHub MCP Agent foundation loaded (module 1)")
    print(json.dumps(settings.summary(), indent=2))

    missing: list[str] = []
    try:
        settings.require_github_token()
    except ConfigurationError as exc:
        missing.append(str(exc))
    try:
        settings.require_llm_key()
    except ConfigurationError as exc:
        missing.append(str(exc))
    try:
        settings.require_binary_path()
    except ConfigurationError as exc:
        missing.append(str(exc))

    if missing:
        print("\nConfiguration notes (expected until .env is filled in):", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 0

    log.info("Required secrets are present (values are never printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
