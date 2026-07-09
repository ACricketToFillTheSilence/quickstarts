import json

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.tools import tool
from airbyte_agent_sdk import connect
from airbyte_agent_sdk.connectors.slack import SlackConnector


load_dotenv()

slack = connect("slack")

@tool
@SlackConnector.tool_utils
async def slack_execute(entity: str, action: str, params: dict | None = None) -> str:
    result = await slack.execute(entity, action, params or {})
    return json.dumps(result, default=str)

llm = ChatOpenAI(model="gpt-4o", temperature=0.2)

agent = create_agent(
    llm,
    [slack_execute],
    system_prompt=(
        "You are a helpful assistant that can access Slack data through the "
        "slack_execute tool. Be concise and accurate. "
    ),
)


async def _demo():
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "List the files I modified in May 2026."}]}
    )
    print(result["messages"][-1].content)

if __name__ == "__main__":
    import asyncio

    asyncio.run(_demo())