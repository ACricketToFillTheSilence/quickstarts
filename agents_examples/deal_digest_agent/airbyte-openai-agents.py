"""
Salesforce -> Slack deal agent, built with the OpenAI Agents SDK and the
Airbyte Agent SDK.

What it does:
  Reads recent opportunities from Salesforce through Airbyte, then posts a
  short deal digest to a Slack channel. Read (Salesforce) plus write (Slack)
  in one agent run, so it demonstrates Ask and Act, not just Ask.

Setup:
  uv add openai-agents airbyte-agent-sdk python-dotenv
  Copy .env.example to .env and fill in the values (see the tutorial).

Run:
  uv run airbyte-openai-agents.py
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from agents import Agent, Runner, function_tool

from airbyte_agent_sdk import AirbyteAuthConfig, build_connector_tools, connect

load_dotenv()

# Airbyte handles auth implicitly from the .env file for every connector once you connect.
# To run auth yourself, uncomment the following lines.
#auth = AirbyteAuthConfig(
#    airbyte_client_id=os.getenv("AIRBYTE_CLIENT_ID"),
#    airbyte_client_secret=os.getenv("AIRBYTE_CLIENT_SECRET"),
#)

salesforce = connect("salesforce")#, auth_config=auth)
slack = connect("slack")#, auth_config=auth)


def register(connector, prefix):
    # build_connector_tools returns inspect_connector, read_skill_docs, and
    # execute bound to one connector. Prefix names so the two sets don't collide.
    tools = build_connector_tools(connector, framework="openai_agents")
    return [
        function_tool(tool, name_override=f"{prefix}_{tool.__name__}", strict_mode=False)
        for tool in tools.as_list()
    ]


agent = Agent(
    name="Deal Digest Agent",
    model="gpt-5.6",
    instructions=(
        "You summarize sales pipeline activity. Before your first execute call, "
        "inspect the connector and read its skill docs to learn the available "
        "entities, actions, and parameters. Read recent opportunities from "
        "Salesforce, then post a short digest to the Slack channel the user names."
        "Before executing an Airbyte operation, call the connector's "
        "read_skill_docs tool without a section to obtain its outline. Copy the "
        "exact section ID from that outline, including the 'actions.' prefix, "
        "and read that section. For execute calls, pass the entity and action "
        "separately; never pass 'entity.action' as either argument. Salesforce "
        "list actions require a complete SOQL query in params['q']."
    ),
    tools=[*register(salesforce, "salesforce"), *register(slack, "slack")],
)


async def main():
    try:
        # Confirm both connections before the agent runs, so a bad credential
        # fails here instead of deep inside the agent loop.
        for name, connector in (("Salesforce", salesforce), ("Slack", slack)):
            health = await connector.check()
            print(f"{name}: {health.status}")
            if health.status != "healthy":
                print(f"  {health.error}")
                return

        # Airbyte translates connector exceptions into tool results so the agent
        # can correct its call. Silence only the translator's traceback logger
        # while that recovery loop is running, then restore its prior state.
        tool_error_logger = logging.getLogger("airbyte_agent_sdk.translation._decorator")
        tool_error_logging_was_disabled = tool_error_logger.disabled
        try:
            tool_error_logger.disabled = True
            result = await Runner.run(
                agent,
                "Summarize our 5 most recent Salesforce opportunities and post the "
                f"digest to the {os.getenv('TARGET_CHANNEL_ID')} Slack channel."
                "Use mrkdwn formatting in the message.",
            )
        finally:
            tool_error_logger.disabled = tool_error_logging_was_disabled

        print(result.final_output)
    finally:
        try:
            await salesforce.close()
        finally:
            await slack.close()


if __name__ == "__main__":
    asyncio.run(main())
