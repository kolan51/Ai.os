from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import ClassVar

from ..registry import tool


class ShellMixin:
    """
    Adds shell execution tools to an agent.

    Safety: commands are run as subprocesses — no shell injection risk from
    the LLM because args are passed as a list, not interpolated into a shell string.
    Timeout is enforced. Stdout/stderr are capped at 8000 chars each.
    """

    shell_timeout: ClassVar[int] = 30  # seconds
    shell_allowed_commands: ClassVar[list[str] | None] = None  # None = allow all

    @tool
    async def run_command(self, command: list[str], cwd: str = ".") -> dict:
        """
        Run a shell command and return stdout, stderr, and exit code.
        command: Command and arguments as a list, e.g. ["python", "script.py"].
        cwd: Working directory for the command.
        """
        if self.shell_allowed_commands is not None:
            if not command or command[0] not in self.shell_allowed_commands:
                return {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": f"Command not allowed: {command[0]!r}. Allowed: {self.shell_allowed_commands}",
                    "success": False,
                }

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.shell_timeout
            )
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode(errors="replace")[:8000],
                "stderr": stderr.decode(errors="replace")[:4000],
                "success": proc.returncode == 0,
            }
        except asyncio.TimeoutError:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {self.shell_timeout}s",
                "success": False,
            }
        except FileNotFoundError:
            return {
                "exit_code": 127,
                "stdout": "",
                "stderr": f"Command not found: {command[0]!r}",
                "success": False,
            }

    @tool
    async def run_python(self, code: str) -> dict:
        """
        Execute a Python code snippet and return the output.
        code: Python source code to run.
        """
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, tmp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.shell_timeout
            )
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode(errors="replace")[:8000],
                "stderr": stderr.decode(errors="replace")[:4000],
                "success": proc.returncode == 0,
            }
        except asyncio.TimeoutError:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Code timed out after {self.shell_timeout}s",
                "success": False,
            }
        finally:
            Path(tmp).unlink(missing_ok=True)
