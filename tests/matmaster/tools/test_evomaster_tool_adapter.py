"""Tests for EvoToolAdapter -- EvoMaster BaseTool to matmaster Tool adapter."""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import MagicMock

from pydantic import Field

from evomaster.agent.tools.base import BaseTool, BaseToolParams
from matmaster.tools.tool_result import ToolResult

# ---------- Fakes for EvoMaster side ----------


class _FakeParams(BaseToolParams):
    """Fake tool description for testing."""

    name: ClassVar[str] = "fake_tool"

    query: str = Field(description="A search query")


class _FakeTool(BaseTool):
    """Fake EvoMaster tool for adapter tests."""

    name: ClassVar[str] = "fake_tool"
    params_class: ClassVar[type[BaseToolParams]] = _FakeParams

    def __init__(self, *, observation: str | dict | list = "ok"):
        super().__init__()
        self._observation = observation
        self.last_session: Any = None
        self.last_args_json: str | None = None

    def execute(self, session: Any, args_json: str) -> tuple[Any, dict[str, Any]]:
        self.last_session = session
        self.last_args_json = args_json
        return self._observation, {"info": "test"}


# ---------- Tests ----------


class TestEvoToolAdapter:
    def test_adapter_exposes_tool_name(self) -> None:
        """Adapter.name returns the wrapped EvoMaster tool's name."""
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool()
        session = MagicMock()
        adapter = EvoToolAdapter(tool, session)
        assert adapter.name == "fake_tool"

    def test_adapter_exposes_description(self) -> None:
        """Adapter.description returns docstring of the params class."""
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool()
        adapter = EvoToolAdapter(tool, MagicMock())
        assert adapter.description == "Fake tool description for testing."

    def test_adapter_exposes_json_schema(self) -> None:
        """Adapter.json_schema returns model_json_schema() from params_class."""
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool()
        adapter = EvoToolAdapter(tool, MagicMock())
        schema = adapter.json_schema
        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "query" in schema["properties"]

    async def test_adapter_returns_raw_string_observation(self) -> None:
        """When wrapped tool returns str observation, adapter returns it unchanged."""
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool(observation="hello world")
        adapter = EvoToolAdapter(tool, MagicMock())
        result = await adapter.execute({"query": "test"})
        assert isinstance(result, ToolResult)
        assert result.content == "hello world"
        assert result.status == "success"
        assert result.info == {"info": "test"}

    async def test_adapter_json_serializes_structured_observation(self) -> None:
        """When wrapped tool returns dict observation, adapter JSON-serializes it."""
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool(observation={"key": "value", "num": 42})
        adapter = EvoToolAdapter(tool, MagicMock())
        result = await adapter.execute({"query": "test"})
        parsed = json.loads(result.content)
        assert parsed == {"key": "value", "num": 42}

    async def test_adapter_json_serializes_list_observation(self) -> None:
        """When wrapped tool returns list observation, adapter JSON-serializes it."""
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool(observation=[1, 2, 3])
        adapter = EvoToolAdapter(tool, MagicMock())
        result = await adapter.execute({"query": "test"})
        parsed = json.loads(result.content)
        assert parsed == [1, 2, 3]

    async def test_adapter_passes_json_args_exactly_once(self) -> None:
        """Adapter serializes arguments to JSON and passes to wrapped tool exactly once."""
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool()
        session = MagicMock()
        adapter = EvoToolAdapter(tool, session)
        await adapter.execute({"query": "hello"})

        assert tool.last_session is session
        assert tool.last_args_json is not None
        parsed = json.loads(tool.last_args_json)
        assert parsed == {"query": "hello"}

    def test_adapter_satisfies_tool_protocol(self) -> None:
        """EvoToolAdapter satisfies the matmaster Tool Protocol (isinstance check)."""
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter
        from matmaster.tools.tool_registry import Tool

        tool = _FakeTool()
        adapter = EvoToolAdapter(tool, MagicMock())
        assert isinstance(adapter, Tool)

    async def test_adapter_sets_error_status_from_info(self) -> None:
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool(observation="err msg")
        tool.execute = lambda session, args_json: ("err msg", {"error": "bad input"})
        adapter = EvoToolAdapter(tool, MagicMock())

        result = await adapter.execute({"query": "test"})
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert result.content == "err msg"
        assert result.info == {"error": "bad input"}

    async def test_adapter_preserves_non_error_info(self) -> None:
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool(observation="ok")
        tool.execute = lambda session, args_json: (
            "ok",
            {"saved_path": "/tmp/out.txt"},
        )
        adapter = EvoToolAdapter(tool, MagicMock())

        result = await adapter.execute({"query": "test"})
        assert result.status == "success"
        assert result.info == {"saved_path": "/tmp/out.txt"}

    async def test_adapter_error_prefixed_observation_is_error_without_info_key(
        self,
    ) -> None:
        from matmaster.tools.evomaster_tool_adapter import EvoToolAdapter

        tool = _FakeTool(observation="Error: remote failure")
        tool.execute = lambda session, args_json: ("Error: remote failure", {})
        adapter = EvoToolAdapter(tool, MagicMock())

        result = await adapter.execute({"query": "test"})
        assert result.status == "error"
        assert result.content == "Error: remote failure"
