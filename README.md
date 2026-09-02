# GitHub MCP Agent

Explore any GitHub repository in **plain English** — issues, pull requests, commits, repository info, and code — through the official [GitHub MCP Server](https://github.com/github/github-mcp-server), not custom GitHub REST/GraphQL calls.

A tool-calling LLM decides which MCP tools to invoke. The agent never talks to `api.github.com` itself.

**Repository:** [Memoona-Shahid/GitHub-MCP-Agent](https://github.com/Memoona-Shahid/GitHub-MCP-Agent)

---

## Status

| Module | What | State |
|--------|------|--------|
| 1 | Foundation — package, config, logging, errors, env files | **Done** |
| 2 | GitHub MCP client (stdio Docker / remote HTTP / local binary) | **Done** |
| 3 | OpenAI-compatible LLM adapter (tool calling) | **Done** |
| 4 | Agent loop (natural language → MCP tools → answer) | **Done** |
| 5 | CLI chat | Next |
| 6 | Simple web UI | Pending |

---

## Architecture (target)

```
User (CLI or browser)
        │
        ▼
  github_mcp_agent.agent     ← tool-calling loop
        │
        ├── llm_client       ← OpenAI-compatible chat.completions
        └── mcp_client       ← official MCP Python SDK
                    │
                    ▼
         GitHub MCP Server   ← Docker / remote / binary
                    │
                    ▼
              GitHub platform
```

No module other than the MCP client is allowed to call GitHub APIs.

### Layout

```
GitHub-MCP-Agent/
├── github_mcp_agent/
│   ├── __init__.py
│   ├── __main__.py          # python -m github_mcp_agent
│   ├── config.py            # env-backed settings
│   ├── logging_setup.py
│   ├── exceptions.py
│   ├── mcp_client.py        # official GitHub MCP Server client
│   ├── llm_client.py        # OpenAI-compatible tool-calling LLM
│   └── agent.py             # natural-language tool-calling loop
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Prerequisites

- Python 3.10+
- A [GitHub Personal Access Token](https://github.com/settings/personal-access-tokens) (classic or fine-grained). Typical scopes: `repo` (or `public_repo`) and `read:org`.
- An OpenAI-compatible API key (OpenAI, Groq, OpenRouter, Azure, Ollama, …)
- **Docker** (recommended) so the agent can launch [`ghcr.io/github/github-mcp-server`](https://github.com/github/github-mcp-server) over stdio

---

## Module 1 setup

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
```

Edit `.env` and set at least:

- `GITHUB_PERSONAL_ACCESS_TOKEN`
- `OPENAI_API_KEY`
- `LLM_MODEL` (and `OPENAI_BASE_URL` if you are not using OpenAI)

Smoke-check the foundation (prints a **redacted** config summary):

```bash
python -m github_mcp_agent
```

---

## How GitHub access works

The agent starts the official [GitHub MCP Server](https://github.com/github/github-mcp-server) and uses **only** its MCP tools (`get_file_contents`, issue/PR tools, commit/repo tools, …). Nothing in this project calls `api.github.com` directly.

Choose the transport with `GITHUB_MCP_MODE`:

| Mode | When to use |
|------|-------------|
| `docker` (default) | Docker is installed; PAT is passed into the container |
| `remote` | Use GitHub-hosted MCP at `https://api.githubcopilot.com/mcp/` |
| `binary` | You built `github-mcp-server` locally |

Toolsets are limited via `GITHUB_TOOLSETS` so the model sees a focused tool list (issues, PRs, repos/code, context).

### Module 2 smoke test

Requires `GITHUB_PERSONAL_ACCESS_TOKEN` in `.env` and (for the default mode) Docker Desktop running. The first Docker launch pulls `ghcr.io/github/github-mcp-server`.

```bash
python -m github_mcp_agent --list-tools
python -m github_mcp_agent --call-tool get_me
```

### Module 3 smoke test

Requires `OPENAI_API_KEY` (and `OPENAI_BASE_URL` / `LLM_MODEL` if you are not using OpenAI). This does **not** start GitHub MCP.

```bash
python -m github_mcp_agent --llm-ping
```

### Module 4 smoke test

Requires both `GITHUB_PERSONAL_ACCESS_TOKEN` and `OPENAI_API_KEY`. Optional: set `DEFAULT_REPO=owner/name` in `.env`.

```bash
python -m github_mcp_agent --ask "What is octocat/Hello-World about?"
python -m github_mcp_agent --ask "List the latest open issues in octocat/Hello-World"
```

The answer is printed to stdout. The MCP tools the model actually called are listed on stderr.

---

## Security

- Secrets live in `.env` only. `.env` is gitignored.
- Logs never print full tokens — `config.py` masks them.
- Grant the PAT the minimum scopes you are comfortable giving an LLM.
