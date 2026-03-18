"""Tests for backend chat event source normalization."""

import logging
from types import SimpleNamespace

import pytest

from playground.mat_master.core.solvers._research_planner_runtime import (
    ResearchPlannerRuntimeMixin,
)
from src.services.chat_history import ChatHistoryConverter
from src.utils.chat_event_source import normalize_event_source


def test_normalize_event_source_collapses_internal_labels():
    """Only User/System remain distinct; all internal roles map to MatMaster."""
    assert normalize_event_source('User') == 'User'
    assert normalize_event_source('System') == 'System'
    assert normalize_event_source('Planner') == 'MatMaster'
    assert normalize_event_source('ToolExecutor') == 'MatMaster'
    assert normalize_event_source('Coder') == 'MatMaster'
    assert normalize_event_source('general') == 'MatMaster'
    assert normalize_event_source('') == 'MatMaster'


def test_chat_history_converter_uses_stable_types_for_assistant_events():
    """Planner/internal sources should still reconstruct assistant history."""
    events = [
        {'source': 'User', 'type': 'query', 'content': 'hello'},
        {'source': 'general', 'type': 'thought', 'content': 'draft answer'},
        {'source': 'Planner', 'type': 'planner_reply', 'content': 'plan update'},
        {
            'source': 'ToolExecutor',
            'type': 'tool_call',
            'content': {'id': 'call_1', 'name': 'demo_tool', 'args': {'x': 1}},
        },
        {
            'source': 'ToolExecutor',
            'type': 'tool_result',
            'content': {'id': 'call_1', 'name': 'demo_tool', 'result': {'ok': True}},
        },
        {'source': 'Planner', 'type': 'finish', 'content': 'done'},
    ]

    out = ChatHistoryConverter.events_to_dialog_messages(events)

    assert [msg.get('role') for msg in out] == [
        'user',
        'assistant',
        'assistant',
        'assistant',
        'tool',
        'assistant',
    ]
    assert out[1]['content'] == 'draft answer'
    assert out[2]['content'] == 'plan update'
    assert out[3]['tool_calls'][0]['function']['name'] == 'demo_tool'
    assert out[4]['name'] == 'demo_tool'
    assert out[5]['content'] == 'done'


def test_planner_ask_human_requires_confirmation_manager():
    """Planner confirmation should fail fast when ConfirmationManager is absent."""

    class _Harness(ResearchPlannerRuntimeMixin):
        def __init__(self):
            self.agent = SimpleNamespace()
            self._output_callback = None
            self.logger = logging.getLogger('test.planner')

    harness = _Harness()

    with pytest.raises(RuntimeError, match='ConfirmationManager'):
        harness._ask_human('continue?', mode='block')
