"""Root conftest -- async mock factories for protocol testing."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.topology import ToolPlane

_TEST_LOG_DIR = Path(tempfile.gettempdir()) / "matmaster-evo-test-logs"
_TEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("LOG_DIR", str(_TEST_LOG_DIR))


class ProviderProtocolAttrs:
    """Default public provider attrs for lightweight LLM test doubles."""

    stream_timeout: float = 300.0
    stream_idle_timeout: float = 300.0
    max_retries: int = 3
    retry_delay: float = 1.0


class MockAsyncLLMProvider(ProviderProtocolAttrs):
    """Async mock satisfying LLMProvider Protocol for testing."""

    def __init__(
        self,
        *,
        chat_response: LLMResponse | None = None,
        stream_chunks: list[StreamChunk] | None = None,
    ) -> None:
        self._chat_response = chat_response or LLMResponse(
            content="mock response", finish_reason="stop"
        )
        self._stream_chunks = stream_chunks or [
            StreamChunk(content="hello", finish_reason="stop")
        ]

    async def __aenter__(self) -> MockAsyncLLMProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return self._chat_response

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        for chunk in self._stream_chunks:
            yield chunk


class MockAsyncTool:
    """Async mock satisfying Tool Protocol for testing."""

    def __init__(
        self,
        name: str = "test_tool",
        result: str = "ok",
        description: str = "A test tool",
    ) -> None:
        self._name = name
        self._result = result
        self._description = description
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


# -- Fixtures --


@pytest.fixture(autouse=True)
def _suppress_devshell_eval_feishu() -> Generator[None]:
    """Devshell 评测飞书在单测中不发真实 webhook（``from … import`` 需在调用方模块上 patch）。"""
    with (
        patch(
            "evaluation.devshell_agent.sdk_tools_eval_run.notify_after_scoring_async"
        ),
        patch(
            "evaluation.devshell_agent.loop_proposal_notify.notify_manual_review_proposal_async"
        ),
    ):
        yield


@pytest.fixture
def async_llm_provider() -> MockAsyncLLMProvider:
    return MockAsyncLLMProvider()


@pytest.fixture
def async_tool() -> MockAsyncTool:
    return MockAsyncTool()


@pytest.fixture
def chat_events_table_with_mocks() -> tuple[Any, Any]:
    """ChatEventsTable with `get_connection` mocked to a captured cursor.

    The returned cursor records every ``cursor.execute`` call so tests
    can assert on the SQL string and params tuple. Used by tests that
    exercise ChatEventsTable's SQL-emitting methods without a real DB.
    """
    from unittest.mock import MagicMock, patch

    from src.dao.chat_events_table import ChatEventsTable

    with patch.object(ChatEventsTable, "init_table", lambda self: None):
        table = ChatEventsTable()
    cursor = MagicMock()
    conn = MagicMock()
    cursor_ctx = MagicMock()
    cursor_ctx.__enter__.return_value = cursor
    cursor_ctx.__exit__.return_value = False
    conn.cursor.return_value = cursor_ctx
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn
    conn_ctx.__exit__.return_value = False
    table.get_connection = MagicMock(return_value=conn_ctx)
    return table, cursor
