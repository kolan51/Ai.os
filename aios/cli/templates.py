"""File templates for `aios init`."""

AGENT_TEMPLATE = '''\
from aios import Agent, tool


class {class_name}(Agent):
    name = "{agent_name}"
    model = "{model}"
    description = ""
    system_prompt = (
        "You are a helpful AI agent."
    )

    @tool
    async def example_tool(self, input: str) -> str:
        """
        An example tool — replace with your own.
        input: The input to process.
        """
        return f"processed: {{input}}"

    async def run(self) -> None:
        # Check long-term memory for previous work
        previous = await self.memory.load("result")
        if previous:
            print(f"[{{self.name}}] resuming — previous result: {{previous}}")

        result = await self.think_with_tools(
            "Process this example task using the available tools."
        )

        await self.memory.save("result", result)
        print(f"[{{self.name}}] done: {{result}}")


if __name__ == "__main__":
    {class_name}.launch()
'''

ENV_TEMPLATE = """\
# {agent_name} — environment variables
# See .env.example for all options

ANTHROPIC_API_KEY=your-key-here
# OPENAI_API_KEY=
# GOOGLE_API_KEY=
"""

GITIGNORE_TEMPLATE = """\
__pycache__/
*.pyc
.env
.aios/
*.db
"""
