
from aios import Agent
class A(Agent):
    name = "a"; model = "claude-haiku-4-5-20251001"; system_prompt = "x"
    async def run(self): return "hi"
