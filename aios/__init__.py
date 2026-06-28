"""
Ai.os — Persistent agent runtime.
Deploy agents that remember, survive crashes, and run forever.
"""

from .agent import Agent
from .scheduling import schedule
from .tools.builtin import FilesystemMixin, GitHubMixin, HttpMixin, ShellMixin, WebSearchMixin
from .tools.registry import tool

__all__ = [
    "Agent",
    "tool",
    "schedule",
    "WebSearchMixin",
    "FilesystemMixin",
    "ShellMixin",
    "HttpMixin",
    "GitHubMixin",
]
__version__ = "0.1.0"
