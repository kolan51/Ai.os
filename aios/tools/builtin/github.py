from __future__ import annotations

import os
from typing import Any

from ..registry import tool


class GitHubMixin:
    """
    Adds GitHub API tools to an agent.

    Requires GITHUB_TOKEN in environment.
    Supports repos, issues, PRs, and code search.

    Usage::

        from aios import Agent
        from aios.tools.builtin import GitHubMixin

        class CodeAgent(Agent, GitHubMixin):
            name = "code_agent"
            model = "claude-sonnet-4-6"

            async def run(self):
                issues = await self.github_list_issues("owner/repo")
    """

    @property
    def _gh_headers(self) -> dict[str, str]:
        token = os.environ.get("GITHUB_TOKEN", "")
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aios-agent/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _gh_get(self, path: str, params: dict | None = None) -> Any:
        import httpx

        url = f"https://api.github.com{path}"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self._gh_headers, params=params or {})
            resp.raise_for_status()
            return resp.json()

    async def _gh_post(self, path: str, body: dict) -> Any:
        import httpx

        url = f"https://api.github.com{path}"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=self._gh_headers, json=body)
            resp.raise_for_status()
            return resp.json()

    @tool
    async def github_get_repo(self, repo: str) -> dict:
        """
        Get basic info about a GitHub repository.
        repo: Repository in 'owner/name' format.
        """
        data = await self._gh_get(f"/repos/{repo}")
        return {
            "name": data["full_name"],
            "description": data.get("description", ""),
            "stars": data["stargazers_count"],
            "forks": data["forks_count"],
            "open_issues": data["open_issues_count"],
            "language": data.get("language", ""),
            "url": data["html_url"],
            "default_branch": data["default_branch"],
        }

    @tool
    async def github_list_issues(self, repo: str, state: str = "open", limit: int = 20) -> list[dict]:
        """
        List issues for a repository.
        repo: Repository in 'owner/name' format.
        state: Issue state — 'open', 'closed', or 'all'.
        limit: Maximum number of issues to return.
        """
        data = await self._gh_get(
            f"/repos/{repo}/issues",
            params={"state": state, "per_page": min(limit, 100), "sort": "updated"},
        )
        return [
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "labels": [l["name"] for l in i.get("labels", [])],
                "created_at": i["created_at"][:10],
                "url": i["html_url"],
                "body_preview": (i.get("body") or "")[:200],
            }
            for i in data
            if "pull_request" not in i  # exclude PRs from issue list
        ][:limit]

    @tool
    async def github_get_issue(self, repo: str, number: int) -> dict:
        """
        Get full details of a specific issue.
        repo: Repository in 'owner/name' format.
        number: Issue number.
        """
        data = await self._gh_get(f"/repos/{repo}/issues/{number}")
        return {
            "number": data["number"],
            "title": data["title"],
            "state": data["state"],
            "body": data.get("body", ""),
            "labels": [l["name"] for l in data.get("labels", [])],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
            "url": data["html_url"],
            "comments": data["comments"],
        }

    @tool
    async def github_create_issue(self, repo: str, title: str, body: str, labels: list | None = None) -> dict:
        """
        Create a new issue on a repository.
        repo: Repository in 'owner/name' format.
        title: Issue title.
        body: Issue description (markdown supported).
        labels: Optional list of label names.
        """
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        data = await self._gh_post(f"/repos/{repo}/issues", payload)
        return {
            "number": data["number"],
            "url": data["html_url"],
            "title": data["title"],
        }

    @tool
    async def github_list_prs(self, repo: str, state: str = "open", limit: int = 20) -> list[dict]:
        """
        List pull requests for a repository.
        repo: Repository in 'owner/name' format.
        state: PR state — 'open', 'closed', or 'all'.
        limit: Maximum number of PRs to return.
        """
        data = await self._gh_get(
            f"/repos/{repo}/pulls",
            params={"state": state, "per_page": min(limit, 100), "sort": "updated"},
        )
        return [
            {
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "draft": pr.get("draft", False),
                "base": pr["base"]["ref"],
                "head": pr["head"]["ref"],
                "created_at": pr["created_at"][:10],
                "url": pr["html_url"],
            }
            for pr in data
        ][:limit]

    @tool
    async def github_search_code(self, query: str, repo: str = "", limit: int = 10) -> list[dict]:
        """
        Search for code on GitHub.
        query: Search query (GitHub code search syntax).
        repo: Limit search to this repo ('owner/name'), optional.
        limit: Maximum number of results.
        """
        q = f"{query} repo:{repo}" if repo else query
        data = await self._gh_get(
            "/search/code",
            params={"q": q, "per_page": min(limit, 30)},
        )
        return [
            {
                "path": item["path"],
                "repo": item["repository"]["full_name"],
                "url": item["html_url"],
                "sha": item["sha"][:8],
            }
            for item in data.get("items", [])
        ][:limit]

    @tool
    async def github_get_file(self, repo: str, path: str, ref: str = "") -> str:
        """
        Get the contents of a file from a repository.
        repo: Repository in 'owner/name' format.
        path: File path within the repository.
        ref: Branch, tag, or commit SHA (default: default branch).
        """
        import base64

        params = {"ref": ref} if ref else {}
        data = await self._gh_get(f"/repos/{repo}/contents/{path}", params=params)
        if isinstance(data, list):
            return "\n".join(f["name"] for f in data)
        content = data.get("content", "")
        encoding = data.get("encoding", "")
        if encoding == "base64":
            decoded = base64.b64decode(content).decode("utf-8", errors="replace")
            if len(decoded) > 10000:
                decoded = decoded[:10000] + "\n\n[... truncated ...]"
            return decoded
        return content
