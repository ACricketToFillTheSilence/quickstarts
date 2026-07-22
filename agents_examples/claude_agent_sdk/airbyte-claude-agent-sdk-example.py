"""
Bug-triage agent: Claude Agent SDK + Airbyte Agents (SDK path).

What it does:
  Reads new GitHub issues, cross-references Linear for duplicates, and posts a
  triage summary to Slack. Each connector exposes three async methods
  (inspect_connector, read_skill_docs, execute). We wrap each with Airbyte's
  agent_tool decorator, which enriches the docstring and steers the model
  through the inspect -> read docs -> execute flow, then bridge each one into a
  Claude Agent SDK @tool since the Claude Agent SDK is not a framework
  agent_tool registers for automatically.

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

from dotenv import load_dotenv

from airbyte_agent_sdk import AirbyteToolError, connect
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

# The Claude Agent SDK still needs an input schema for each tool, keyed by the
# agent_tool role.
SCHEMAS = {
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
    """Wrap a connector's three async methods as Claude Agent SDK tools.

    agent_tool infers each method's role from its signature, enriches the
    docstring, and defaults to framework="none" so failures raise
    AirbyteToolError. The bridge then adapts the result to the content-array
    shape the Claude Agent SDK expects.
    """
    # agent_tool is a classmethod on the typed connector class; reach it off the instance.
    agent_tool = type(connector).agent_tool
    inspect_name = f"{slug}_inspect"
    docs_name = f"{slug}_read_docs"
    execute_name = f"{slug}_execute"

    @agent_tool()  # role inferred from the empty signature: inspect_connector
    async def inspect() -> str:
        """Inspect this connector: metadata, Context Store readiness, and its skill-doc id."""
        return json.dumps(await connector.inspect_connector(), default=str)

    @agent_tool()  # role inferred from the section-only signature: read_skill_docs
    async def read_docs(section: str | None = None) -> str:
        """Read the connector's skill docs. Omit the section to get the outline first."""
        return await connector.read_skill_docs(section)

    # execute passes its role explicitly and names its siblings, so agent_tool can
    # steer the model: inspect -> read the outline -> read one section -> execute.
    @agent_tool("execute", inspect_tool=inspect_name, docs_tool=docs_name)
    async def execute(entity: str, action: str, params: dict | None = None) -> str:
        """Run an operation once you've read the relevant skill-doc section."""
        return json.dumps(await connector.execute(entity, action, params or {}), default=str)

    # Bridge each agent_tool function into a Claude Agent SDK tool. agent_tool has
    # already enriched __doc__ and set framework="none", so failures arrive as
    # AirbyteToolError; the bridge adapts the return shape and surfaces errors.
    def bridge(name, role, fn):
        @tool(name, fn.__doc__ or name, SCHEMAS[role])
        async def _run(args):
            try:
                text = await fn(**args)
            except AirbyteToolError as exc:
                # Surface the error (e.g. a bad section id) so Claude can retry.
                return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
            return {"content": [{"type": "text", "text": text}]}

        return _run

    return [
        bridge(inspect_name, "inspect_connector", inspect),
        bridge(docs_name, "read_skill_docs", read_docs),
        bridge(execute_name, "execute", execute),
    ]


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
        allowed_tools=["mcp__airbyte__*"],  # inspect, read_docs, execute for all three
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
