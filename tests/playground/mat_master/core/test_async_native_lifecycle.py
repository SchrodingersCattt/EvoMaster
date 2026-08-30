import json
import time
from types import SimpleNamespace

import pytest

from evomaster.agent.tools.mcp.mcp import MCPTool
from playground.mat_master.core.async_execution_policy import AsyncExecutionPolicy
from playground.mat_master.core.async_tool_registry import AsyncToolRegistry
from playground.mat_master.core.callback import (
    MatToolCallbacks,
    ToolCallbackPipeline,
    ToolCallRejected,
)
from playground.mat_master.core.job_registry import JobRegistry


def _config(native_lifecycle=True):
    return {
        'mcp': {
            'calculation_executors': {
                'mat_compdart': {
                    'native_lifecycle': native_lifecycle,
                    'executor': {
                        'machine': {'remote_profile': {'image_address': 'test'}}
                    },
                    'sync_tools': [],
                },
                'mat_dpa': {
                    'executor': {
                        'machine': {'remote_profile': {'image_address': 'test'}}
                    },
                    'sync_tools': [],
                },
            }
        }
    }


def _spec(name):
    return SimpleNamespace(function=SimpleNamespace(name=name))


def _call(name, arguments=None):
    return SimpleNamespace(
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments or {}),
        )
    )


def test_policy_exposes_only_configured_native_lifecycle_tools():
    registry = AsyncToolRegistry(_config())
    policy = AsyncExecutionPolicy(registry)
    names = [
        'mat_compdart_submit_run_dart_ga',
        'mat_compdart_run_dart_ga',
        'mat_compdart_query_job_status',
        'mat_compdart_get_job_results',
        'mat_compdart_terminate_job',
        'mat_dpa_submit_predict',
        'mat_dpa_query_job_status',
    ]

    filtered = policy.filter_tool_specs_for_llm([_spec(name) for name in names])
    exposed = {spec.function.name for spec in filtered}

    assert exposed == {
        'mat_compdart_submit_run_dart_ga',
        'mat_compdart_query_job_status',
        'mat_compdart_get_job_results',
        'mat_dpa_submit_predict',
    }
    assert policy.is_call_allowed_while_pending(
        _call('mat_compdart_query_job_status', {'job_id': 'job-1'})
    )
    assert policy.is_call_allowed_while_pending(
        _call('mat_compdart_get_job_results', {'job_id': 'job-1'})
    )
    assert not policy.is_call_allowed_while_pending(
        _call('mat_dpa_query_job_status', {'job_id': 'job-1'})
    )


def test_registry_rules_route_compdart_to_native_lifecycle():
    registry = AsyncToolRegistry(_config())
    rules = registry.format_calculation_rules()

    assert registry.uses_native_lifecycle('mat_compdart')
    assert not registry.uses_native_lifecycle('mat_dpa')
    assert 'mat_compdart_* for COMPDART' in rules
    assert 'query_job_status' in rules
    assert 'get_job_results' in rules
    assert 'mat_dpa_* for DPA' in rules
    assert 'monitor_job' in rules


def test_native_status_and_results_release_pending_gate():
    registry = JobRegistry(SimpleNamespace(warning=lambda *args: None))
    registry.record_submit(
        job_id='job-1', software='compdart', source_tool='submit_run_dart_ga'
    )

    registry.record_native_status('job-1', 'Running')
    assert len(registry.pending_jobs()) == 1

    registry.record_native_status('job-1', 'Finished')
    assert len(registry.pending_jobs()) == 1

    registry.record_native_results('job-1', {'best': [0.8, 0.2]})
    assert registry.pending_jobs() == []
    assert registry.jobs['job-1'].results == {'best': [0.8, 0.2]}


