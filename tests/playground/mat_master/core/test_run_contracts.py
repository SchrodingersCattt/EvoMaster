import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from playground.mat_master.core.job_registry import JobRegistry
from playground.mat_master.core.run_contracts import RunContractManager


class _SkillRegistry:
    def __init__(self, root: Path):
        self.root = root

    def get_skill(self, name):
        if name != 'deep-survey':
            return None
        return SimpleNamespace(skill_path=self.root)


def _manager(tmp_path: Path) -> RunContractManager:
    skill_root = tmp_path / 'deep-survey'
    contract = skill_root / 'reference' / 'comparative_candidate_search.md'
    contract.parent.mkdir(parents=True)
    contract.write_text('# contract\nsymmetric retrieval\n', encoding='utf-8')
    protocol = {
        'protocol': {
            'broad_searches': ['a', 'b', 'c', 'd', 'e'],
            'finalist_count': 3,
            'finalist_rounds': ['direct', 'adverse', 'mechanism', 'feasibility'],
            'calls_per_finalist_per_round': 1,
            'inspected_records_per_finalist_per_round': 1,
            'symmetric_gap_filling': True,
            'abstention_allowed': True,
        }
    }
    protocol_path = tmp_path / 'protocol.yaml'
    protocol_path.write_text(yaml.safe_dump(protocol), encoding='utf-8')
    config = {
        'agents': {
            'general': {
                'prompt_profile': 'scoped',
                'execution_mode': 'direct',
                'runtime_tool_allowlist': ['finish', 'peek_file'],
                'required_contracts': [
                    {
                        'package': 'deep-survey',
                        'entrypoint': 'reference/comparative_candidate_search.md',
                    }
                ],
                'contract_config_file': str(protocol_path),
            }
        }
    }
    return RunContractManager(config, _SkillRegistry(skill_root), tmp_path)


def _valid_workspace(tmp_path: Path, manager: RunContractManager):
    manager.initialize_state(tmp_path)
    state_path = tmp_path / '_tmp' / 'protocol_state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['phase'] = 'complete'
    state['broad_queries'] = [
        {'query': f'broad-{i}', 'step': i} for i in range(1, 6)
    ]
    state['finalists'] = ['A', 'B', 'C']
    step = 6
    state['rounds'] = []
    inspected = []
    for role in manager.protocol['finalist_rounds']:
        candidates = {}
        for finalist in state['finalists']:
            source = f'{role}-{finalist}'
            candidates[finalist] = {
                'query_steps': [step],
                'inspected_sources': [source],
            }
            inspected.append(source)
            step += 1
        state['rounds'].append({'name': role, 'candidates': candidates})
    state['inspected_sources'] = inspected
    state_path.write_text(json.dumps(state), encoding='utf-8')

    (tmp_path / 'evidence_matrix.csv').write_text(
        'source,composition,dose,observation,scope\n'
        'doi:1,A,1,measured,reported range\n',
        encoding='utf-8',
    )
    for name in (
        'outer_loop_decision.md',
        'base_host_ga.csv',
        'next_batch.csv',
    ):
        (tmp_path / name).write_text('complete\n', encoding='utf-8')
    (tmp_path / 'run_result.json').write_text(
        json.dumps(
            {
                'primary_element': 'A',
                'abstained': False,
                'protocol_pass': False,
                'finalists': ['A', 'B', 'C'],
                'search_calls': {
                    'broad': 5,
                    'targeted': {'A': 4, 'B': 4, 'C': 4},
                },
                'inspected_sources': {'A': 4, 'B': 4, 'C': 4},
                'dart': {
                    'submitted': True,
                    'job_id': 'job-1',
                    'status': 'finished',
                    'results_retrieved': True,
                },
                'usage': {
                    'prompt_tokens': 0,
                    'completion_tokens': 0,
                    'total_tokens': 0,
                },
            }
        ),
        encoding='utf-8',
    )
    journal = [
        {'step': i, 'tool': 'mat_sn_search-papers-enhanced'}
        for i in range(1, 18)
    ]
    jobs = JobRegistry(SimpleNamespace(warning=lambda *args: None))
    jobs.record_submit(
        job_id='job-1',
        software='compdart',
        source_tool='mat_compdart_submit_run_dart_ga',
        native_lifecycle=True,
    )
    jobs.record_native_status('job-1', 'finished')
    jobs.record_native_results('job-1', {'rows': [1]})
    return state_path, journal, jobs


