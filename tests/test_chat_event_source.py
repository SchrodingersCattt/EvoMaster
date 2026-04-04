"""Tests for backend chat event source normalization."""

import logging
from types import SimpleNamespace

import pytest

from playground.mat_master.core.solvers._research_planner_runtime import (
    ResearchPlannerRuntimeMixin,
)
from playground.mat_master.service.server.chat_utils import normalize_event_source


def test_normalize_event_source_collapses_internal_labels():
    """Only User/System remain distinct; all internal roles map to MatMaster."""
    assert normalize_event_source('User') == 'User'
    assert normalize_event_source('System') == 'System'
    assert normalize_event_source('Planner') == 'MatMaster'
    assert normalize_event_source('ToolExecutor') == 'MatMaster'
    assert normalize_event_source('Coder') == 'MatMaster'
    assert normalize_event_source('general') == 'MatMaster'
    assert normalize_event_source('') == 'MatMaster'


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
