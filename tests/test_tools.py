import pytest
from aios.tools.registry import tool, ToolRegistry


class FakeAgent:
    @tool
    async def fetch_data(self, url: str, timeout: int = 10) -> str:
        """
        Fetch data from a URL.
        url: The URL to fetch.
        timeout: Request timeout in seconds.
        """
        return f"data from {url}"

    @tool
    def compute(self, value: float, multiplier: float) -> float:
        """Multiply value by multiplier."""
        return value * multiplier

    def not_a_tool(self) -> str:
        return "ignored"


@pytest.fixture
def registry() -> ToolRegistry:
    agent = FakeAgent()
    reg = ToolRegistry()
    reg.register_from_agent(agent)
    return reg


def test_tool_marker_set() -> None:
    from aios.tools.registry import _TOOL_MARKER
    assert getattr(FakeAgent.fetch_data, _TOOL_MARKER, False) is True
    assert getattr(FakeAgent.not_a_tool, _TOOL_MARKER, False) is False


def test_registry_finds_tools(registry: ToolRegistry) -> None:
    names = {t.name for t in registry.all()}
    assert "fetch_data" in names
    assert "compute" in names
    assert "not_a_tool" not in names


def test_schema_infers_types(registry: ToolRegistry) -> None:
    defn = registry.get("fetch_data")
    assert defn is not None
    props = defn.schema["properties"]
    assert props["url"]["type"] == "string"
    assert props["timeout"]["type"] == "integer"
    assert "url" in defn.schema["required"]
    assert "timeout" not in defn.schema["required"]  # has default


def test_llm_format(registry: ToolRegistry) -> None:
    fmt = registry.to_llm_format()
    names = [f["function"]["name"] for f in fmt]
    assert "fetch_data" in names
    assert "compute" in names


async def test_call_async_tool(registry: ToolRegistry) -> None:
    result = await registry.call("fetch_data", {"url": "https://example.com"})
    assert result == "data from https://example.com"


async def test_call_sync_tool(registry: ToolRegistry) -> None:
    result = await registry.call("compute", {"value": 3.0, "multiplier": 4.0})
    assert result == 12.0


async def test_call_unknown_tool(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError, match="Unknown tool"):
        await registry.call("nonexistent", {})


async def test_call_with_json_string_args(registry: ToolRegistry) -> None:
    import json
    result = await registry.call("fetch_data", json.dumps({"url": "https://test.io"}))
    assert result == "data from https://test.io"
