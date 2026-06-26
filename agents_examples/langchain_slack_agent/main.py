import asyncio
from agent import agent

async def main():
    print("Slack Agent ready! Ask questions about Slack.")
    print("Type 'quit' to exit.\n")

    history = []

    while True:
        prompt = input("You: ")
        if prompt.lower() in ("quit", "exit", "q"):
            break
        history.append({"role": "user", "content": prompt})
        result = await agent.ainvoke({"messages": history})
        response = result["messages"][-1].content
        history = result["messages"]
        print(f"\nAgent: {response}\n")

if __name__ == "__main__":
    asyncio.run(main())