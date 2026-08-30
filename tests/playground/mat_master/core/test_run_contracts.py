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
            'compdart': {'require_agent_authored_constraints': True},
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
    roles = manager.protocol['finalist_rounds']
    finalists = ['A', 'B', 'C']
    rows = [
        'source,composition,dose,observation,scope,evidence_role,candidate'
    ]
    for role in roles:
        for finalist in finalists:
            rows.append(
                f'doi:{role}-{finalist},{finalist},1,measured,reported,{role},{finalist}'
            )
    (tmp_path / 'evidence_matrix.csv').write_text(
        '\n'.join(rows) + '\n', encoding='utf-8'
    )
    for name in ('outer_loop_decision.md', 'base_host_ga.csv', 'next_batch.csv'):
        (tmp_path / name).write_text('complete\n', encoding='utf-8')
    (tmp_path / 'run_result.json').write_text(
        json.dumps(
            {
                'primary_element': 'A',
                'abstained': False,
                'finalists': finalists,
            }
        ),
        encoding='utf-8',
    )

    journal = []
    for step in range(1, 6):
        journal.append(
            {
                'step': step,
                'tool': 'mat_sn_search-papers-enhanced',
                'status': 'success',
                'arguments': {
                    'question': f'broad facet {step}',
                    'page_size': 20,
                },
            }
        )
    step = 6
    for role in roles:
        for finalist in finalists:
            journal.append(
                {
                    'step': step,
                    'tool': 'mat_sn_search-papers-enhanced',
                    'status': 'success',
                    'arguments': {
                        'question': (
                            f'[ROLE: {role}] [FINALIST: {finalist}] '
                            f'{role} evidence for finalist {finalist}'
                        ),
                        'page_size': 10,
                    },
                }
            )
            step += 1
    journal.append(
        {
            'step': step,
            'tool': 'mat_compdart_submit_run_dart_ga',
            'status': 'success',
            'arguments': {
                'constraints': [
                    {'target': 'A', 'condition': '>0.25'},
                    {'target': ['B', 'C'], 'condition': '<0.75'},
                ]
            },
        }
    )

    jobs = JobRegistry(SimpleNamespace(warning=lambda *args: None))
    jobs.record_submit(
        job_id='job-1',
        software='compdart',
        source_tool='mat_compdart_submit_run_dart_ga',
        native_lifecycle=True,
    )
    jobs.record_native_status('job-1', 'finished')
    jobs.record_native_results('job-1', {'rows': [1]})
    return journal, jobs


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
    assert [spec.function.name for spec in manager.filter_specs(specs)] == [
        'finish',
        'peek_file',
    ]
    prompt = manager.contract_text()
    assert 'symmetric retrieval' in prompt
    assert 'Do not create, inspect, or edit internal protocol-state files.' in prompt


def test_missing_runtime_tool_fails_before_agent_call(tmp_path):
    manager = _manager(tmp_path)
    specs = [SimpleNamespace(function=SimpleNamespace(name='finish'))]
    try:
        manager.filter_specs(specs)
    except RuntimeError as exc:
        assert 'peek_file' in str(exc)
    else:
        raise AssertionError('missing required runtime tool was accepted')


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


