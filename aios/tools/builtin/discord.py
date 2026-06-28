from __future__ import annotations

import os
from typing import Any

from ..registry import tool


class DiscordMixin:
    """
    Adds Discord webhook and bot tools to an agent.

    Supports two modes:
    - **Webhook** (simplest): set DISCORD_WEBHOOK_URL — post messages with no bot setup.
    - **Bot** (full API): set DISCORD_BOT_TOKEN — send messages, read channels, manage threads.

    Webhook usage (recommended for notifications)::

        DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

    Bot usage (full API access)::

        DISCORD_BOT_TOKEN=Bot your-token-here

    Usage::

        from aios import Agent
        from aios.tools.builtin import DiscordMixin

        class AlertAgent(Agent, DiscordMixin):
            name = "alerter"
            model = "claude-haiku-4-5-20251001"

            async def run(self):
                await self.discord_send("Build failed on main!", username="CI Bot")
    """

    @property
    def _discord_webhook(self) -> str:
        url = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not url:
            raise OSError(
                "DISCORD_WEBHOOK_URL not set. "
                "Create a webhook: Server Settings → Integrations → Webhooks → New Webhook. "
                "Set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/..."
            )
        return url

    @property
    def _discord_headers(self) -> dict[str, str]:
        token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            raise OSError(
                "DISCORD_BOT_TOKEN not set. "
                "Create a bot at https://discord.com/developers/applications, "
                "add it to your server, and set DISCORD_BOT_TOKEN=Bot your-token."
            )
        if not token.startswith("Bot "):
            token = f"Bot {token}"
        return {"Authorization": token, "Content-Type": "application/json"}

    async def _dapi(self, method: str, path: str, payload: dict | None = None) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            fn = getattr(client, method.lower())
            kwargs: dict[str, Any] = {"headers": self._discord_headers}
            if payload is not None:
                kwargs["json"] = payload
            resp = await fn(f"https://discord.com/api/v10{path}", **kwargs)
            resp.raise_for_status()
            if resp.status_code == 204:
                return {}
            return resp.json()

    @tool
    async def discord_send(self, content: str, username: str = "", avatar_url: str = "") -> str:
        """
        Send a message to a Discord channel via webhook.
        content: Message text (supports Discord markdown).
        username: Override the webhook display name (optional).
        avatar_url: Override the webhook avatar image URL (optional).
        """
        import httpx

        payload: dict[str, Any] = {"content": content}
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self._discord_webhook, json=payload)
            resp.raise_for_status()

        return "Message sent to Discord webhook"

    @tool
    async def discord_send_embed(
        self,
        title: str,
        description: str,
        color: int = 0x5B8DF6,
        fields: list | None = None,
        url: str = "",
    ) -> str:
        """
        Send a rich embed message to Discord via webhook.
        title: Embed title.
        description: Embed body text (markdown supported).
        color: Embed color as a hex integer (default: blue).
        fields: Optional list of {'name': str, 'value': str, 'inline': bool} dicts.
        url: Optional URL to make the title a hyperlink.
        """
        import httpx

        embed: dict[str, Any] = {"title": title, "description": description, "color": color}
        if fields:
            embed["fields"] = fields
        if url:
            embed["url"] = url

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self._discord_webhook, json={"embeds": [embed]})
            resp.raise_for_status()

        return f"Embed '{title}' sent to Discord"

    @tool
    async def discord_send_message(self, channel_id: str, content: str) -> dict:
        """
        Send a message to a specific Discord channel (requires bot token).
        channel_id: Discord channel ID (enable Developer Mode to copy IDs).
        content: Message text.
        """
        data = await self._dapi("POST", f"/channels/{channel_id}/messages", {"content": content})
        return {"id": data.get("id", ""), "channel_id": channel_id}

    @tool
    async def discord_get_messages(self, channel_id: str, limit: int = 20) -> list[dict]:
        """
        Fetch recent messages from a Discord channel (requires bot token).
        channel_id: Discord channel ID.
        limit: Number of messages to fetch (max 100).
        """
        data = await self._dapi("GET", f"/channels/{channel_id}/messages?limit={min(limit, 100)}")
        return [
            {
                "id": m["id"],
                "author": m["author"].get("username", ""),
                "content": m.get("content", ""),
                "timestamp": m.get("timestamp", "")[:19],
            }
            for m in (data if isinstance(data, list) else [])
        ]

    @tool
    async def discord_create_thread(self, channel_id: str, message_id: str, name: str) -> dict:
        """
        Create a thread on an existing message (requires bot token).
        channel_id: Channel containing the message.
        message_id: ID of the message to thread from.
        name: Thread name (shown in the channel sidebar).
        """
        data = await self._dapi(
            "POST",
            f"/channels/{channel_id}/messages/{message_id}/threads",
            {"name": name},
        )
        return {"thread_id": data.get("id", ""), "name": data.get("name", "")}
