"""
Ai.os — Persistent agent runtime.
Deploy agents that remember, survive crashes, and run forever.
"""

from .agent import Agent
from .scheduling import schedule
from .triggers import trigger
from .tools.builtin import (
    DiscordMixin,
    EmailMixin,
    FilesystemMixin,
    GitHubMixin,
    HttpMixin,
    LinearMixin,
    NotionMixin,
    PostgresMixin,
    ShellMixin,
    SlackMixin,
    WebSearchMixin,
)
from .tools.registry import tool

__all__ = [
    "Agent",
    "tool",
    "schedule",
    "trigger",
    "WebSearchMixin",
    "FilesystemMixin",
    "ShellMixin",
    "HttpMixin",
    "GitHubMixin",
    "SlackMixin",
    "DiscordMixin",
    "NotionMixin",
    "LinearMixin",
    "PostgresMixin",
    "EmailMixin",
]
__version__ = "0.2.0"
