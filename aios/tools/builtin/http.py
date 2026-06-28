from __future__ import annotations

import json as _json
from typing import Any

from ..registry import tool


class HttpMixin:
    """
    Adds generic HTTP tools to an agent.
    GET, POST, PUT, PATCH, DELETE — all return structured response dicts.
    """

    http_timeout: int = 30
    http_max_response: int = 16_000

    @tool
    async def http_get(self, url: str, headers: dict | None = None, params: dict | None = None) -> dict:
        """
        Send an HTTP GET request.
        url: The URL to request.
        headers: Optional HTTP headers as a dict.
        params: Optional query parameters as a dict.
        """
        return await self._request("GET", url, headers=headers, params=params)

    @tool
    async def http_post(self, url: str, body: dict, headers: dict | None = None) -> dict:
        """
        Send an HTTP POST request with a JSON body.
        url: The URL to request.
        body: Request body as a dict (sent as JSON).
        headers: Optional HTTP headers as a dict.
        """
        return await self._request("POST", url, json=body, headers=headers)

    @tool
    async def http_put(self, url: str, body: dict, headers: dict | None = None) -> dict:
        """
        Send an HTTP PUT request with a JSON body.
        url: The URL to request.
        body: Request body as a dict (sent as JSON).
        headers: Optional HTTP headers as a dict.
        """
        return await self._request("PUT", url, json=body, headers=headers)

    @tool
    async def http_delete(self, url: str, headers: dict | None = None) -> dict:
        """
        Send an HTTP DELETE request.
        url: The URL to request.
        headers: Optional HTTP headers as a dict.
        """
        return await self._request("DELETE", url, headers=headers)

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict:
        import httpx

        default_headers = {"User-Agent": "aios-agent/0.1"}
        if "headers" in kwargs and kwargs["headers"]:
            kwargs["headers"] = {**default_headers, **kwargs["headers"]}
        else:
            kwargs["headers"] = default_headers

        try:
            async with httpx.AsyncClient(timeout=self.http_timeout, follow_redirects=True) as client:
                resp = await client.request(method, url, **kwargs)

            body_text = resp.text[: self.http_max_response]
            try:
                body_json = resp.json()
            except Exception:
                body_json = None

            return {
                "status": resp.status_code,
                "ok": resp.is_success,
                "headers": dict(resp.headers),
                "body": body_json if body_json is not None else body_text,
            }
        except Exception as exc:
            return {
                "status": 0,
                "ok": False,
                "headers": {},
                "body": f"Request failed: {exc}",
            }
