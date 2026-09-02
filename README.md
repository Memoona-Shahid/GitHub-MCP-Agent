# GitHub MCP Agent

Explore any GitHub repository in **plain English** — issues, pull requests, commits, repository info, and code — through the official [GitHub MCP Server](https://github.com/github/github-mcp-server).

A tool-calling LLM chooses MCP tools. This project never calls `api.github.com` itself.

**Repository:** [Memoona-Shahid/GitHub-MCP-Agent](https://github.com/Memoona-Shahid/GitHub-MCP-Agent.git)

---

## What you can ask

- “What is `octocat/Hello-World` about?”
- “List open issues in `microsoft/vscode`”
- “Summarize the latest pull requests”
- “Show recent commits on `main`”
- “What does `README.md` say?”

---

## Architecture

```
User (CLI or browser)
        │
        ▼
  github_mcp_agent.agent     ← tool-calling loop
        │
        ├── llm_client       ← OpenAI-compatible Chat Completions
        └── mcp_client       ← official MCP Python SDK
                    │
                    ▼
         GitHub MCP Server   ← Docker / remote / binary
                    │
                    ▼
              GitHub platform
```

| Module | File | Role |
|--------|------|------|
| 1 | `config.py`, `logging_setup.py`, `exceptions.py` | Settings, logging, errors |
| 2 | `mcp_client.py` | GitHub MCP Server client (the only GitHub access path) |
| 3 | `llm_client.py` | OpenAI-compatible tool calling |
| 4 | `agent.py` | Natural language → MCP tools → answer |
| 5 | `cli.py` | Interactive terminal chat |
| 6 | `web.py` + `static/index.html` | Browser chat UI |

---

## Prerequisites

- Python 3.10+
- A [GitHub Personal Access Token](https://github.com/settings/personal-access-tokens) (classic or fine-grained). Typical scopes: `repo` (or `public_repo`) and `read:org`
- An OpenAI-compatible API key (OpenAI, Groq, OpenRouter, Azure, Ollama, …)
- **Docker Desktop** (recommended) so the agent can launch [`ghcr.io/github/github-mcp-server`](https://github.com/github/github-mcp-server)

---

## Setup

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

| Variable | Purpose |
|----------|---------|
| `GITHUB_PERSONAL_ACCESS_TOKEN` | Authenticates the GitHub MCP Server |
| `OPENAI_API_KEY` | Tool-calling LLM |
| `LLM_MODEL` | e.g. `gpt-4o-mini` |
| `OPENAI_BASE_URL` | Change this for Groq, OpenRouter, Ollama, Azure, … |
| `GITHUB_MCP_MODE` | `docker` (default), `remote`, or `binary` |
| `DEFAULT_REPO` | Optional `owner/name` used when a question omits the repo |
| `GITHUB_TOOLSETS` | MCP tool groups. Default: `context,repos,issues,pull_requests,users` |
| `WEB_HOST` / `WEB_PORT` | Browser UI bind address (`127.0.0.1:8000`) |

Confirm settings (tokens are masked):

```bash
python -m github_mcp_agent --config
```

---

## Run

### Interactive CLI

```bash
python -m github_mcp_agent --chat
# or, in a terminal with no other flags:
python -m github_mcp_agent
```

Slash commands: `/help`, `/tools`, `/repo`, `/reset`, `/exit`.

### Browser UI

```bash
python -m github_mcp_agent --web
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The page talks to `/api/chat`, `/api/health`, and `/api/reset`. One MCP session is shared for the process; requests are serialized.

### One-shot question

```bash
python -m github_mcp_agent --ask "What is octocat/Hello-World about?"
python -m github_mcp_agent --ask "List the latest open issues in octocat/Hello-World"
```

### Diagnostics

```bash
python -m github_mcp_agent --list-tools
python -m github_mcp_agent --call-tool get_me
python -m github_mcp_agent --llm-ping
```

The first Docker launch pulls `ghcr.io/github/github-mcp-server` and can take a minute.

---

## How GitHub access works

The agent starts the official GitHub MCP Server and uses **only** its tools. Nothing in this repo calls the GitHub REST or GraphQL API.

| `GITHUB_MCP_MODE` | When to use |
|-------------------|-------------|
| `docker` (default) | Docker is installed; the PAT is passed into the container |
| `remote` | GitHub-hosted MCP at `https://api.githubcopilot.com/mcp/` |
| `binary` | You built `github-mcp-server` locally (`GITHUB_MCP_BINARY_PATH`) |

Keep `GITHUB_TOOLSETS` focused so the model chooses tools more reliably. Commits and file browsing come from the `repos` toolset.

---

## Layout

```
GitHub-MCP-Agent/
├── github_mcp_agent/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── logging_setup.py
│   ├── exceptions.py
│   ├── mcp_client.py
│   ├── llm_client.py
│   ├── agent.py
│   ├── cli.py
│   ├── web.py
│   └── static/index.html
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Security

- Secrets live in `.env` only. `.env` is gitignored.
- Logs and `--config` never print full tokens.
- Grant the PAT the minimum scopes you are comfortable giving an LLM.
- The web UI binds to `127.0.0.1` by default; it is not an authenticated multi-user server.
