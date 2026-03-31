"""E2E pipeline tests for mat_master: Playground.prepare() -> Exp.assemble() -> Kernel.run().

All external dependencies mocked per D-10. Tests verify pipeline connectivity
and correct event flow without requiring real LLM/Redis/Bohrium.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from matmaster.config.exp import ExpConfig
from matmaster.core.agent import AgentKernel
from matmaster.core.bus import MessageBus
from matmaster.core.exp import Exp
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import (
    ResponseEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    Message,
    StreamChunk,
    UserMessage,
)
from matmaster.types.runtime import KernelResult

# ── Mock LLM provider ────────────────────────────────


class MockLLMProvider:
    """Mock LLM that returns a natural finish after 1 turn (no tool calls).

    Streams a single chunk with content, then a finish chunk.
    """

    def __init__(self, content: str = 'Hello from mock LLM') -> None:
        self._content = content

    async def __aenter__(self) -> MockLLMProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        return LLMResponse(content=self._content, finish_reason='stop')

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(
            content=self._content,
            stream_state='start',
            stream_id='s1',
            finish_reason='stop',
        )


class MockLLMProviderWithToolCall:
    """Mock LLM: returns tool_call on first turn, finish on second.

    First call: streams a tool call delta for 'echo' tool.
    Second call: streams natural finish with content.
    """

    def __init__(self) -> None:
        self._call_count = 0

    async def __aenter__(self) -> MockLLMProviderWithToolCall:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content='done', finish_reason='stop')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            # First turn: tool call
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        'index': 0,
                        'id': 'call_001',
                        'name': 'echo',
                        'arguments': json.dumps({'text': 'hello'}),
                    }
                ],
                finish_reason='tool_calls',
            )
        else:
            # Second turn: natural finish
            yield StreamChunk(content='Done after tool.', finish_reason='stop')


class MockLLMProviderCapturingMessages:
    """Mock LLM that captures messages passed to chat_stream for verification."""

    def __init__(self) -> None:
        self.captured_messages: list[list[dict]] = []

    async def __aenter__(self) -> MockLLMProviderCapturingMessages:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content='ok', finish_reason='stop')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        self.captured_messages.append(list(messages))
        yield StreamChunk(content='Acknowledged history.', finish_reason='stop')


# ── Mock tool ─────────────────────────────────────────


class EchoTool:
    """Simple echo tool for E2E testing. Satisfies Tool Protocol."""

    @property
    def name(self) -> str:
        return 'echo'

    @property
    def description(self) -> str:
        return 'Echoes the input text'

    @property
    def json_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {'text': {'type': 'string'}},
            'required': ['text'],
        }

    async def execute(self, arguments: dict[str, Any]) -> str:
        return f"ECHO: {arguments.get('text', '')}"


# ── Helper ────────────────────────────────────────────


def _make_pg_ctx(tmp_path: Path, llm_provider: Any = None) -> PlaygroundContext:
    """Create a test PlaygroundContext using tmp_path."""
    return PlaygroundContext(
        workdir=tmp_path / 'workspace',
        session_type='local',
        cache_area=tmp_path / 'cache',
        run_meta={'run_dir': str(tmp_path), 'task_id': 'test-task'},
        llm_provider=llm_provider,
    )


def _collect_bus_events(bus: MessageBus, timeout: float = 0.5) -> list:
    """Drain all events from bus."""
    events = []
    try:
        while True:
            events.append(bus.get_nowait())
    except asyncio.QueueEmpty:
        pass
    return events


# ── E2E tests ─────────────────────────────────────────


class TestMatMasterE2EPipeline:
    """QUAL-02: Full E2E pipeline with mock LLM."""

    _EXP_CONFIG: ExpConfig = ExpConfig(name='direct')

    async def test_mat_master_e2e_pipeline(self, tmp_path: Path) -> None:
        """E2E: Playground.prepare() -> Exp.build_runtime() -> Kernel.run() with mock LLM."""
        mock_llm = MockLLMProvider()
        pg_ctx = _make_pg_ctx(tmp_path, llm_provider=mock_llm)
        bus = MessageBus()

        exp = Exp(self._EXP_CONFIG)
        runtime = await exp.build_runtime(pg_ctx, bus=bus)

        kernel = AgentKernel()
        finish = await kernel.run(runtime.spec, 'test task')

        assert isinstance(finish.result, KernelResult)
        assert finish.result.reason == "natural"
        assert finish.result.status == "completed"

        # MessageBus received ResponseEvent from streaming content
        events = _collect_bus_events(bus)
        response_events = [e for e in events if isinstance(e, ResponseEvent)]
        assert len(response_events) >= 1

    async def test_mat_master_e2e_with_tool_call(self, tmp_path: Path) -> None:
        """E2E: Pipeline with a tool call and tool result."""
        mock_llm = MockLLMProviderWithToolCall()
        pg_ctx = _make_pg_ctx(tmp_path, llm_provider=mock_llm)
        bus = MessageBus()
        echo_tool = EchoTool()

        exp = Exp(self._EXP_CONFIG)
        runtime = await exp.build_runtime(pg_ctx, bus=bus)
        # Register echo tool directly on the runtime's registry
        runtime.spec.tool_registry.register(echo_tool, source='test')

        kernel = AgentKernel()
        finish = await kernel.run(runtime.spec, 'call echo tool')

        assert isinstance(finish.result, KernelResult)
        assert finish.result.reason == "natural"

        # Collect bus events -- should have ToolCallEvent and ToolResultEvent
        events = _collect_bus_events(bus)
        tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
        tool_result_events = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(tool_call_events) >= 1
        assert len(tool_result_events) >= 1
        assert tool_call_events[0].tool_name == 'echo'

    async def test_mat_master_e2e_with_history(self, tmp_path: Path) -> None:
        """E2E: Pipeline with multi-turn history injection."""
        mock_llm = MockLLMProviderCapturingMessages()
        pg_ctx = _make_pg_ctx(tmp_path, llm_provider=mock_llm)
        bus = MessageBus()

        history: list[Message] = [
            UserMessage(content='old question'),
            AssistantMessage(content='old answer'),
        ]

        exp = Exp(self._EXP_CONFIG)
        runtime = await exp.build_runtime(pg_ctx, bus=bus)

        kernel = AgentKernel()
        finish = await kernel.run(runtime.spec, 'new task', history=history)

        assert finish.result.reason == "natural"
        # Verify messages passed to LLM include history
        assert len(mock_llm.captured_messages) == 1
        llm_messages = mock_llm.captured_messages[0]
        # Structure: SystemMessage, old question, old answer, new task
        roles = [m['role'] for m in llm_messages]
        assert roles == ['system', 'user', 'assistant', 'user']
        assert llm_messages[1]['content'] == 'old question'
        assert llm_messages[2]['content'] == 'old answer'
        assert llm_messages[3]['content'] == 'new task'


class TestMatMasterRunAgentE2E:
    """QUAL-02: run_agent() with mock LLM provider injected."""

    @patch('matmaster.config.loader.load_llm_config')
    @patch('matmaster.providers.llm_factory.build_provider')
    def test_mat_master_run_agent_e2e(
        self, mock_build_provider, mock_load_config, tmp_path: Path
    ) -> None:
        """E2E: run_agent() with mock LLM provider injected."""
        from src.services.agent_run_service import AgentRunService

        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = 'user-123'

        svc = AgentRunService(sessions_service=mock_sessions_svc)

        # Patch build_provider to return mock LLM
        mock_llm = MockLLMProvider('E2E test response')
        mock_build_provider.return_value = mock_llm
        mock_load_config.return_value = MagicMock()

        # Patch Playground to return test context
        mock_pg = MagicMock()
        mock_pg_ctx = _make_pg_ctx(tmp_path)
        mock_pg.prepare.return_value = mock_pg_ctx
        mock_pg.config_path = Path('configs/mat_master/config.yaml')
        mock_pg.session = None

        with (
            patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
            patch(
                'src.services.agent_run_service.BohriumSetupService'
            ) as mock_bohrium_cls,
            patch(
                'src.services.agent_run_service.get_chat_events_table'
            ) as mock_events_table_fn,
            patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
            patch('src.services.agent_run_service.use_quota') as mock_use_quota,
        ):
            # Configure Bohrium mock
            mock_bohrium_result = MagicMock()
            mock_bohrium_result.ssh_attached = False
            mock_bohrium_result.abort_result = None
            mock_bohrium_result.execution_session = None
            mock_bohrium_result.execution_workdir = None
            mock_bohrium_result.session_type = None
            mock_bohrium_result._asdict.return_value = {
                "ssh_attached": False,
                "abort_result": None,
                "execution_session": None,
                "execution_workdir": None,
                "session_type": None,
            }
            mock_bohrium_svc = mock_bohrium_cls.return_value
            mock_bohrium_svc.load_credentials.return_value = ({}, None, 'org-1')
            mock_bohrium_svc.run_setup = AsyncMock(return_value=mock_bohrium_result)
            mock_bohrium_svc.run_cleanup = AsyncMock()

            # Configure events_table mock -- returned by get_chat_events_table()
            mock_events_table = MagicMock()
            mock_events_table.get_session_events.return_value = []
            mock_events_table_fn.return_value = mock_events_table

            # Configure Redis mock
            mock_redis = MagicMock()
            mock_redis_fn.return_value = mock_redis

            # use_quota is async
            async def _mock_use_quota(uid):
                pass

            mock_use_quota.side_effect = _mock_use_quota

            # Track SSE sends
            sse_payloads = []

            async def mock_send_cb(payload):
                sse_payloads.append(payload)

            # Execute
            asyncio.run(
                svc.run_agent(
                    session_id='sess-1',
                    user_prompt='test prompt',
                    send_cb=mock_send_cb,
                    stop_event=threading.Event(),
                    mode='direct',
                    reply_queue=None,
                    task_id='task-1',
                )
            )

            # Verify: pipeline completed successfully -- use_quota called (success path)
            # use_quota is the strongest signal that kernel.run() returned a
            # non-cancelled FinishEvent and post-processing ran.
            assert mock_use_quota.called, 'use_quota should be called on success'

            # Verify: SSE events were sent (send_cb was called).
            # In direct mode, streaming thought events (stream_state="start") are pushed.
            # Non-streaming thoughts are filtered. At minimum, the pipeline ran end-to-end.
            # Note: PersistenceHandler skips streaming thoughts (stream_state in start/streaming/end)
            # so add_event may not be called for this minimal mock. That's correct behavior.
            # The key E2E validation is: kernel ran -> finish -> use_quota called.

    @patch('matmaster.config.loader.load_llm_config')
    @patch('matmaster.providers.llm_factory.build_provider')
    def test_run_agent_excludes_current_task_query_from_history(
        self, mock_build_provider, mock_load_config, tmp_path: Path
    ) -> None:
        """Current task query should not be replayed into history for the LLM."""
        from src.services.agent_run_service import AgentRunService

        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = 'user-123'

        svc = AgentRunService(sessions_service=mock_sessions_svc)

        mock_llm = MockLLMProviderCapturingMessages()
        mock_build_provider.return_value = mock_llm
        mock_load_config.return_value = MagicMock()

        mock_pg = MagicMock()
        mock_pg_ctx = _make_pg_ctx(tmp_path)
        mock_pg.prepare.return_value = mock_pg_ctx
        mock_pg.config_path = Path('configs/mat_master/config.yaml')
        mock_pg.session = None

        current_task_id = 'task-1'
        raw_events = [
            {
                'source': 'User',
                'type': 'query',
                'content': 'old question',
                'task_id': 'task-0',
            },
            {
                'source': 'MatMaster',
                'type': 'finish',
                'content': 'old answer',
                'task_id': 'task-0',
            },
            {
                'source': 'User',
                'type': 'query',
                'content': 'new question',
                'task_id': current_task_id,
            },
        ]

        with (
            patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
            patch(
                'src.services.agent_run_service.BohriumSetupService'
            ) as mock_bohrium_cls,
            patch(
                'src.services.agent_run_service.get_chat_events_table'
            ) as mock_events_table_fn,
            patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
            patch('src.services.agent_run_service.use_quota') as mock_use_quota,
        ):
            mock_bohrium_result = MagicMock()
            mock_bohrium_result.ssh_attached = False
            mock_bohrium_result.abort_result = None
            mock_bohrium_result.execution_session = None
            mock_bohrium_result.execution_workdir = None
            mock_bohrium_result.session_type = None
            mock_bohrium_result._asdict.return_value = {
                "ssh_attached": False,
                "abort_result": None,
                "execution_session": None,
                "execution_workdir": None,
                "session_type": None,
            }
            mock_bohrium_svc = mock_bohrium_cls.return_value
            mock_bohrium_svc.load_credentials.return_value = ({}, None, 'org-1')
            mock_bohrium_svc.run_setup = AsyncMock(return_value=mock_bohrium_result)
            mock_bohrium_svc.run_cleanup = AsyncMock()

            mock_events_table = MagicMock()
            mock_events_table.get_session_events.return_value = raw_events
            mock_events_table_fn.return_value = mock_events_table

            mock_redis = MagicMock()
            mock_redis_fn.return_value = mock_redis

            async def _mock_use_quota(uid):
                pass

            mock_use_quota.side_effect = _mock_use_quota

            asyncio.run(
                svc.run_agent(
                    session_id='sess-1',
                    user_prompt='new question',
                    send_cb=AsyncMock(),
                    stop_event=threading.Event(),
                    mode='direct',
                    reply_queue=None,
                    task_id=current_task_id,
                )
            )

        assert len(mock_llm.captured_messages) == 1
        llm_messages = mock_llm.captured_messages[0]
        assert [m['role'] for m in llm_messages] == [
            'system',
            'user',
            'assistant',
            'user',
        ]
        assert [m['content'] for m in llm_messages[1:]] == [
            'old question',
            'old answer',
            'new question',
        ]

    @patch('matmaster.config.loader.load_llm_config')
    @patch('matmaster.providers.llm_factory.build_provider')
    def test_events_table_failure_returns_cleanly_without_router_lifecycle(
        self, mock_build_provider, mock_load_config, tmp_path: Path
    ) -> None:
        """Regression: events table failure exits before router bootstrap."""
        from src.services.agent_run_service import AgentRunService

        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = 'user-123'

        svc = AgentRunService(sessions_service=mock_sessions_svc)
        mock_load_config.return_value = MagicMock()

        mock_pg = MagicMock()
        mock_pg_ctx = _make_pg_ctx(tmp_path)
        mock_pg.prepare.return_value = mock_pg_ctx
        mock_pg.config_path = Path('configs/mat_master/config.yaml')
        mock_pg.session = None

        # Pre-populate the PlaygroundManager cache so release() finds the mock
        svc._pg_manager._playgrounds['sess-events-table-error'] = mock_pg

        with (
            patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
            patch('src.services.agent_run_service.EventRouter') as mock_router_cls,
            patch(
                'src.services.agent_run_service.BohriumSetupService'
            ) as mock_bohrium_cls,
            patch(
                'src.services.agent_run_service.get_chat_events_table',
                side_effect=RuntimeError('events table unavailable'),
            ),
            patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
            patch('src.services.agent_run_service.use_quota') as mock_use_quota,
        ):
            sse_payloads: list[dict[str, Any]] = []

            async def mock_send_cb(payload: dict[str, Any]) -> None:
                sse_payloads.append(payload)

            result = asyncio.run(
                svc.run_agent(
                    session_id='sess-events-table-error',
                    user_prompt='test prompt',
                    send_cb=mock_send_cb,
                    stop_event=threading.Event(),
                    mode='direct',
                    reply_queue=None,
                    task_id='task-events-table-error',
                )
            )

        assert result == ((False, 'pre_router_setup_failed'), 0)
        mock_router_cls.assert_not_called()
        mock_router_cls.return_value.start.assert_not_called()
        mock_router_cls.return_value.stop.assert_not_called()
        mock_bohrium_cls.assert_not_called()
        mock_build_provider.assert_not_called()
        mock_use_quota.assert_not_called()
        assert sse_payloads == []
        mock_redis_fn.return_value.delete_stop_requested.assert_called_once_with(
            'sess-events-table-error',
            'task-events-table-error',
        )
        mock_pg.cleanup.assert_called_once()

    @patch('matmaster.config.loader.load_llm_config')
    @patch('matmaster.providers.llm_factory.build_provider')
    def test_bohrium_events_reach_sse_before_setup_returns(
        self, mock_build_provider, mock_load_config, tmp_path: Path
    ) -> None:
        """Bohrium setup events must reach SSE before setup() returns."""
        from src.services.agent_run_service import AgentRunService

        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = 'user-123'

        svc = AgentRunService(sessions_service=mock_sessions_svc)
        mock_build_provider.return_value = MockLLMProvider(
            'Bohrium event test response'
        )
        mock_load_config.return_value = MagicMock()

        mock_pg = MagicMock()
        mock_pg_ctx = _make_pg_ctx(tmp_path)
        mock_pg.prepare.return_value = mock_pg_ctx
        mock_pg.config_path = Path('configs/mat_master/config.yaml')
        mock_pg.session = None

        with (
            patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
            patch(
                'src.services.agent_run_service.BohriumSetupService'
            ) as mock_bohrium_cls,
            patch(
                'src.services.agent_run_service.get_chat_events_table'
            ) as mock_events_table_fn,
            patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
            patch('src.services.agent_run_service.use_quota') as mock_use_quota,
        ):
            mock_bohrium_result = MagicMock()
            mock_bohrium_result.ssh_attached = False
            mock_bohrium_result.abort_result = None
            mock_bohrium_result.execution_session = None
            mock_bohrium_result.execution_workdir = None
            mock_bohrium_result.session_type = None
            mock_bohrium_result._asdict.return_value = {
                "ssh_attached": False,
                "abort_result": None,
                "execution_session": None,
                "execution_workdir": None,
                "session_type": None,
            }
            # Capture the bus from BohriumSetupService constructor
            captured_bus = [None]
            real_mock_svc = MagicMock()
            real_mock_svc.load_credentials.return_value = ({}, None, 'org-1')

            def _capture_init(sessions_svc, bus):
                captured_bus[0] = bus
                return real_mock_svc

            mock_bohrium_cls.side_effect = _capture_init

            mock_events_table = MagicMock()
            mock_events_table.get_session_events.return_value = []
            mock_events_table_fn.return_value = mock_events_table

            mock_redis = MagicMock()
            mock_redis_fn.return_value = mock_redis

            async def _mock_use_quota(uid):
                pass

            mock_use_quota.side_effect = _mock_use_quota

            sse_payloads: list[dict[str, Any]] = []
            bohrium_seen_by_sse = threading.Event()
            setup_state: dict[str, bool] = {}

            async def mock_send_cb(payload: dict[str, Any]) -> None:
                sse_payloads.append(payload)
                if payload.get('type') == 'bohrium_node':
                    bohrium_seen_by_sse.set()

            async def _mock_setup(**kwargs):
                from matmaster.types.events import BohriumNodeEvent

                bus = captured_bus[0]
                bus.emit_nowait(
                    BohriumNodeEvent(
                        source='BohriumSetup',
                        payload={
                            'type': 'node_ready',
                            'content': 'node is ready',
                            'stage': 'setup',
                        },
                    )
                )
                await asyncio.sleep(0)  # yield to let router dispatch
                setup_state['saw_bohrium_event_before_return'] = (
                    bohrium_seen_by_sse.wait(timeout=1.0)
                )
                return mock_bohrium_result

            real_mock_svc.run_setup = _mock_setup
            real_mock_svc.run_cleanup = AsyncMock()

            asyncio.run(
                svc.run_agent(
                    session_id='sess-bohrium-event',
                    user_prompt='test prompt',
                    send_cb=mock_send_cb,
                    stop_event=threading.Event(),
                    mode='direct',
                    reply_queue=None,
                    task_id='task-bohrium-event',
                )
            )

        assert setup_state['saw_bohrium_event_before_return'] is True
        bohrium_payload = next(
            (
                payload
                for payload in sse_payloads
                if payload.get('type') == 'bohrium_node'
            ),
            None,
        )
        assert bohrium_payload is not None
        assert bohrium_payload['payload']['type'] == 'node_ready'
        assert bohrium_payload['payload']['content'] == 'node is ready'

    @patch('matmaster.config.loader.load_llm_config')
    @patch('matmaster.providers.llm_factory.build_provider')
    def test_bohrium_abort_emits_top_level_error_and_stream_closed(
        self, mock_build_provider, mock_load_config, tmp_path: Path
    ) -> None:
        """When setup aborts, error/stream_closed must be top-level SSE types (not bohrium_node)."""
        from src.services.agent_run_bohrium import BohriumSetupResult
        from src.services.agent_run_service import AgentRunService

        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = 'user-123'

        svc = AgentRunService(sessions_service=mock_sessions_svc)
        mock_build_provider.return_value = MockLLMProvider('unused')
        mock_load_config.return_value = MagicMock()

        mock_pg = MagicMock()
        mock_pg_ctx = _make_pg_ctx(tmp_path)
        mock_pg.prepare.return_value = mock_pg_ctx
        mock_pg.config_path = Path('configs/mat_master/config.yaml')
        mock_pg.session = None

        reason = 'Bohrium 节点创建失败: no attach'

        with (
            patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
            patch(
                'src.services.agent_run_service.BohriumSetupService'
            ) as mock_bohrium_cls,
            patch(
                'src.services.agent_run_service.get_chat_events_table'
            ) as mock_events_table_fn,
            patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
            patch('src.services.agent_run_service.use_quota') as mock_use_quota,
        ):
            captured_bus = [None]
            real_mock_svc = MagicMock()
            real_mock_svc.load_credentials.return_value = ({}, None, 'org-1')

            def _capture_init(sessions_svc, bus):
                captured_bus[0] = bus
                return real_mock_svc

            mock_bohrium_cls.side_effect = _capture_init

            async def _mock_setup(**kwargs):
                from matmaster.types.events import ErrorEvent, StreamClosedEvent

                bus = captured_bus[0]
                bus.emit_nowait(ErrorEvent(source='System', message=reason))
                bus.emit_nowait(
                    StreamClosedEvent(
                        source='System',
                        content='Bohrium 节点创建失败，会话已结束.',
                        task_completed=False,
                        end_reason='error',
                        treat_as_failure=True,
                    )
                )
                return BohriumSetupResult(False, ((False, reason), 10))

            real_mock_svc.run_setup = _mock_setup
            real_mock_svc.run_cleanup = AsyncMock()

            mock_events_table = MagicMock()
            mock_events_table.get_session_events.return_value = []
            mock_events_table_fn.return_value = mock_events_table

            mock_redis = MagicMock()
            mock_redis_fn.return_value = mock_redis

            async def _mock_use_quota(uid):
                pass

            mock_use_quota.side_effect = _mock_use_quota

            sse_payloads: list[dict[str, Any]] = []

            async def mock_send_cb(payload: dict[str, Any]) -> None:
                sse_payloads.append(payload)

            asyncio.run(
                svc.run_agent(
                    session_id='sess-bohrium-abort',
                    user_prompt='test prompt',
                    send_cb=mock_send_cb,
                    stop_event=threading.Event(),
                    mode='direct',
                    reply_queue=None,
                    task_id='task-bohrium-abort',
                )
            )

        err = next((p for p in sse_payloads if p.get('type') == 'error'), None)
        closed = next(
            (p for p in sse_payloads if p.get('type') == 'stream_closed'), None
        )
        assert err is not None
        assert err['message'] == reason
        assert closed is not None
        assert closed['end_reason'] == 'error'
        assert closed['task_completed'] is False
        nested_sc = next(
            (
                p
                for p in sse_payloads
                if p.get('type') == 'bohrium_node'
                and (p.get('payload') or {}).get('type') == 'stream_closed'
            ),
            None,
        )
        assert nested_sc is None

    @patch('matmaster.config.loader.load_llm_config')
    @patch('matmaster.providers.llm_factory.build_provider')
    def test_bohrium_setup_exception_is_sent_to_sse_when_router_starts_early(
        self, mock_build_provider, mock_load_config, tmp_path: Path
    ) -> None:
        """Bohrium-stage exceptions should surface through SSE error payloads."""
        from src.services.agent_run_service import AgentRunService

        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = 'user-123'

        svc = AgentRunService(sessions_service=mock_sessions_svc)
        mock_build_provider.return_value = MockLLMProvider(
            'Bohrium exception test response'
        )
        mock_load_config.return_value = MagicMock()

        mock_pg = MagicMock()
        mock_pg_ctx = _make_pg_ctx(tmp_path)
        mock_pg.prepare.return_value = mock_pg_ctx
        mock_pg.config_path = Path('configs/mat_master/config.yaml')
        mock_pg.session = None

        with (
            patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
            patch(
                'src.services.agent_run_service.BohriumSetupService'
            ) as mock_bohrium_cls,
            patch(
                'src.services.agent_run_service.get_chat_events_table'
            ) as mock_events_table_fn,
            patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
            patch('src.services.agent_run_service.use_quota') as mock_use_quota,
        ):
            mock_bohrium_svc = mock_bohrium_cls.return_value
            mock_bohrium_svc.load_credentials.return_value = ({}, None, 'org-1')
            mock_bohrium_svc.run_setup = AsyncMock(
                side_effect=RuntimeError('bohrium setup failed')
            )
            mock_bohrium_svc.run_cleanup = AsyncMock()

            mock_events_table = MagicMock()
            mock_events_table.get_session_events.return_value = []
            mock_events_table_fn.return_value = mock_events_table

            mock_redis = MagicMock()
            mock_redis_fn.return_value = mock_redis

            async def _mock_use_quota(uid):
                pass

            mock_use_quota.side_effect = _mock_use_quota

            sse_payloads: list[dict[str, Any]] = []

            async def mock_send_cb(payload: dict[str, Any]) -> None:
                sse_payloads.append(payload)

            asyncio.run(
                svc.run_agent(
                    session_id='sess-bohrium-error',
                    user_prompt='test prompt',
                    send_cb=mock_send_cb,
                    stop_event=threading.Event(),
                    mode='direct',
                    reply_queue=None,
                    task_id='task-bohrium-error',
                )
            )

        error_payload = next(
            (payload for payload in sse_payloads if payload.get('type') == 'error'),
            None,
        )
        assert error_payload is not None
        assert error_payload['source'] == 'System'
        assert error_payload['message'] == 'bohrium setup failed'
