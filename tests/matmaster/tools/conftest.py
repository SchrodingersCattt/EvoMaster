"""Shared test fixtures for matmaster.tools tests."""

from __future__ import annotations

from typing import Any

from matmaster.types.topology import ToolPlane


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
        self.resource_claims = ()
        self.capabilities = frozenset()
        self.effect_level = "local_mutation"
        self.fast_path_eligible = False
        self.max_result_chars = 0
        self.plane = ToolPlane.CONTROL_PLANE
        self.state_mode = "stateless"
        self.stop_mode = "cancellable"
        self.exposed_to_model = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def describe(self, ctx: Any) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> str:
        return self._result