def test_scoped_system_prompt_is_project_independent():
    path = (
        Path(__file__).resolve().parents[4]
        / 'playground'
        / 'mat_master'
        / 'prompts'
        / 'scoped_system_prompt.txt'
    )
    text = path.read_text(encoding='utf-8')
    assert len([line for line in text.splitlines() if line.strip()]) == 8
    lower = text.lower()
    for leaked in ('candidate', 'comparison', 'invar', 'silicon', 'dart'):
        assert leaked not in lower


def test_contract_is_preloaded_hashed_and_tools_are_exact(tmp_path):
    manager = _manager(tmp_path)
    assert manager.active
    assert len(manager.contracts[0]['sha256']) == 64
    specs = [
        SimpleNamespace(function=SimpleNamespace(name='finish')),
        SimpleNamespace(function=SimpleNamespace(name='peek_file')),
        SimpleNamespace(function=SimpleNamespace(name='monitor_job')),
    ]
    assert [
        spec.function.name for spec in manager.filter_specs(specs)
    ] == ['finish', 'peek_file']
    assert 'symmetric retrieval' in manager.contract_text()


def test_missing_required_contract_fails_before_agent_call(tmp_path):
    config = {
        'agents': {
            'general': {
                'prompt_profile': 'scoped',
                'required_contracts': [
                    {'package': 'missing', 'entrypoint': 'reference/a.md'}
                ],
            }
        }
    }
    try:
        RunContractManager(config, _SkillRegistry(tmp_path), tmp_path)
    except FileNotFoundError as exc:
        assert 'not loaded' in str(exc)
    else:
        raise AssertionError('missing required contract was accepted')


def test_valid_protocol_passes_and_marks_structured_result(tmp_path):
    manager = _manager(tmp_path)
    state_path, journal, jobs = _valid_workspace(tmp_path, manager)
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert errors == []
    assert json.loads(state_path.read_text())['protocol_pass'] is True
    result = json.loads((tmp_path / 'run_result.json').read_text())
    assert result['protocol_pass'] is True
    assert result['primary_element'] == 'A'


def test_asymmetric_candidate_round_is_blocked(tmp_path):
    manager = _manager(tmp_path)
    state_path, journal, jobs = _valid_workspace(tmp_path, manager)
    state = json.loads(state_path.read_text())
    state['rounds'][1]['candidates'].pop('C')
    state_path.write_text(json.dumps(state))
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert any('does not cover every finalist' in error for error in errors)


def test_targeted_search_before_broad_completion_is_blocked(tmp_path):
    manager = _manager(tmp_path)
    state_path, journal, jobs = _valid_workspace(tmp_path, manager)
    state = json.loads(state_path.read_text())
    state['broad_queries'][-1]['step'] = 30
    state_path.write_text(json.dumps(state))
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert any('before broad retrieval finished' in error for error in errors)


def test_generic_monitor_and_unresolved_semantic_violation_are_blocked(tmp_path):
    manager = _manager(tmp_path)
    state_path, journal, jobs = _valid_workspace(tmp_path, manager)
    state = json.loads(state_path.read_text())
    state['violations'] = ['unsupported favorable dose window']
    state_path.write_text(json.dumps(state))
    journal.append({'step': 18, 'tool': 'monitor_job'})
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert 'protocol has unresolved violations' in errors
    assert 'monitor_job was used in a native-lifecycle run' in errors


def test_native_job_is_not_polled_by_generic_refresh():
    jobs = JobRegistry(SimpleNamespace(warning=lambda *args: None))
    jobs.record_submit(
        job_id='native-1',
        software='compdart',
        source_tool='mat_compdart_submit_run_dart_ga',
        native_lifecycle=True,
    )
    jobs.refresh_pending()
    assert jobs.jobs['native-1'].lifecycle_state == 'submitted'
