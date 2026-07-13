# Bug-triage agent: Claude Agent SDK + Airbyte Agents

A runnable example agent that reads new GitHub issues, cross-references Linear for duplicates, and posts a triage summary to Slack. It shows how to give a [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) agent live read and write access to your business systems through [Airbyte Agents](https://airbyte.com).

The agent lives in `airbyte-claude-agent-sdk-example.py`. You wire each connector's Airbyte tools into the agent yourself, which gives you full programmatic control.

## Prerequisites

- Python 3.10 or newer. The Claude Agent SDK bundles the Claude Code CLI, so there is nothing else to install for it to run.
- An Airbyte account with the GitHub, Linear, and Slack connectors connected in your workspace. The Free tier is enough to follow along. Airbyte Agents ships 50+ agent connectors, so you can swap in others; confirm what is live with `list_connectors()`.
- An Anthropic API key and your Airbyte workspace credentials (see below).
- `uv` for dependency management. `pip` works too.

## Setup

Install the two SDKs and `python-dotenv`:

```bash
uv add airbyte-agent-sdk claude-agent-sdk python-dotenv
# or: pip install airbyte-agent-sdk claude-agent-sdk python-dotenv
```

Create a `.env` file in the same folder as the script. `connect()` reads the Airbyte variables from the environment automatically, and the Agent SDK reads `ANTHROPIC_API_KEY`.

```
ANTHROPIC_API_KEY=your_anthropic_key
AIRBYTE_CLIENT_ID=your_client_id
AIRBYTE_CLIENT_SECRET=your_client_secret
AIRBYTE_WORKSPACE_NAME=your_workspace_name
```

Get the client ID, client secret, and workspace name from your Airbyte workspace.

## Run

```bash
uv run airbyte-claude-agent-sdk-example.py
```

The script prints a health line per connector, then streams the agent's work as it reads GitHub and Linear, then posts to Slack.

## How it works

Every Airbyte connector exposes three callables through `build_connector_tools(connector)`: `inspect_connector`, `read_skill_docs`, and `execute`. The agent uses them in a progressive flow instead of loading the whole connector catalog into its prompt up front:

1. `inspect_connector()` reports the connector's metadata and Context Store readiness, and resolves the skill-doc id the other two calls use.
2. `read_skill_docs()` returns an outline of the connector's entities and actions. `read_skill_docs(section="...")` drills into the guidance for the specific operation the agent is about to run.
3. `execute(entity, action, params)` runs the operation and returns a structured result with `data` (the records) and `meta` (pagination cursors).

The Claude Agent SDK is not one of the frameworks `build_connector_tools` registers into automatically, so the `make_airbyte_tools` helper takes each callable and wraps it by hand as a custom `@tool`. Tool names are prefixed per connector (for example `github_execute`) because the three callables share a name across connectors, and all of them register on a single in-process MCP server. `allowed_tools=["mcp__airbyte__*"]` pre-approves every tool on that server so none trip a permission prompt.

Handlers catch exceptions and return `is_error: True`. When the agent guesses a wrong `read_skill_docs` section, the error message carries the valid outline, so Claude can read it and retry instead of the run aborting.

## Files

- `airbyte-claude-agent-sdk-example.py` — the agent.
- `airbyte-claude-agent-sdk-tutorial.md` — full tutorial.
- `airbyte-claude-agent-sdk-quickstart.md` — five-minute quickstart.
