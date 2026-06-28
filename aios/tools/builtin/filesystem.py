from __future__ import annotations

import os
from pathlib import Path

from ..registry import tool


class FilesystemMixin:
    """
    Adds safe filesystem tools to an agent.

    All paths are resolved relative to the working directory.
    Writes outside cwd are blocked unless the agent sets allow_absolute = True.
    """

    allow_absolute: bool = False

    def _safe_path(self, path: str) -> Path:
        resolved = Path(path).resolve()
        if not self.allow_absolute:
            cwd = Path.cwd().resolve()
            if not str(resolved).startswith(str(cwd)):
                raise PermissionError(
                    f"Path escapes working directory: {resolved}\n"
                    "Set allow_absolute = True on your agent to allow absolute paths."
                )
        return resolved

    @tool
    async def read_file(self, path: str) -> str:
        """
        Read a text file and return its contents.
        path: File path (relative to cwd).
        """
        p = self._safe_path(path)
        if not p.exists():
            return f"File not found: {path}"
        if not p.is_file():
            return f"Not a file: {path}"
        try:
            return p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Binary file — cannot read as text: {path}"

    @tool
    async def write_file(self, path: str, content: str, append: bool = False) -> str:
        """
        Write text to a file, creating parent directories if needed.
        path: File path (relative to cwd).
        content: Text content to write.
        append: Append to existing content instead of overwriting.
        """
        p = self._safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with p.open(mode, encoding="utf-8") as f:
            f.write(content)
        lines = len(content.splitlines())
        action = "Appended" if append else "Written"
        return f"{action} {lines} lines to {path}"

    @tool
    async def list_directory(self, path: str = ".", pattern: str = "*") -> str:
        """
        List files and directories at a path.
        path: Directory to list (default: current directory).
        pattern: Glob pattern to filter results (e.g. '*.py').
        """
        p = self._safe_path(path)
        if not p.exists():
            return f"Directory not found: {path}"
        if not p.is_dir():
            return f"Not a directory: {path}"

        entries = sorted(p.glob(pattern))
        if not entries:
            return f"No entries matching '{pattern}' in {path}"

        lines = []
        for entry in entries[:200]:
            rel = entry.relative_to(p)
            suffix = "/" if entry.is_dir() else ""
            size = f"  {entry.stat().st_size:>10,} B" if entry.is_file() else ""
            lines.append(f"{str(rel)}{suffix}{size}")

        result = "\n".join(lines)
        if len(entries) > 200:
            result += f"\n\n[... and {len(entries) - 200} more entries]"
        return result

    @tool
    async def delete_file(self, path: str) -> str:
        """
        Delete a file (not a directory).
        path: File path to delete.
        """
        p = self._safe_path(path)
        if not p.exists():
            return f"File not found: {path}"
        if not p.is_file():
            return f"Not a file — will not delete directories: {path}"
        p.unlink()
        return f"Deleted: {path}"

    @tool
    async def file_exists(self, path: str) -> bool:
        """
        Check whether a file or directory exists.
        path: Path to check.
        """
        try:
            return self._safe_path(path).exists()
        except PermissionError:
            return False