def test_dict_submit_response_tracks_complete_native_lifecycle():
    logger = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None)
    agent = SimpleNamespace(
        logger=logger,
        _job_registry=JobRegistry(logger),
        _async_tool_registry=AsyncToolRegistry(_config()),
    )
    callbacks = MatToolCallbacks(agent)

    callbacks.after_track_async_submit(
        _call('mat_compdart_submit_run_dart_ga'),
        {
            'job_id': 'native-1',
            'extra_info': {'bohr_job_id': 'bohr-1'},
        },
        {'success': True},
    )
    callbacks.after_track_native_lifecycle(
        _call('mat_compdart_query_job_status', {'job_id': 'native-1'}),
        {'status': 'Succeeded'},
        {},
    )
    callbacks.after_track_native_lifecycle(
        _call('mat_compdart_get_job_results', {'job_id': 'native-1'}),
        {'best': [0.8, 0.2]},
        {},
    )

    record = agent._job_registry.jobs['native-1']
    assert record.native_lifecycle is True
    assert record.bohr_job_id == 'bohr-1'
    assert record.lifecycle_state == 'succeeded'
    assert record.results == {'best': [0.8, 0.2]}


def test_native_results_require_success_status_and_nonempty_payload():
    logger = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None)
    registry = JobRegistry(logger)
    registry.record_submit(
        job_id='native-1',
        software='compdart',
        source_tool='mat_compdart_submit_run_dart_ga',
        native_lifecycle=True,
    )
    callbacks = MatToolCallbacks(
        SimpleNamespace(
            logger=logger,
            _job_registry=registry,
            _async_tool_registry=AsyncToolRegistry(_config()),
        )
    )
    results_call = _call(
        'mat_compdart_get_job_results',
        {'job_id': 'native-1'},
    )

    with pytest.raises(ToolCallRejected, match='results are unavailable'):
        callbacks.before_validate_job_lifecycle_route(results_call)

    registry.record_native_status('native-1', 'Succeeded')
    callbacks.before_validate_job_lifecycle_route(results_call)
    assert registry.record_native_results('native-1', {}) is False
    assert registry.jobs['native-1'].lifecycle_state == 'results_pending'
    assert registry.record_native_results(
        'native-1', {'best': [0.8, 0.2]}
    ) is True

    registry.record_native_status('native-1', 'Running')
    assert registry.jobs['native-1'].lifecycle_state == 'succeeded'


def test_pipeline_propagates_intentional_tool_rejection():
    pipeline = ToolCallbackPipeline(
        SimpleNamespace(warning=lambda *args: None)
    )

    def reject(_tool_call):
        raise ToolCallRejected('blocked')

    pipeline.register_before(reject)
    with pytest.raises(ToolCallRejected, match='blocked'):
        pipeline.run_before(_call('peek_file'))


def test_runtime_owned_protocol_state_is_rejected():
    logger = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None)
    callbacks = MatToolCallbacks(
        SimpleNamespace(
            logger=logger,
            _job_registry=JobRegistry(logger),
            _async_tool_registry=AsyncToolRegistry(_config()),
        )
    )
    call = _call(
        'peek_file',
        {'file_path': '/workspace/_tmp/protocol_state.json'},
    )
    with pytest.raises(ToolCallRejected, match='not accessible'):
        callbacks.before_reject_runtime_owned_state_access(call)


def test_native_status_polling_is_throttled():
    logger = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None)
    registry = JobRegistry(logger)
    registry.record_submit(
        job_id='native-1',
        software='compdart',
        source_tool='mat_compdart_submit_run_dart_ga',
        native_lifecycle=True,
    )
    callbacks = MatToolCallbacks(
        SimpleNamespace(
            logger=logger,
            _job_registry=registry,
            _async_tool_registry=AsyncToolRegistry(_config()),
        )
    )
    callbacks._native_poll_interval_seconds = 0.02
    call = _call('mat_compdart_query_job_status', {'job_id': 'native-1'})

    start = time.monotonic()
    callbacks.before_throttle_native_status_poll(call)
    callbacks.before_throttle_native_status_poll(call)

    assert time.monotonic() - start >= 0.015


