"""Typed errors for the GitHub MCP Agent.

Callers should catch these instead of raw SDK exceptions so the CLI and web UI
can show a consistent message.
"""


class GitHubMCPAgentError(Exception):
    """Base error for every failure this agent knows how to report."""


class ConfigurationError(GitHubMCPAgentError):
    """Missing or invalid environment / settings."""


class MCPConnectionError(GitHubMCPAgentError):
    """Could not start or talk to the GitHub MCP Server."""


class MCPToolError(GitHubMCPAgentError):
    """An MCP tool was called and the server returned an error result."""


class LLMError(GitHubMCPAgentError):
    """The tool-calling LLM failed, timed out, or returned an unusable response."""


class AgentError(GitHubMCPAgentError):
    """The agent loop could not finish the user's request."""
