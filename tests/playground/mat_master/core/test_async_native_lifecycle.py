import json
from types import SimpleNamespace

from evomaster.agent.tools.mcp.mcp import MCPTool
from playground.mat_master.core.async_execution_policy import AsyncExecutionPolicy
from playground.mat_master.core.async_tool_registry import AsyncToolRegistry
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
