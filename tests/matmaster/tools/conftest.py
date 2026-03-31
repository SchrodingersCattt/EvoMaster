"""Shared test fixtures for matmaster.tools tests."""

from __future__ import annotations

from typing import Any


class MockTool:
    """Mock tool satisfying the Tool Protocol for testing.

    Provides configurable name, description, schema, and execute result.
    """

    def __init__(
        self,
        name: str = "test_tool",
        description: str = "A test tool",
        result: str = "ok",
    ) -> None:
        self._name = name
        self._description = description
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> str:
        return self._result
