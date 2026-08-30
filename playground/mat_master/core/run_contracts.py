"""Required run-contract loading and deterministic completion checks."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tool_name(spec: Any) -> str:
    function = getattr(spec, 'function', None)
    if function is not None:
        return str(getattr(function, 'name', '') or '')
    if isinstance(spec, dict):
        return str((spec.get('function') or {}).get('name') or '')
    return ''


class RunContractManager:
    """Resolve required contracts before the first LLM request."""

    def __init__(self, config: dict | None, skills: Any, config_dir: Any) -> None:
        self.config = config or {}
        general = ((self.config.get('agents') or {}).get('general') or {})
        self.prompt_profile = str(general.get('prompt_profile') or 'legacy')
        self.execution_mode = str(general.get('execution_mode') or 'auto')
        allowed = general.get('runtime_tool_allowlist')
        self.tool_allowlist = (
            frozenset(map(str, allowed))
            if isinstance(allowed, list) and allowed
            else None
        )
        self.config_dir = Path(config_dir or '.').resolve()
        self.contracts = self._resolve_contracts(
            general.get('required_contracts') or [], skills
        )
        self.protocol: dict[str, Any] = {}
        self.protocol_sha256 = ''
        protocol_file = general.get('contract_config_file')
        if protocol_file:
            path = Path(str(protocol_file))
            if not path.is_absolute():
                path = self.config_dir / path
            path = path.resolve()
            if not path.is_file():
                raise FileNotFoundError(f'Required protocol file not found: {path}')
            raw = path.read_bytes()
            loaded = yaml.safe_load(raw.decode('utf-8')) or {}
            if not isinstance(loaded, dict):
                raise ValueError('Protocol file must contain a YAML mapping')
            self.protocol = loaded.get('protocol') or loaded
            if not isinstance(self.protocol, dict):
                raise ValueError('protocol must be a YAML mapping')
            self.protocol_sha256 = _sha256(raw)
        if self.prompt_profile == 'scoped' and not self.contracts:
            raise ValueError('Scoped prompt profile requires a loaded run contract')

    @property
    def active(self) -> bool:
        return self.prompt_profile == 'scoped' and bool(self.contracts)

    @staticmethod
    def _resolve_contracts(items: list, skills: Any) -> list[dict[str, str]]:
        resolved: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('required_contracts entries must be mappings')
            package = str(item.get('package') or '').strip()
            entrypoint = str(item.get('entrypoint') or '').strip()
            skill = skills.get_skill(package) if skills is not None else None
            if not package or not entrypoint or skill is None:
                raise FileNotFoundError(
                    f'Required contract package is not loaded: {package or "(empty)"}'
                )
            root = Path(skill.skill_path).resolve()
            path = (root / entrypoint).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError('Contract entrypoint escapes its package') from exc
            if not path.is_file():
                raise FileNotFoundError(f'Required contract not found: {path}')
            raw = path.read_bytes()
            resolved.append(
                {
                    'name': path.stem.replace('_', '-'),
                    'package': package,
                    'entrypoint': entrypoint,
                    'path': str(path),
                    'sha256': _sha256(raw),
                    'content': raw.decode('utf-8').strip(),
                }
            )
        return resolved

    def filter_specs(self, specs: list[Any]) -> list[Any]:
        if self.tool_allowlist is None:
            return specs
        return [spec for spec in specs if tool_name(spec) in self.tool_allowlist]

    def tool_is_allowed(self, name: str) -> bool:
        return self.tool_allowlist is None or name in self.tool_allowlist

    def capabilities_text(self, specs: list[Any], async_registry: Any) -> str:
        names = sorted(filter(None, (tool_name(spec) for spec in specs)))
        retrieval = [
            n for n in names
            if n.startswith('mat_sn_') or n == 'extract_info_from_webpage'
        ]
        workspace = [
            n for n in names
            if n in {'execute_bash', 'str_replace_editor', 'peek_file', 'finish'}
        ]
        service = [n for n in names if n not in retrieval + workspace]
        lines = ['Runtime capabilities']
        for heading, values in (
            ('Retrieval', retrieval),
            ('Registered services', service),
            ('Workspace', workspace),
        ):
            if values:
                lines += ['', heading, *[f'- {name}' for name in values]]
        for entry in getattr(async_registry, 'entries', []):
            prefix = entry.server_prefix
            submits = [n for n in names if n.startswith(prefix + '_submit_')]
            if not submits or not entry.native_lifecycle:
                continue
            lines += ['', f'{entry.software_name} lifecycle']
            lines += [f'- submit: {name}' for name in submits]
            for suffix, label in (
                ('_query_job_status', 'status'),
                ('_get_job_results', 'results'),
            ):
                name = prefix + suffix
                if name in names:
                    lines.append(f'- {label}: {name}')
        lines += ['', 'Required run contracts']
        lines += [
            f"- {c['package']}/{c['entrypoint']} (sha256: {c['sha256']})"
            for c in self.contracts
        ]
        return '\n'.join(lines)

    def contract_text(self) -> str:
        sections = [
            f"Loaded run contract: {c['package']}/{c['entrypoint']}\n"
            f"SHA-256: {c['sha256']}\n\n{c['content']}"
            for c in self.contracts
        ]
        if self.protocol:
            sections.append(
                'Run-contract configuration\n\n'
                + yaml.safe_dump(
                    {'protocol': self.protocol},
                    sort_keys=False,
                    allow_unicode=True,
                ).strip()
            )
        sections.append(
            'Protocol state\n\n'
            'Maintain "_tmp/protocol_state.json" as the single internal state file. '
            'Record tool-call step numbers for broad and targeted queries. Lock the '
            'finalists before targeted rounds, and record each round as a candidates '
            'mapping with query_steps and inspected_sources. Set phase to "complete" '
            'only after every required artifact has been saved.'
        )
        return '\n\n'.join(sections)

    def initialize_state(self, workspace: str | Path) -> None:
        if not self.active:
            return
        path = Path(workspace) / '_tmp' / 'protocol_state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        expected = {
            'name': self.contracts[0]['name'],
            'package': self.contracts[0]['package'],
            'entrypoint': self.contracts[0]['entrypoint'],
            'sha256': self.contracts[0]['sha256'],
        }
        if path.exists():
            current = json.loads(path.read_text(encoding='utf-8'))
            if (current.get('contract') or {}).get('sha256') != expected['sha256']:
                raise ValueError('Protocol state contract hash mismatch')
            return
        state = {
            'contract': expected,
            'protocol_sha256': self.protocol_sha256,
            'phase': 'broad',
            'broad_queries': [],
            'finalists': [],
            'rounds': [],
            'inspected_sources': [],
            'violations': [],
            'protocol_pass': False,
        }
        path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')

    def validate_finish(
        self, workspace: str | Path, journal: list[dict], jobs: Any
    ) -> tuple[list[str], dict[str, Any]]:
        if not self.active:
            return [], {}
        root = Path(workspace)
        state_path = root / '_tmp' / 'protocol_state.json'
        errors: list[str] = []
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except Exception as exc:
            return [f'Cannot read protocol state: {exc}'], {}
        if (state.get('contract') or {}).get('sha256') != self.contracts[0]['sha256']:
            errors.append('contract hash differs from the startup manifest')
        if state.get('protocol_sha256') != self.protocol_sha256:
            errors.append('protocol hash differs from the startup manifest')
        if state.get('phase') != 'complete':
            errors.append('protocol phase is not complete')
        if state.get('violations'):
            errors.append('protocol has unresolved violations')

        broad = state.get('broad_queries') or []
        expected_broad = self.protocol.get('broad_searches') or []
        if len(broad) < len(expected_broad):
            errors.append(f'broad searches incomplete: {len(broad)}/{len(expected_broad)}')
        finalists = state.get('finalists') or []
        expected_count = int(self.protocol.get('finalist_count') or 0)
        if len(finalists) != expected_count:
            errors.append(f'expected {expected_count} finalists; found {len(finalists)}')

        broad_steps = [
            int(q['step']) for q in broad
            if isinstance(q, dict) and str(q.get('step', '')).isdigit()
        ]
        if len(broad_steps) != len(broad):
            errors.append('every broad query must record its tool-call step')
        targeted_steps: list[int] = []
        rounds = {
            str(r.get('name')): r
            for r in state.get('rounds') or []
            if isinstance(r, dict)
        }
        query_n = int(self.protocol.get('calls_per_finalist_per_round') or 0)
        source_n = int(
            self.protocol.get('inspected_records_per_finalist_per_round') or 0
        )
        for role in self.protocol.get('finalist_rounds') or []:
            candidates = (rounds.get(str(role)) or {}).get('candidates') or {}
            if set(candidates) != set(finalists):
                errors.append(f'round "{role}" does not cover every finalist')
                continue
            for finalist in finalists:
                record = candidates.get(finalist) or {}
                queries = record.get('query_steps') or []
                sources = record.get('inspected_sources') or []
                if len(queries) != query_n or len(sources) != source_n:
                    errors.append(
                        f'round "{role}", finalist "{finalist}" is asymmetric'
                    )
                numeric_steps = [
                    int(step) for step in queries if str(step).isdigit()
                ]
                if len(numeric_steps) != len(queries):
                    errors.append(
                        f'round "{role}", finalist "{finalist}" lacks query steps'
                    )
                targeted_steps += numeric_steps
        if broad_steps and targeted_steps and max(broad_steps) >= min(targeted_steps):
            errors.append('targeted retrieval started before broad retrieval finished')

        required_files = (
            'evidence_matrix.csv',
            'outer_loop_decision.md',
            'base_host_ga.csv',
            'next_batch.csv',
            'run_result.json',
        )
        for name in required_files:
            path = root / name
            if not path.is_file() or not path.stat().st_size:
                errors.append(f'missing or empty artifact: {name}')
        evidence = root / 'evidence_matrix.csv'
        if evidence.is_file() and evidence.stat().st_size:
            with evidence.open(encoding='utf-8-sig', newline='') as handle:
                fields = set(csv.DictReader(handle).fieldnames or [])
            needed = {'source', 'composition', 'dose', 'observation', 'scope'}
            if needed - fields:
                errors.append(
                    'evidence matrix missing fields: ' + ', '.join(sorted(needed - fields))
                )

        result_path = root / 'run_result.json'
        result: dict[str, Any] = {}
        if result_path.is_file() and result_path.stat().st_size:
            try:
                result = json.loads(result_path.read_text(encoding='utf-8'))
                if result.get('abstained') is not True and not isinstance(
                    result.get('primary_element'), str
                ):
                    errors.append('structured primary_element is missing')
                for key in (
                    'finalists', 'search_calls', 'inspected_sources', 'dart', 'usage'
                ):
                    if key not in result:
                        errors.append(f'run_result.json missing {key}')
                dart = result.get('dart') or {}
                if (
                    dart.get('submitted') is not True
                    or not isinstance(dart.get('job_id'), str)
                    or dart.get('results_retrieved') is not True
                ):
                    errors.append('run_result.json lacks a completed CompDART lifecycle')
                usage = result.get('usage') or {}
                if not all(
                    isinstance(usage.get(key), int)
                    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                ):
                    errors.append('run_result.json usage fields must be integers')
            except Exception as exc:
                errors.append(f'invalid run_result.json: {exc}')
        retrieval_calls = [
            entry for entry in journal
            if str(entry.get('tool', '')).startswith('mat_sn_search-')
        ]
        if len(retrieval_calls) < len(broad_steps) + len(targeted_steps):
            errors.append('protocol state contains unobserved retrieval calls')
        if any(str(entry.get('tool')) == 'monitor_job' for entry in journal):
            errors.append('monitor_job was used in a native-lifecycle run')
        compdart_jobs = [
            job for job in getattr(jobs, 'jobs', {}).values()
            if str(job.source_tool).startswith('mat_compdart_submit_')
        ]
        if not compdart_jobs:
            errors.append('no successful CompDART submission was recorded')
        for job in compdart_jobs:
            if str(job.source_tool).startswith('mat_compdart_submit_'):
                if job.lifecycle_state != 'succeeded' or job.results is None:
                    errors.append(f'CompDART job {job.job_id} has no native result')

        if not errors:
            state['protocol_pass'] = True
            state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
            result['protocol_pass'] = True
            result_path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        return errors, {'protocol_state': str(state_path), 'errors': errors}
