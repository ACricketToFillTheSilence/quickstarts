# Deal-digest agent: OpenAI Agents SDK + Airbyte Agents

A runnable example agent that reads your most recent Salesforce opportunities and posts a short deal digest to a Slack channel. It shows how to give an [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) agent live read and write access to your business systems through [Airbyte Agents](https://airbyte.com). The agent uses `build_connector_tools` to expose each connector's tools. This call allows the agent to discover
 what a connector supports at runtime instead of relying on a hard-coded schema.

## Prerequisites

- Python 3.11 or newer.
- An Airbyte account with the Salesforce and Slack connectors connected in your workspace. The Free tier is enough to follow along.
- An OpenAI API key and your Airbyte workspace credentials (see below).
- `uv` or `pip` for dependency management.

## Setup

Install the SDKs and `python-dotenv`:

```bash
uv add openai-agents airbyte-agent-sdk python-dotenv
# or: pip install openai-agents airbyte-agent-sdk python-dotenv
```

Create a `.env` file in the same folder as the script. `connect()` reads the Airbyte credentials from the environment automatically, the OpenAI Agents SDK reads `OPENAI_API_KEY`, and the script reads `TARGET_CHANNEL_ID` to pick the Slack channel.

```
OPENAI_API_KEY=your_openai_key
AIRBYTE_CLIENT_ID=your_client_id
AIRBYTE_CLIENT_SECRET=your_client_secret
TARGET_CHANNEL_ID=C0XXXXXXXXX
```

Get the client ID and client secret from your Airbyte workspace. `connect()` targets the `default` workspace unless you pass a name in `WORKSPACE_NAME`. Set `TARGET_CHANNEL_ID` to the Slack channel's ID (the `C...` string), not its name; copy it from Slack.

The script keeps an explicit `AirbyteAuthConfig` block commented out. Uncomment it and pass `auth_config=auth` to `connect()` if you want to build the auth config yourself rather than let the SDK read the environment.

## Run

```bash
uv run airbyte-openai-agents-example.py
```

The script prints a health line per connector, then prints the agent's final output after it reads Salesforce and posts the digest to Slack in mrkdwn formatting.

## How it works

Each connector exposes three callables through `build_connector_tools(connector, framework="openai_agents")`: `inspect_connector`, `read_skill_docs`, and `execute`. The agent uses them in a progressive flow:

1. `inspect_connector()` reports the connector's metadata and Context Store readiness, and resolves the skill-doc ID the other two calls use.
2. `read_skill_docs()` returns an outline of the connector's entities and actions. `read_skill_docs(section="...")` drills into the guidance for the specific operation the agent is about to run.
3. `execute(entity, action, params)` runs the operation and returns a structured result with `data` (the records) and `meta` (pagination cursors).

The `framework="openai_agents"` argument makes runtime errors surface as the SDK's retry signal, so a wrong `read_skill_docs` section guess feeds the valid outline back to the model instead of aborting the run. When a tool call fails, Airbyte translates the connector exception into a tool result for the agent to correct from. During that recovery loop the script temporarily disables the `airbyte_agent_sdk.translation._decorator` logger so its tracebacks do not clutter the console, then restores the logger to its prior state.

The `register()` helper wraps each callable with `function_tool`. Two details matter:

- `strict_mode=False`. The OpenAI Agents SDK enforces a strict JSON schema by default, which rejects `execute`'s open-ended `params` object, so the tools will not register without it.
- `name_override` with a per-connector prefix (for example `salesforce_execute`, `slack_execute`). Both connectors produce tools with the same three names, and tool names must be unique within one agent, so the prefix keeps the two sets distinct and tells the model which system each tool belongs to.

The agent's `instructions` carry certain domain constraints: read the outline before drilling into a section, copy the exact section ID (including the `actions.` prefix), pass `entity` and `action` as separate arguments, and provide a full SOQL query in `params["q"]` for Salesforce list actions.

The model is set to `gpt-5.6` in the `Agent` constructor. Change that string to use a different model. `build_connector_tools` supports the following AI frameworks:

- `pydantic_ai`: [Pydantic AI](https://docs.airbyte.com/ai-agents/get-started/developer-quickstart/tutorial-pydantic)
- `langchain`: [LangChain](https://docs.airbyte.com/ai-agents/get-started/developer-quickstart/tutorial-langchain)
- `mcp`: [FastMCP](https://docs.airbyte.com/ai-agents/get-started/developer-quickstart/tutorial-fastmcp)

## Files

- `airbyte-openai-agents-example.py`: the agent.
- `airbyte-openai-agents-tutorial.md`: the step-by-step tutorial this script accompanies.
```
