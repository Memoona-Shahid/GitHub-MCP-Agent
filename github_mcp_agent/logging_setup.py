"""Central logging setup.

Keep one named logger (`github_mcp_agent`) so every module can call
`logging.getLogger(__name__)` and inherit this format. Secrets must never be
logged — callers are responsible for redacting tokens before interpolation.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

LOGGER_NAME = "github_mcp_agent"
_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure the package logger once and return it.

    Safe to call repeatedly; later calls only update the level.
    """
    global _configured

    logger = logging.getLogger(LOGGER_NAME)
    resolved = _parse_level(level)
    logger.setLevel(resolved)

    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT))
        logger.addHandler(handler)
        logger.propagate = False
        _configured = True

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a child logger under `github_mcp_agent`."""
    if not name or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    if name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def _parse_level(level: str) -> int:
    numeric = getattr(logging, str(level).upper(), None)
    if not isinstance(numeric, int):
        return logging.INFO
    return numeric
