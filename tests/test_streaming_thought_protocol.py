"""Regression tests for streamed thought event protocol."""

from playground.mat_master.core.solvers._research_planner_runtime import (
    ResearchPlannerRuntimeMixin,
)
from playground.mat_master.service.stream_agent import StreamingMatMasterAgent
from src.services.agent_run_service import (
    _should_persist_event,
    _should_skip_push,
)


def test_streaming_agent_emits_thought_stream_events():
    """Direct streaming should use thought + stream_state instead of llm_token."""
    events: list[dict] = []
    agent = StreamingMatMasterAgent.__new__(StreamingMatMasterAgent)
    agent.event_callback = lambda source, event_type, content, **extra: events.append(
        {
            'source': source,
            'type': event_type,
            'content': content,
            **extra,
        }
    )
    agent._current_stream_id = None
    agent._stream_token_count = 0
    agent._agent_name = 'Coder'

    agent._begin_llm_stream('MatMaster', context='step_execution')
    agent._on_llm_token_cb('alpha')
    agent._end_llm_stream('MatMaster')

    assert [event['type'] for event in events] == ['thought', 'thought', 'thought']
    assert [event.get('stream_state') for event in events] == [
        'start',
        'streaming',
        'end',
    ]
    assert events[1]['content'] == 'alpha'
    assert events[2]['token_count'] == len('alpha')
    assert events[0]['stream_id'] == events[1]['stream_id'] == events[2]['stream_id']


class _PlannerRuntimeProbe(ResearchPlannerRuntimeMixin):
    """Minimal probe for planner event normalization."""

    def __init__(self):
        self.events: list[dict] = []
        self._output_callback = (
            lambda source, event_type, content, **extra: self.events.append(
                {
                    'source': source,
                    'type': event_type,
                    'content': content,
                    **extra,
                }
            )
        )


def test_planner_keeps_streaming_thought_but_normalizes_final_reply():
    """Planner should stream thought deltas but keep final reply as planner_reply."""
    runtime = _PlannerRuntimeProbe()

    runtime._emit('Planner', 'thought', 'alpha', stream_state='streaming')
    runtime._emit('Planner', 'thought', 'final answer')

    assert runtime.events[0]['type'] == 'thought'
    assert runtime.events[0]['stream_state'] == 'streaming'
    assert runtime.events[1]['type'] == 'planner_reply'
    assert isinstance(runtime.events[1]['content'], dict)


def test_direct_mode_streamed_thought_is_ephemeral_but_final_thought_is_durable():
    """Only the final thought snapshot should be persisted in direct mode."""
    assert not _should_persist_event('thought', {'stream_state': 'start'})
    assert not _should_persist_event('thought', {'stream_state': 'streaming'})
    assert not _should_persist_event('thought', {'stream_state': 'end'})
    assert _should_persist_event('thought', {})
    assert not _should_persist_event('llm_token', {'status': 'streaming'})

    assert not _should_skip_push(
        'direct', 'MatMaster', 'thought', {'stream_state': 'streaming'}
    )
    assert _should_skip_push('direct', 'MatMaster', 'thought', {})
    assert _should_skip_push(
        'planner', 'Planner', 'thought', {'stream_state': 'streaming'}
    )
    assert _should_skip_push('planner', 'Planner', 'thought', {'stream_state': 'start'})
    assert _should_skip_push('planner', 'Planner', 'thought', {'stream_state': 'end'})
    assert not _should_skip_push('planner', 'Planner', 'thought', {})
