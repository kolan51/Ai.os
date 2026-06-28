from __future__ import annotations

import os
from typing import Any

from ..registry import tool


class NotionMixin:
    """
    Adds Notion tools to an agent via the Notion API.

    Requires NOTION_TOKEN in environment (Internal Integration Token).
    Create one at: https://www.notion.so/my-integrations

    The integration must be shared with the pages/databases it accesses:
    open the page → Share → invite the integration by name.

    Usage::

        from aios import Agent
        from aios.tools.builtin import NotionMixin

        class KBAgent(Agent, NotionMixin):
            name = "knowledge_base"
            model = "claude-sonnet-4-6"

            async def run(self):
                pages = await self.notion_search("project roadmap")
                content = await self.notion_get_page(pages[0]["id"])
                await self.notion_append_block(pages[0]["id"], "✓ Reviewed by agent")
    """

    @property
    def _notion_headers(self) -> dict[str, str]:
        token = os.environ.get("NOTION_TOKEN", "")
        if not token:
            raise OSError(
                "NOTION_TOKEN not set. "
                "Create an Internal Integration at https://www.notion.so/my-integrations "
                "and set NOTION_TOKEN=secret_..."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    async def _notion_get(self, path: str, params: dict | None = None) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"https://api.notion.com/v1{path}",
                headers=self._notion_headers,
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()

    async def _notion_post(self, path: str, body: dict) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"https://api.notion.com/v1{path}",
                headers=self._notion_headers,
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    async def _notion_patch(self, path: str, body: dict) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.patch(
                f"https://api.notion.com/v1{path}",
                headers=self._notion_headers,
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    def _extract_text(self, rich_text: list) -> str:
        return "".join(t.get("plain_text", "") for t in rich_text)

    def _extract_page_title(self, page: dict) -> str:
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                return self._extract_text(prop.get("title", []))
        return page.get("id", "untitled")

    def _block_to_text(self, block: dict) -> str:
        btype = block.get("type", "")
        content = block.get(btype, {})
        rich_text = content.get("rich_text", [])
        text = self._extract_text(rich_text)
        prefix_map = {
            "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
            "bulleted_list_item": "• ", "numbered_list_item": "1. ",
            "to_do": "☐ " if not content.get("checked") else "☑ ",
            "quote": "> ", "code": "```\n",
        }
        prefix = prefix_map.get(btype, "")
        suffix = "\n```" if btype == "code" else ""
        return f"{prefix}{text}{suffix}" if text or btype == "divider" else ""

    @tool
    async def notion_search(self, query: str, limit: int = 10) -> list[dict]:
        """
        Search across all pages and databases the integration can access.
        query: Search query string.
        limit: Maximum number of results to return.
        """
        data = await self._notion_post("/search", {"query": query, "page_size": min(limit, 100)})
        results = []
        for item in data.get("results", []):
            obj_type = item.get("object", "")
            results.append(
                {
                    "id": item["id"],
                    "type": obj_type,
                    "title": self._extract_page_title(item),
                    "url": item.get("url", ""),
                    "last_edited": item.get("last_edited_time", "")[:10],
                }
            )
        return results[:limit]

    @tool
    async def notion_get_page(self, page_id: str) -> dict:
        """
        Get a page's properties and metadata.
        page_id: Notion page ID (from search results or page URL).
        """
        data = await self._notion_get(f"/pages/{page_id}")
        return {
            "id": data["id"],
            "title": self._extract_page_title(data),
            "url": data.get("url", ""),
            "created": data.get("created_time", "")[:10],
            "last_edited": data.get("last_edited_time", "")[:10],
            "properties": {
                k: self._extract_text(v.get("title") or v.get("rich_text") or [])
                for k, v in data.get("properties", {}).items()
                if v.get("type") in ("title", "rich_text")
            },
        }

    @tool
    async def notion_get_page_content(self, page_id: str) -> str:
        """
        Get the text content of a Notion page as markdown-like plain text.
        page_id: Notion page ID.
        """
        data = await self._notion_get(f"/blocks/{page_id}/children", {"page_size": 100})
        lines = []
        for block in data.get("results", []):
            text = self._block_to_text(block)
            if text:
                lines.append(text)
        return "\n".join(lines) or "(empty page)"

    @tool
    async def notion_append_block(self, page_id: str, text: str, block_type: str = "paragraph") -> str:
        """
        Append a text block to a Notion page.
        page_id: Notion page ID to append to.
        text: Text content for the new block.
        block_type: Block type — 'paragraph', 'heading_1', 'heading_2', 'heading_3',
                    'bulleted_list_item', 'numbered_list_item', 'to_do', 'quote', 'code'.
        """
        valid_types = {"paragraph", "heading_1", "heading_2", "heading_3",
                       "bulleted_list_item", "numbered_list_item", "to_do", "quote", "code"}
        if block_type not in valid_types:
            block_type = "paragraph"

        block: dict[str, Any] = {
            "object": "block",
            "type": block_type,
            block_type: {
                "rich_text": [{"type": "text", "text": {"content": text}}]
            },
        }
        await self._notion_patch(f"/blocks/{page_id}/children", {"children": [block]})
        return f"Block appended to page {page_id}"

    @tool
    async def notion_create_page(
        self, parent_id: str, title: str, content: str = "", is_database: bool = False
    ) -> dict:
        """
        Create a new Notion page.
        parent_id: ID of the parent page or database to create inside.
        title: Page title.
        content: Optional initial paragraph content.
        is_database: Set to true if parent_id is a database (not a page).
        """
        parent = (
            {"database_id": parent_id} if is_database else {"page_id": parent_id}
        )
        body: dict[str, Any] = {
            "parent": parent,
            "properties": {
                "title": {"title": [{"type": "text", "text": {"content": title}}]}
            },
        }
        if content:
            body["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": content}}]
                    },
                }
            ]
        data = await self._notion_post("/pages", body)
        return {
            "id": data["id"],
            "url": data.get("url", ""),
            "title": title,
        }

    @tool
    async def notion_query_database(self, database_id: str, filter_json: str = "", limit: int = 20) -> list[dict]:
        """
        Query a Notion database and return matching rows.
        database_id: ID of the database to query.
        filter_json: Optional Notion filter object as a JSON string (see Notion API docs).
        limit: Maximum number of rows to return.
        """
        import json

        body: dict[str, Any] = {"page_size": min(limit, 100)}
        if filter_json:
            try:
                body["filter"] = json.loads(filter_json)
            except json.JSONDecodeError:
                pass

        data = await self._notion_post(f"/databases/{database_id}/query", body)
        results = []
        for page in data.get("results", []):
            row: dict[str, Any] = {"id": page["id"], "url": page.get("url", "")}
            for key, prop in page.get("properties", {}).items():
                ptype = prop.get("type")
                if ptype == "title":
                    row[key] = self._extract_text(prop.get("title", []))
                elif ptype == "rich_text":
                    row[key] = self._extract_text(prop.get("rich_text", []))
                elif ptype in ("number", "checkbox", "select", "status"):
                    row[key] = prop.get(ptype)
                elif ptype == "date":
                    row[key] = (prop.get("date") or {}).get("start", "")
                elif ptype == "multi_select":
                    row[key] = [s["name"] for s in prop.get("multi_select", [])]
            results.append(row)
        return results[:limit]
