from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, get_type_hints

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


def tool(fn: Callable) -> Callable:
    """Mark a method as an agent tool. Schema is inferred from type hints."""
    setattr(fn, _TOOL_MARKER, True)
    setattr(fn, "__aios_schema__", _build_schema(fn))
    return fn


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