class _PathAdaptor:
    def __init__(self):
        self.calls = 0

    def resolve_args(self, workspace_path, args, *unused_args, **unused_kwargs):
        self.calls += 1
        return {**args, 'adapted': True}


def _mcp_tool(remote_name):
    tool = MCPTool(
        mcp_connection=SimpleNamespace(),
        tool_name=f'mat_compdart_{remote_name}',
        tool_description='test',
        input_schema={'type': 'object'},
        remote_tool_name=remote_name,
    )
    tool._mcp_server = 'mat_compdart'
    tool._path_adaptor = _PathAdaptor()
    tool._call_mcp_tool_sync = lambda args: args
    return tool


def test_native_lifecycle_bypasses_path_adaptor():
    tool = _mcp_tool('query_job_status')

    observation, _ = tool.execute(None, '{"job_id": "job-1"}')

    assert observation == {'job_id': 'job-1'}
    assert tool._path_adaptor.calls == 0


def test_submit_still_uses_path_adaptor():
    tool = _mcp_tool('submit_run_dart_ga')

    observation, _ = tool.execute(None, '{"elements": ["Fe", "Ni"]}')

    assert observation['adapted'] is True
    assert tool._path_adaptor.calls == 1


def test_compdart_schema_explains_single_comparison_conditions():
    schema = {
        'type': 'object',
        'properties': {
            'constraints': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {'condition': {'type': 'string'}},
                },
            }
        },
    }
    spec = SimpleNamespace(
        function=SimpleNamespace(
            name='mat_compdart_submit_run_dart_ga',
            parameters=schema,
        )
    )
    policy = AsyncExecutionPolicy(AsyncToolRegistry(_config()))

    policy.filter_tool_specs_for_llm([spec])

    description = schema['properties']['constraints']['items']['properties'][
        'condition'
    ]['description']
    assert 'one comparison operator' in description
    assert 'two constraint entries with the same target' in description


def test_compdart_chained_constraint_is_rejected_before_submission():
    logger = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None)
    callbacks = MatToolCallbacks(
        SimpleNamespace(
            logger=logger,
            _job_registry=JobRegistry(logger),
            _async_tool_registry=AsyncToolRegistry(_config()),
        )
    )
    callbacks.before_validate_compdart_constraint_syntax(
        _call(
            'mat_compdart_submit_run_dart_ga',
            {
                'constraints': [
                    {'target': 'A', 'condition': '>=0.1'},
                    {'target': 'A', 'condition': '<0.5'},
                ]
            },
        )
    )
    with pytest.raises(ToolCallRejected, match='closed range as two entries'):
        callbacks.before_validate_compdart_constraint_syntax(
            _call(
                'mat_compdart_submit_run_dart_ga',
                {'constraints': [{'target': 'A', 'condition': '0.1 <= x < 0.5'}]},
            )
        )


@pytest.mark.parametrize('arguments', [{}, {'constraints': []}])
def test_required_compdart_constraints_cannot_be_omitted(arguments):
    logger = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None)
    callbacks = MatToolCallbacks(
        SimpleNamespace(
            logger=logger,
            _job_registry=JobRegistry(logger),
            _async_tool_registry=AsyncToolRegistry(_config()),
            _run_contracts=SimpleNamespace(
                protocol={
                    'compdart': {'require_agent_authored_constraints': True}
                }
            ),
        )
    )

    with pytest.raises(ToolCallRejected, match='unconstrained submission'):
        callbacks.before_validate_compdart_constraint_syntax(
            _call('mat_compdart_submit_run_dart_ga', arguments)
        )


def test_optional_compdart_constraints_may_be_omitted():
    logger = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None)
    callbacks = MatToolCallbacks(SimpleNamespace(logger=logger))
    callbacks.before_validate_compdart_constraint_syntax(
        _call('mat_compdart_submit_run_dart_ga', {})
    )
