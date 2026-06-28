from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

# Sentinel applied by the @tool decorator
_TOOL_MARKER = "__aios_tool__"

_PY_TO_JSON: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _build_schema(fn: Callable) -> dict:
    hints = get_type_hints(fn)
    sig = inspect.signature(fn)
    properties: dict[str, dict] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name == "self":
            continue
        hint = hints.get(name, str)
        json_type = _PY_TO_JSON.get(hint, "string")
        prop: dict[str, Any] = {"type": json_type}

        doc = inspect.getdoc(fn) or ""
        # Simple per-param doc extraction: "param_name: description" lines
        for line in doc.splitlines():
            if line.strip().startswith(f"{name}:"):
                prop["description"] = line.split(":", 1)[1].strip()
                break

        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def tool(fn: Callable | None = None, *, retries: int = 0, backoff: float = 1.0, cache_ttl: float = 0.0) -> Any:
    """Mark a method as an agent tool.

    Can be used as a plain decorator or with keyword arguments::

        @tool
        async def my_tool(self, x: str) -> str: ...

        @tool(retries=3, backoff=2.0)
        async def my_tool(self, x: str) -> str: ...

        @tool(cache_ttl=60)
        async def fetch_data(self, url: str) -> str: ...

    retries:   How many times to retry on exception (0 = no retries).
    backoff:   Seconds to wait between retries (doubles each attempt).
    cache_ttl: Cache results for this many seconds (0 = no cache).
               Same args → same result returned instantly within the TTL.
    """
    def decorator(f: Callable) -> Callable:
        schema = _build_schema(f)  # build from original signature before wrapping
        if cache_ttl > 0:
            f = _with_cache(f, cache_ttl)
        if retries > 0:
            f = _with_retries(f, retries, backoff)
        setattr(f, _TOOL_MARKER, True)
        setattr(f, "__aios_schema__", schema)
        setattr(f, "__aios_retries__", retries)
        setattr(f, "__aios_cache_ttl__", cache_ttl)
        return f

    if fn is not None:
        # Called as @tool (no parentheses)
        return decorator(fn)
    # Called as @tool(...) — return decorator
    return decorator


def _with_cache(fn: Callable, ttl: float) -> Callable:
    import time

    _cache: dict[str, tuple[float, Any]] = {}  # key → (expires_at, result)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        cache_key = json.dumps({"a": args[1:], "k": kwargs}, sort_keys=True, default=str)
        now = time.monotonic()
        if cache_key in _cache:
            expires, cached_result = _cache[cache_key]
            if now < expires:
                return cached_result
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        _cache[cache_key] = (now + ttl, result)
        return result

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _with_retries(fn: Callable, retries: int, backoff: float) -> Callable:
    import asyncio
    import logging

    logger = logging.getLogger("aios")

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        delay = backoff
        for attempt in range(retries + 1):
            try:
                result = fn(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    logger.warning(
                        "tool %s attempt %d/%d failed: %s — retrying in %.1fs",
                        fn.__name__, attempt + 1, retries + 1, exc, delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
        raise last_exc  # type: ignore[misc]

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@dataclass
class ToolDefinition:
    name: str
    description: str
    schema: dict
    fn: Callable

    def to_llm_format(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register_from_agent(self, agent_instance: Any) -> None:
        for attr_name in dir(type(agent_instance)):
            method = getattr(type(agent_instance), attr_name, None)
            if method is None or not getattr(method, _TOOL_MARKER, False):
                continue
            bound = getattr(agent_instance, attr_name)
            self._tools[attr_name] = ToolDefinition(
                name=attr_name,
                description=(inspect.getdoc(method) or attr_name).split("\n")[0],
                schema=getattr(method, "__aios_schema__", {}),
                fn=bound,
            )

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_llm_format(self) -> list[dict]:
        return [t.to_llm_format() for t in self._tools.values()]

    async def call(self, name: str, arguments: str | dict) -> Any:
        defn = self._tools.get(name)
        if defn is None:
            raise ValueError(f"Unknown tool: {name!r}")
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        result = defn.fn(**args)
        if inspect.isawaitable(result):
            result = await result
        return result
