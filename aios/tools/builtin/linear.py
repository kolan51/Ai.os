from __future__ import annotations

import os
from typing import Any

from ..registry import tool


class LinearMixin:
    """
    Adds Linear issue tracker tools to an agent.

    Requires LINEAR_API_KEY in environment.
    Get one at: https://linear.app/settings/api → Personal API keys.

    Uses the Linear GraphQL API — no extra dependencies needed.

    Usage::

        from aios import Agent
        from aios.tools.builtin import LinearMixin

        class DevAgent(Agent, LinearMixin):
            name = "dev_agent"
            model = "claude-sonnet-4-6"

            async def run(self):
                issues = await self.linear_get_issues(team="ENG", state="In Progress")
                issue = await self.linear_create_issue(
                    team="ENG",
                    title="Fix login timeout",
                    description="Users are getting logged out after 5 minutes.",
                    priority=2,
                )
    """

    @property
    def _linear_key(self) -> str:
        key = os.environ.get("LINEAR_API_KEY", "")
        if not key:
            raise OSError("LINEAR_API_KEY not set. Create a Personal API key at https://linear.app/settings/api and set LINEAR_API_KEY=lin_api_...")
        return key

    async def _gql(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.linear.app/graphql",
                headers={"Authorization": self._linear_key, "Content-Type": "application/json"},
                json={"query": query, "variables": variables or {}},
            )
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                raise RuntimeError(f"Linear API error: {data['errors'][0]['message']}")
            return data.get("data", {})

    @tool
    async def linear_get_issues(
        self,
        team: str = "",
        state: str = "",
        assignee_me: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """
        List issues from Linear with optional filters.
        team: Filter by team key (e.g. 'ENG'). Empty = all teams.
        state: Filter by state name (e.g. 'In Progress', 'Todo', 'Done').
        assignee_me: If true, only return issues assigned to the API key owner.
        limit: Maximum number of issues to return.
        """
        filters: list[str] = []
        if team:
            filters.append(f'team: {{key: {{eq: "{team}"}}}}')
        if state:
            filters.append(f'state: {{name: {{eq: "{state}"}}}}')
        if assignee_me:
            filters.append("assignee: {isMe: {eq: true}}")

        filter_str = "{" + ", ".join(filters) + "}" if filters else ""
        filter_clause = f"filter: {filter_str}," if filter_str else ""

        query = f"""
        query {{
          issues({filter_clause} first: {min(limit, 100)}, orderBy: updatedAt) {{
            nodes {{
              id identifier title priority
              state {{ name }}
              assignee {{ name email }}
              team {{ key name }}
              createdAt updatedAt url
              description
            }}
          }}
        }}
        """
        data = await self._gql(query)
        return [
            {
                "id": n["id"],
                "identifier": n["identifier"],
                "title": n["title"],
                "state": n["state"]["name"],
                "priority": n["priority"],
                "team": n["team"]["key"],
                "assignee": (n.get("assignee") or {}).get("name", ""),
                "url": n["url"],
                "updated": n["updatedAt"][:10],
                "description": (n.get("description") or "")[:300],
            }
            for n in data.get("issues", {}).get("nodes", [])
        ][:limit]

    @tool
    async def linear_get_issue(self, issue_id: str) -> dict:
        """
        Get full details of a single Linear issue.
        issue_id: Issue ID or identifier (e.g. 'ENG-123').
        """
        # Accept both UUID and identifier like 'ENG-123'
        if "-" in issue_id and not issue_id.startswith("lin_"):
            query = f"""
            query {{
              issue(id: "{issue_id}") {{
                id identifier title description priority
                state {{ name }} team {{ key }}
                assignee {{ name }} createdAt updatedAt url
                comments {{ nodes {{ body createdAt user {{ name }} }} }}
              }}
            }}
            """
        else:
            query = f"""
            query {{
              issue(id: "{issue_id}") {{
                id identifier title description priority
                state {{ name }} team {{ key }}
                assignee {{ name }} createdAt updatedAt url
                comments {{ nodes {{ body createdAt user {{ name }} }} }}
              }}
            }}
            """
        data = await self._gql(query)
        n = data.get("issue", {})
        comments = [{"author": c["user"]["name"], "body": c["body"], "date": c["createdAt"][:10]} for c in n.get("comments", {}).get("nodes", [])]
        return {
            "id": n.get("id", ""),
            "identifier": n.get("identifier", ""),
            "title": n.get("title", ""),
            "description": n.get("description", ""),
            "state": (n.get("state") or {}).get("name", ""),
            "priority": n.get("priority", 0),
            "team": (n.get("team") or {}).get("key", ""),
            "assignee": (n.get("assignee") or {}).get("name", ""),
            "url": n.get("url", ""),
            "created": n.get("createdAt", "")[:10],
            "updated": n.get("updatedAt", "")[:10],
            "comments": comments,
        }

    @tool
    async def linear_create_issue(
        self,
        team: str,
        title: str,
        description: str = "",
        priority: int = 0,
        state: str = "",
        label: str = "",
    ) -> dict:
        """
        Create a new Linear issue.
        team: Team key (e.g. 'ENG').
        title: Issue title.
        description: Optional markdown description.
        priority: 0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low.
        state: Optional state name (e.g. 'Todo', 'In Progress').
        label: Optional label name to apply.
        """
        # Resolve team ID
        team_data = await self._gql(f'query {{ team(key: "{team}") {{ id }} }}')
        team_id = team_data.get("team", {}).get("id", "")
        if not team_id:
            raise ValueError(f"Team {team!r} not found")

        variables: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "priority": priority,
        }
        if description:
            variables["description"] = description

        # Resolve state ID if given
        if state:
            state_data = await self._gql(f'query {{ workflowStates(filter: {{team: {{key: {{eq: "{team}"}}}}, name: {{eq: "{state}"}}}}) {{ nodes {{ id }} }} }}')
            nodes = state_data.get("workflowStates", {}).get("nodes", [])
            if nodes:
                variables["stateId"] = nodes[0]["id"]

        mutation = """
        mutation CreateIssue($teamId: String!, $title: String!, $description: String,
                             $priority: Int, $stateId: String) {
          issueCreate(input: {
            teamId: $teamId, title: $title,
            description: $description, priority: $priority,
            stateId: $stateId
          }) {
            issue { id identifier title url }
          }
        }
        """
        result = await self._gql(mutation, variables)
        issue = result.get("issueCreate", {}).get("issue", {})
        return {
            "id": issue.get("id", ""),
            "identifier": issue.get("identifier", ""),
            "title": issue.get("title", ""),
            "url": issue.get("url", ""),
        }

    @tool
    async def linear_update_issue(
        self,
        issue_id: str,
        state: str = "",
        priority: int = -1,
        title: str = "",
        description: str = "",
    ) -> str:
        """
        Update a Linear issue's state, priority, title, or description.
        issue_id: Issue ID (UUID).
        state: New state name (e.g. 'Done', 'In Progress'). Empty = no change.
        priority: New priority 0-4. -1 = no change.
        title: New title. Empty = no change.
        description: New description. Empty = no change.
        """
        updates: dict[str, Any] = {}
        if title:
            updates["title"] = title
        if description:
            updates["description"] = description
        if priority >= 0:
            updates["priority"] = priority

        # Resolve state to ID
        if state:
            state_data = await self._gql(f'query {{ workflowStates(filter: {{name: {{eq: "{state}"}}}}) {{ nodes {{ id }} }} }}')
            nodes = state_data.get("workflowStates", {}).get("nodes", [])
            if nodes:
                updates["stateId"] = nodes[0]["id"]

        if not updates:
            return "No changes specified"

        mutation = "mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }"
        result = await self._gql(mutation, {"id": issue_id, "input": updates})
        success = result.get("issueUpdate", {}).get("success", False)
        return f"Issue {issue_id} updated" if success else "Update failed"

    @tool
    async def linear_add_comment(self, issue_id: str, body: str) -> str:
        """
        Add a comment to a Linear issue.
        issue_id: Issue ID (UUID).
        body: Comment text (markdown supported).
        """
        mutation = """
        mutation AddComment($issueId: String!, $body: String!) {
          commentCreate(input: {issueId: $issueId, body: $body}) {
            comment { id createdAt }
          }
        }
        """
        await self._gql(mutation, {"issueId": issue_id, "body": body})
        return f"Comment added to {issue_id}"

    @tool
    async def linear_list_teams(self) -> list[dict]:
        """List all teams in the Linear workspace."""
        data = await self._gql("query { teams { nodes { id key name description } } }")
        return [{"id": t["id"], "key": t["key"], "name": t["name"], "description": t.get("description", "")} for t in data.get("teams", {}).get("nodes", [])]
