"""
Coder Agent — reads a task description, writes code, runs it, iterates until passing.
Demonstrates: multi-step tool use, persistent task state, crash recovery mid-coding.

Usage:
    TASK="Write a Python function that finds all prime numbers up to N using a sieve" \\
    python examples/coder.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from aios import Agent, tool


class CoderAgent(Agent):
    name = "coder"
    model = "claude-sonnet-4-6"
    version = "1.0.0"
    description = "Writes, runs, and iterates on code until the task is solved."
    system_prompt = (
        "You are an expert Python programmer. Write clean, correct, well-tested code. "
        "When you run code and it fails, read the error carefully and fix it. "
        "Always include a simple test at the bottom of the script to verify correctness."
    )
    config = {
        "max_iterations": 5,
        "language": "python",
    }

    # ── Tools ─────────────────────────────────────────────────────────────────

    @tool
    async def write_file(self, filename: str, content: str) -> str:
        """
        Write content to a file in the working directory.
        filename: The filename to write (e.g. 'solution.py').
        content: The full file content.
        """
        path = Path(filename)
        path.write_text(content, encoding="utf-8")
        lines = len(content.splitlines())
        return f"Written {lines} lines to {filename}"

    @tool
    async def read_file(self, filename: str) -> str:
        """
        Read a file from the working directory.
        filename: The filename to read.
        """
        path = Path(filename)
        if not path.exists():
            return f"File not found: {filename}"
        return path.read_text(encoding="utf-8")

    @tool
    async def run_code(self, filename: str) -> dict:
        """
        Execute a Python file and return stdout, stderr, and exit code.
        filename: The Python file to run.
        """
        result = subprocess.run(
            [sys.executable, filename],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout[:3000],
            "stderr": result.stderr[:2000],
            "success": result.returncode == 0,
        }

    @tool
    async def run_snippet(self, code: str) -> dict:
        """
        Run a Python code snippet directly (without saving to a file).
        code: Python source code to execute.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        try:
            result = subprocess.run(
                [sys.executable, tmp],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[:3000],
                "stderr": result.stderr[:2000],
                "success": result.returncode == 0,
            }
        finally:
            Path(tmp).unlink(missing_ok=True)

    # ── Main logic ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        task = os.environ.get(
            "TASK",
            "Write a Python function that returns the nth Fibonacci number iteratively, "
            "then test it for n=0,1,5,10,20.",
        )

        print(f"\n[{self.name}] task: {task}\n")

        # Check if we already solved this task
        existing = await self.memory.load("solution")
        if existing:
            print(f"[{self.name}] already solved this task in a previous run:")
            print(existing)
            return

        max_iter = self.config.get("max_iterations", 5)

        result = await self.think_with_tools(
            f"""Complete this coding task:

TASK: {task}

Instructions:
1. Write a clean Python solution to solution.py
2. Include tests at the bottom of the file
3. Run the file with run_code
4. If it fails, fix the errors and run again
5. Repeat until all tests pass (exit code 0)
6. Once passing, confirm the final solution

Maximum {max_iter} attempts. Be methodical about fixing errors.
""",
            max_iterations=max_iter * 3,
        )

        # Save the solution to memory
        try:
            solution = await self.read_file("solution.py")
            if solution and "File not found" not in solution:
                await self.memory.save("solution", solution)
                await self.memory.save("task", task)
                await self.memory.log_event("task_completed", {"task": task[:200]})
                print(f"\n[{self.name}] solution saved to memory")
        except Exception:
            pass

        print(f"\n[{self.name}] result:\n{result}")


if __name__ == "__main__":
    CoderAgent.launch()