def test_valid_protocol_is_derived_and_marks_structured_result(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert errors == []
    state = json.loads((tmp_path / '_tmp/protocol_state.json').read_text())
    assert state['protocol_pass'] is True
    assert len(state['broad_queries']) == 5
    assert len(state['rounds']) == 4
    result = json.loads((tmp_path / 'run_result.json').read_text())
    assert result['protocol_pass'] is True
    assert result['search_calls']['targeted'] == {'A': 4, 'B': 4, 'C': 4}
    assert result['inspected_sources'] == {'A': 4, 'B': 4, 'C': 4}
    assert result['dart']['results_retrieved'] is True
    assert result['dart']['submitted_constraints'] == [
        {'target': 'A', 'condition': '>0.25'},
        {'target': ['B', 'C'], 'condition': '<0.75'},
    ]
    assert result['dart']['result_payload'] == {'rows': [1]}


def test_missing_agent_authored_compdart_constraints_is_blocked(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    journal[-1]['arguments'].pop('constraints')
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert (
        'CompDART submit omitted required agent-authored constraints at steps: 18'
        in errors
    )


def test_any_unconstrained_compdart_submit_invalidates_run(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    journal.insert(
        -1,
        {
            'step': 17,
            'tool': 'mat_compdart_submit_run_dart_ga',
            'status': 'success',
            'arguments': {},
        },
    )
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert any('at steps: 17' in error for error in errors)


def test_irrecoverable_protocol_errors_are_classified():
    assert RunContractManager.errors_are_irrecoverable(
        ['targeted round "direct" is asymmetric for "A"']
    )
    assert RunContractManager.errors_are_irrecoverable(
        ['targeted round "direct" query has evidence role "mechanism"']
    )
    assert not RunContractManager.errors_are_irrecoverable(
        ['evidence role "direct", finalist "A" has 0/1 inspected records']
    )


def test_model_authored_protocol_state_is_ignored(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    path = tmp_path / '_tmp/protocol_state.json'
    path.write_text(json.dumps({'contract': {'sha256': 'forged'}}))
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert errors == []
    state = json.loads(path.read_text())
    assert state['contract']['sha256'] == manager.contracts[0]['sha256']


def test_asymmetric_targeted_round_is_blocked(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    journal.pop(-2)
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert 'targeted searches end with an incomplete symmetric round' in errors


def test_additional_candidate_neutral_broad_search_is_allowed(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    journal.insert(
        5,
        {
            'step': 6,
            'tool': 'mat_sn_search-papers-enhanced',
            'status': 'success',
            'arguments': {
                'question': 'one additional candidate-neutral broad facet',
                'page_size': 20,
            },
        },
    )
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert errors == []
    result = json.loads((tmp_path / 'run_result.json').read_text())
    assert result['search_calls']['broad'] == 6


def test_named_finalist_in_broad_search_is_blocked(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    journal[2]['arguments']['question'] = 'broad search centered on A'
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert (
        'named finalist retrieval began before broad searches completed'
        in errors
    )


def test_one_candidate_gap_fill_is_blocked(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    journal.append(
        {
            'step': 30,
            'tool': 'mat_sn_search-papers-enhanced',
            'status': 'success',
            'arguments': {'question': 'extra evidence for A', 'page_size': 10},
        }
    )
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert 'targeted searches end with an incomplete symmetric round' in errors


def test_inconsistent_round_parameters_are_blocked(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    journal[6]['arguments']['page_size'] = 99
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert any('inconsistent retrieval parameters' in error for error in errors)


def test_evidence_role_coverage_is_runtime_checked(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    path = tmp_path / 'evidence_matrix.csv'
    lines = path.read_text().splitlines()
    path.write_text('\n'.join(lines[:-1]) + '\n')
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert any('has 0/1 inspected records' in error for error in errors)


def test_generic_monitor_is_blocked(tmp_path):
    manager = _manager(tmp_path)
    journal, jobs = _valid_workspace(tmp_path, manager)
    journal.append({'step': 30, 'tool': 'monitor_job', 'status': 'success'})
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert 'monitor_job was used in a native-lifecycle run' in errors


def test_native_job_without_results_is_blocked(tmp_path):
    manager = _manager(tmp_path)
    journal, _ = _valid_workspace(tmp_path, manager)
    jobs = JobRegistry(SimpleNamespace(warning=lambda *args: None))
    jobs.record_submit(
        job_id='native-1',
        software='compdart',
        source_tool='mat_compdart_submit_run_dart_ga',
        native_lifecycle=True,
    )
    jobs.record_native_status('native-1', 'finished')
    errors, _ = manager.validate_finish(tmp_path, journal, jobs)
    assert 'no CompDART job completed its native lifecycle' in errors


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

def test_targeted_retrieval_requires_model_authored_finalist_lock(tmp_path):
    manager = _manager(tmp_path)
    manager.initialize_state(tmp_path)
    broad = [
        {
            'tool': 'mat_sn_search-papers-enhanced',
            'status': 'success',
            'arguments': {'question': f'broad facet {index}'},
        }
        for index in range(5)
    ]
    error = manager.validate_retrieval_start(
        tmp_path,
        broad,
        {'question': '[ROLE: direct] [FINALIST: A] direct evidence for A'},
    )
    assert 'write run_result.json' in error

    (tmp_path / 'run_result.json').write_text(
        json.dumps({'finalists': ['A', 'B', 'C']}),
        encoding='utf-8',
    )
    assert manager.validate_retrieval_start(
        tmp_path,
        broad,
        {'question': '[ROLE: direct] [FINALIST: A] direct evidence for A'},
    ) is None
    state = json.loads((tmp_path / '_tmp/protocol_state.json').read_text())
    assert state['phase'] == 'finalists_locked'
    assert state['finalists'] == ['A', 'B', 'C']


def test_optional_broad_search_and_locked_finalists_are_enforced(tmp_path):
    manager = _manager(tmp_path)
    manager.initialize_state(tmp_path)
    broad = [
        {
            'tool': 'mat_sn_search-papers-enhanced',
            'status': 'success',
            'arguments': {'question': f'broad facet {index}'},
        }
        for index in range(5)
    ]
    assert manager.validate_retrieval_start(
        tmp_path,
        broad,
        {'question': '[BROAD] one additional neutral facet'},
    ) is None

    (tmp_path / 'run_result.json').write_text(
        json.dumps({'finalists': ['A', 'B', 'C']}),
        encoding='utf-8',
    )
    assert manager.validate_retrieval_start(
        tmp_path,
        broad,
        {'question': '[ROLE: direct] [FINALIST: A] evidence for A and B'},
    ) == (
        'Each targeted retrieval query must name exactly one locked finalist '
        'identifier, matching its FINALIST tag.'
    )

    (tmp_path / 'run_result.json').write_text(
        json.dumps({'finalists': ['A', 'B', 'D']}),
        encoding='utf-8',
    )
    assert manager.validate_retrieval_start(
        tmp_path,
        broad,
        {'question': '[ROLE: direct] [FINALIST: A] evidence for A'},
    ) == 'The finalist set is runtime-locked and cannot be changed.'


def test_parallel_boundary_reserves_the_fifth_broad_call(tmp_path):
    manager = _manager(tmp_path)
    manager.initialize_state(tmp_path)
    four_completed = [
        {
            'tool': 'mat_sn_search-papers-enhanced',
            'status': 'success',
            'arguments': {'question': f'broad facet {index}'},
        }
        for index in range(4)
    ]
    assert manager.validate_retrieval_start(
        tmp_path,
        four_completed,
        {'question': 'fifth candidate-neutral broad facet'},
    ) is None
    error = manager.validate_retrieval_start(
        tmp_path,
        four_completed,
        {'question': 'premature named targeted query'},
    )
    assert 'write run_result.json' in error


def test_targeted_roles_advance_only_after_symmetric_coverage(tmp_path):
    manager = _manager(tmp_path)
    manager.initialize_state(tmp_path)
    broad = [
        {
            'tool': 'mat_sn_search-papers-enhanced',
            'status': 'success',
            'arguments': {'question': f'broad facet {index}'},
        }
        for index in range(5)
    ]
    (tmp_path / 'run_result.json').write_text(
        json.dumps({'finalists': ['A', 'B', 'C']}),
        encoding='utf-8',
    )
    for finalist in ('A', 'B', 'C'):
        assert manager.validate_retrieval_start(
            tmp_path,
            broad,
            {
                'question': (
                    f'[ROLE: direct] [FINALIST: {finalist}] '
                    f'direct evidence for {finalist}'
                ),
                'page_size': 10,
            },
        ) is None

    error = manager.validate_retrieval_start(
        tmp_path,
        broad,
        {
            'question': (
                '[ROLE: mechanism] [FINALIST: A] mechanism evidence for A'
            ),
            'page_size': 10,
        },
    )
    assert 'expected "adverse"' in error

    assert manager.validate_retrieval_start(
        tmp_path,
        broad,
        {
            'question': (
                '[ROLE: adverse] [FINALIST: A] adverse evidence for A'
            ),
            'page_size': 10,
        },
    ) is None
    duplicate = manager.validate_retrieval_start(
        tmp_path,
        broad,
        {
            'question': (
                '[ROLE: adverse] [FINALIST: A] more adverse evidence for A'
            ),
            'page_size': 10,
        },
    )
    assert 'repeats finalist "A"' in duplicate
