"""
Trigger decorators — make agents respond to external events instead of running once.

    @trigger("webhook", path="/webhook", secret="env:WEBHOOK_SECRET")
    async def run(self, payload: dict) -> None:
        ...

    @trigger("cron", interval="every 6h")
    async def run(self) -> None:
        ...
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("aios.triggers")

# Sentinel applied by @trigger
_TRIGGER_MARKER = "__aios_trigger__"


def trigger(kind: str, **kwargs: Any) -> Callable:
    """
    Decorate an agent's run() to make it event-driven rather than one-shot.

    Supported kinds:

    ``webhook``
        Start a lightweight HTTP server; call run(payload) on each POST.

        Options:
            path (str):    URL path. Default ``/webhook``.
            port (int):    Port to listen on. Default ``8080``.
            secret (str):  HMAC-SHA256 secret for signature verification.
                           Prefix with ``env:`` to read from an env var.
                           Example: ``secret="env:GITHUB_WEBHOOK_SECRET"``
            header (str):  Header name for the signature. Default ``X-Hub-Signature-256``.
            method (str):  HTTP method. Default ``POST``.

    ``cron``
        Alias for ``@schedule`` — repeat on an interval. Provided for symmetry.

        Options:
            interval (str): e.g. ``"every 6h"``, ``"every 30m"``

    Example::

        from aios import Agent, tool, trigger

        class GitHubReviewAgent(Agent):
            name = "gh_reviewer"
            model = "claude-sonnet-4-6"

            @trigger("webhook", path="/github", secret="env:GITHUB_WEBHOOK_SECRET")
            async def run(self, payload: dict) -> None:
                pr = payload.get("pull_request", {})
                if not pr:
                    return
                review = await self.think(f"Review this PR: {pr['title']}")
                await self.memory.save(f"review_{pr['number']}", review)
    """

    def decorator(fn: Callable) -> Callable:
        setattr(fn, _TRIGGER_MARKER, True)
        setattr(fn, "__aios_trigger_kind__", kind)
        setattr(fn, "__aios_trigger_opts__", kwargs)
        return fn

    return decorator


# ── Webhook server ────────────────────────────────────────────────────────────


class WebhookServer:
    """
    Minimal asyncio HTTP server that calls agent.run(payload) on each matching request.
    No external deps — pure asyncio + stdlib.
    """

    def __init__(
        self,
        agent: Any,
        path: str = "/webhook",
        port: int = 8080,
        secret: str = "",
        header: str = "X-Hub-Signature-256",
        method: str = "POST",
    ) -> None:
        self._agent = agent
        self._path = path
        self._port = port
        self._header = header.lower()
        self._method = method.upper()
        self._secret = self._resolve_secret(secret)

    def _resolve_secret(self, secret: str) -> str:
        if secret.startswith("env:"):
            return os.environ.get(secret[4:], "")
        return secret

    async def serve(self) -> None:
        server = await asyncio.start_server(self._handle, "0.0.0.0", self._port)
        logger.info(
            "[%s] webhook listening on http://0.0.0.0:%d%s",
            self._agent.name,
            self._port,
            self._path,
        )
        async with server:
            await server.serve_forever()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.read(65536)
            text = raw.decode("utf-8", errors="replace")
            lines = text.split("\r\n")
            if not lines:
                return

            request_line = lines[0]
            parts = request_line.split(" ")
            if len(parts) < 2:
                await self._respond(writer, 400, "Bad request")
                return

            method, path = parts[0], parts[1].split("?")[0]

            if method != self._method:
                await self._respond(writer, 405, "Method not allowed")
                return

            if path != self._path:
                await self._respond(writer, 404, "Not found")
                return

            # Parse headers
            headers: dict[str, str] = {}
            body_start = 0
            for i, line in enumerate(lines[1:], 1):
                if line == "":
                    body_start = i + 1
                    break
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            body = "\r\n".join(lines[body_start:]).strip()

            # Verify HMAC signature if secret configured
            if self._secret:
                sig = headers.get(self._header, "")
                if not self._verify_signature(body.encode(), sig):
                    logger.warning("[%s] webhook: invalid signature — request rejected", self._agent.name)
                    await self._respond(writer, 401, "Invalid signature")
                    return

            # Parse payload
            content_type = headers.get("content-type", "")
            try:
                if "json" in content_type:
                    payload = json.loads(body) if body else {}
                else:
                    payload = {"body": body, "content_type": content_type}
            except json.JSONDecodeError:
                payload = {"body": body}

            await self._respond(writer, 200, "OK")

            # Run the agent asynchronously (don't block the server)
            asyncio.create_task(self._dispatch(payload))

        except Exception as exc:
            logger.error("[%s] webhook handler error: %s", self._agent.name, exc)
        finally:
            writer.close()

    async def _dispatch(self, payload: dict) -> None:
        logger.info("[%s] webhook triggered — dispatching run()", self._agent.name)
        await self._agent.memory.log_event("webhook_triggered", {"keys": list(payload.keys())})
        try:
            import inspect

            result = self._agent.run(payload)
            if inspect.isawaitable(result):
                await result
            logger.info("[%s] webhook run() complete", self._agent.name)
        except Exception as exc:
            logger.error("[%s] webhook run() failed: %s", self._agent.name, exc)

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        expected = "sha256=" + hmac.new(self._secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, status: int, message: str) -> None:
        body = message.encode()
        response = (f"HTTP/1.1 {status} {message}\r\nContent-Length: {len(body)}\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n").encode() + body
        writer.write(response)
        await writer.drain()
