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

from .filesystem import FilesystemMixin
from .github import GitHubMixin
from .http import HttpMixin
from .shell import ShellMixin
from .web import WebSearchMixin

__all__ = [
    "FilesystemMixin",
    "GitHubMixin",
    "HttpMixin",
    "ShellMixin",
    "WebSearchMixin",
]
