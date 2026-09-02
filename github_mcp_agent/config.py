"""Load settings from environment variables and an optional `.env` file."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from github_mcp_agent.exceptions import ConfigurationError


class McpMode(str, Enum):
    """How the agent reaches the official GitHub MCP Server."""

    docker = "docker"
    remote = "remote"
    binary = "binary"


class Settings(BaseSettings):
    """All runtime configuration. Names map 1:1 to environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # GitHub MCP — env names are the uppercase field names
    # (e.g. github_personal_access_token → GITHUB_PERSONAL_ACCESS_TOKEN).
    github_personal_access_token: str = ""
    github_mcp_mode: McpMode = McpMode.docker
    github_mcp_image: str = "ghcr.io/github/github-mcp-server"
    github_mcp_remote_url: str = "https://api.githubcopilot.com/mcp/"
    github_mcp_binary_path: str = ""
    github_toolsets: str = "context,repos,issues,pull_requests,users"
    github_host: str = ""

    # LLM
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    agent_max_turns: int = Field(default=12, ge=1, le=50)

    # App
    log_level: str = "INFO"
    default_repo: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = Field(default=8000, ge=1, le=65535)

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.upper().strip()

    @field_validator("github_toolsets")
    @classmethod
    def _normalize_toolsets(cls, value: str) -> str:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        return ",".join(parts)

    @field_validator("default_repo")
    @classmethod
    def _normalize_default_repo(cls, value: str) -> str:
        cleaned = value.strip().removeprefix("https://github.com/").removesuffix(".git")
        return cleaned.strip("/")

    @property
    def toolset_list(self) -> list[str]:
        return [part for part in self.github_toolsets.split(",") if part]

    def masked_github_token(self) -> str:
        return _mask(self.github_personal_access_token)

    def masked_openai_key(self) -> str:
        return _mask(self.openai_api_key)

    def require_github_token(self) -> None:
        if not self.github_personal_access_token.strip():
            raise ConfigurationError(
                "GITHUB_PERSONAL_ACCESS_TOKEN is missing. Copy .env.example to .env and set a PAT."
            )

    def require_llm_key(self) -> None:
        if not self.openai_api_key.strip():
            raise ConfigurationError(
                "OPENAI_API_KEY is missing. Copy .env.example to .env and set an OpenAI-compatible key."
            )

    def require_binary_path(self) -> None:
        if self.github_mcp_mode is McpMode.binary and not self.github_mcp_binary_path.strip():
            raise ConfigurationError(
                "GITHUB_MCP_MODE=binary requires GITHUB_MCP_BINARY_PATH to point at github-mcp-server."
            )

    def summary(self) -> dict[str, str | int | list[str]]:
        """Safe-to-print snapshot (tokens redacted)."""
        return {
            "mcp_mode": self.github_mcp_mode.value,
            "mcp_image": self.github_mcp_image,
            "mcp_remote_url": self.github_mcp_remote_url,
            "toolsets": self.toolset_list,
            "github_host": self.github_host or "github.com",
            "github_token": self.masked_github_token(),
            "llm_model": self.llm_model,
            "openai_base_url": self.openai_base_url,
            "openai_api_key": self.masked_openai_key(),
            "agent_max_turns": self.agent_max_turns,
            "log_level": self.log_level,
            "default_repo": self.default_repo or "(none)",
            "web": f"{self.web_host}:{self.web_port}",
        }


def _mask(secret: str) -> str:
    value = secret.strip()
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()


def reload_settings() -> Settings:
    """Drop the cache — useful in tests."""
    get_settings.cache_clear()
    return get_settings()
