from __future__ import annotations

import os
from typing import Any

from ..registry import tool


class SlackMixin:
    """
    Adds Slack tools to an agent.

    Requires SLACK_BOT_TOKEN in environment.
    Uses the Slack Web API via httpx — no slack-sdk dependency needed.

    Usage::

        from aios import Agent
        from aios.tools.builtin import SlackMixin

        class NotifyAgent(Agent, SlackMixin):
            name = "notifier"
            model = "claude-haiku-4-5-20251001"

            async def run(self):
                await self.slack_send_message("#alerts", "Deployment complete!")
    """

    @property
    def _slack_token(self) -> str:
        token = os.environ.get("SLACK_BOT_TOKEN", "")
        if not token:
            raise OSError(
                "SLACK_BOT_TOKEN not set. "
                "Create a Slack app at https://api.slack.com/apps, "
                "add OAuth scopes (chat:write, channels:history), "
                "install to workspace, and set SLACK_BOT_TOKEN=xoxb-..."
            )
        return token

    async def _slack_post(self, method: str, payload: dict) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://slack.com/api/{method}",
                headers={"Authorization": f"Bearer {self._slack_token}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
            return data

    async def _slack_get(self, method: str, params: dict | None = None) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://slack.com/api/{method}",
                headers={"Authorization": f"Bearer {self._slack_token}"},
                params=params or {},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
            return data

    @tool
    async def slack_send_message(self, channel: str, text: str) -> str:
        """
        Send a message to a Slack channel or DM.
        channel: Channel name (e.g. '#alerts') or channel ID or user ID for DMs.
        text: Message text. Supports mrkdwn formatting (*bold*, _italic_, `code`).
        """
        data = await self._slack_post("chat.postMessage", {"channel": channel, "text": text})
        ts = data.get("ts", "")
        return f"Message sent to {channel} (ts={ts})"

    @tool
    async def slack_send_blocks(self, channel: str, text: str, blocks: list) -> str:
        """
        Send a rich Slack message using Block Kit layout blocks.
        channel: Channel name or ID.
        text: Fallback text shown in notifications.
        blocks: List of Block Kit block objects (dicts).
        """
        data = await self._slack_post(
            "chat.postMessage",
            {"channel": channel, "text": text, "blocks": blocks},
        )
        ts = data.get("ts", "")
        return f"Rich message sent to {channel} (ts={ts})"

    @tool
    async def slack_get_messages(self, channel: str, limit: int = 20) -> list[dict]:
        """
        Fetch recent messages from a Slack channel.
        channel: Channel ID (not name — use slack_list_channels to get IDs).
        limit: Number of messages to retrieve (max 100).
        """
        data = await self._slack_get(
            "conversations.history",
            {"channel": channel, "limit": min(limit, 100)},
        )
        messages = []
        for msg in data.get("messages", []):
            if msg.get("type") != "message" or msg.get("subtype"):
                continue
            messages.append(
                {
                    "ts": msg.get("ts", ""),
                    "user": msg.get("user", msg.get("bot_id", "unknown")),
                    "text": msg.get("text", ""),
                    "thread_ts": msg.get("thread_ts"),
                }
            )
        return messages

    @tool
    async def slack_list_channels(self, limit: int = 50) -> list[dict]:
        """
        List public channels in the workspace.
        limit: Maximum number of channels to return.
        """
        data = await self._slack_get(
            "conversations.list",
            {"types": "public_channel", "limit": min(limit, 200), "exclude_archived": "true"},
        )
        return [
            {
                "id": ch["id"],
                "name": ch["name"],
                "members": ch.get("num_members", 0),
                "topic": ch.get("topic", {}).get("value", ""),
            }
            for ch in data.get("channels", [])
        ]

    @tool
    async def slack_reply_to_thread(self, channel: str, thread_ts: str, text: str) -> str:
        """
        Reply to an existing message thread.
        channel: Channel ID.
        thread_ts: Timestamp of the parent message (from slack_get_messages).
        text: Reply text.
        """
        data = await self._slack_post(
            "chat.postMessage",
            {"channel": channel, "thread_ts": thread_ts, "text": text},
        )
        return f"Thread reply sent (ts={data.get('ts', '')})"

    @tool
    async def slack_add_reaction(self, channel: str, timestamp: str, emoji: str) -> str:
        """
        Add an emoji reaction to a message.
        channel: Channel ID.
        timestamp: Message timestamp (ts field).
        emoji: Emoji name without colons (e.g. 'white_check_mark', 'rocket').
        """
        await self._slack_post(
            "reactions.add",
            {"channel": channel, "timestamp": timestamp, "name": emoji},
        )
        return f"Reaction :{emoji}: added"
