"""
Bug-triage agent: Claude Agent SDK + Airbyte Agents (SDK path).

What it does:
  Reads new GitHub issues, cross-references Linear for duplicates, and posts a
  triage summary to Slack. Each connector exposes three callables through
  Airbyte's build_connector_tools (inspect_connector, read_skill_docs, execute).
  The Claude Agent SDK has no built-in integration for them, so we wrap each
  callable by hand as a custom @tool.

Run it:
  uv run airbyte-claude-agent-sdk-example.py

Requires a .env with:
  ANTHROPIC_API_KEY        (the Claude Agent SDK uses this to run Claude)
  AIRBYTE_CLIENT_ID
  AIRBYTE_CLIENT_SECRET
  AIRBYTE_WORKSPACE_NAME
"""

import asyncio
import json
import os

from dotenv import load_dotenv

from airbyte_agent_sdk import AirbyteAuthConfig, build_connector_tools, connect
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

load_dotenv()

# Input schemas for Airbyte's three callables, in Claude Agent SDK @tool form.
TOOL_SCHEMAS = {
    "inspect_connector": {"type": "object", "properties": {}},
    "read_skill_docs": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "Section id from the outline. Omit to get the outline itself.",
            }
        },
    },
    "execute": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Entity to operate on, e.g. 'issues'"},
            "action": {
                "type": "string",
                "description": "One of: list, get, create, update, delete, api_search",
            },
            "params": {
                "type": "object",
                "description": "Parameters for the action (filters, body fields, ids)",
                "additionalProperties": True,
            },
        },
        "required": ["entity", "action"],
    },
}


def make_airbyte_tools(slug, connector):
    """Wrap a connector's three Airbyte callables as Claude Agent SDK tools.

    build_connector_tools returns inspect_connector, read_skill_docs, and execute
    as plain async callables. We register each by hand because the Claude Agent
    SDK is not one of build_connector_tools' supported frameworks.
    """
    callables = {fn.__name__: fn for fn in build_connector_tools(connector).as_list()}

    def wrap(name):
        underlying = callables[name]

        @tool(f"{slug}_{name}", underlying.__doc__ or name, TOOL_SCHEMAS[name])
        async def _run(args):
            try:
                result = await underlying(**args)
            except Exception as exc:
                # Surface the error (e.g. a bad section id) so Claude can retry.
                return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
            return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

        return _run

    return [wrap(name) for name in ("inspect_connector", "read_skill_docs", "execute")]


async def main():
    connectors = (
        ("github", connect("github")),
        ("linear", connect("linear")),
        ("slack", connect("slack")),
    )

    # Confirm every connection before handing control to the agent.
    for name, connector in connectors:
        health = await connector.check()
        print(f"{name}: {health.status}")
        if health.status != "healthy":
            print(f"  {name} is not healthy: {health.error}")

    tools = []
    for slug, connector in connectors:
        tools.extend(make_airbyte_tools(slug, connector))

    server = create_sdk_mcp_server(name="airbyte", version="1.0.0", tools=tools)

    options = ClaudeAgentOptions(
        mcp_servers={"airbyte": server},
        allowed_tools=["mcp__airbyte__*"],  # inspect, read_skill_docs, execute for all three
        system_prompt=(
            "You triage engineering bugs. For each connector, inspect it and read its "
            "skill docs to learn the entities and actions before you execute. Check "
            "Linear for duplicates before creating anything, and keep summaries short."
        ),
    )

    prompt = (
        "Look at the GitHub issues opened in the last 24 hours. Check Linear for "
        "related or duplicate issues, group what you find by severity, and post a "
        "short triage summary in the #engineering Slack channel."
    )

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage) and message.subtype == "success":
            print(message.result)

    for _, connector in connectors:
        await connector.close()


if __name__ == "__main__":
    asyncio.run(main())
