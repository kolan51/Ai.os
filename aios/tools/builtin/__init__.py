"""
Built-in tool mixins for Ai.os agents.

Use by inheriting alongside Agent:

    from aios import Agent
    from aios.tools.builtin import WebSearchMixin, FilesystemMixin, ShellMixin

    class MyAgent(Agent, WebSearchMixin, FilesystemMixin):
        async def run(self):
            results = await self.web_search("latest AI news")
            await self.write_file("results.txt", results)
"""

from .discord import DiscordMixin
from .email import EmailMixin
from .filesystem import FilesystemMixin
from .github import GitHubMixin
from .http import HttpMixin
from .linear import LinearMixin
from .notion import NotionMixin
from .postgres import PostgresMixin
from .shell import ShellMixin
from .slack import SlackMixin
from .web import WebSearchMixin

__all__ = [
    "DiscordMixin",
    "EmailMixin",
    "FilesystemMixin",
    "GitHubMixin",
    "HttpMixin",
    "LinearMixin",
    "NotionMixin",
    "PostgresMixin",
    "ShellMixin",
    "SlackMixin",
    "WebSearchMixin",
]
