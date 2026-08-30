"""Required run-contract loading and deterministic completion checks."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
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
        filtered: list[Any] = []
        seen: set[str] = set()
        for spec in specs:
            name = tool_name(spec)
            if self.tool_allowlist is not None and name not in self.tool_allowlist:
                continue
            if name and name not in seen:
                seen.add(name)
                filtered.append(spec)
        if self.active and self.tool_allowlist is not None:
            missing = sorted(self.tool_allowlist - seen)
            if missing:
                raise RuntimeError(
                    'Required runtime tools were not registered: '
                    + ', '.join(missing)
                )
        return filtered

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
            'Runtime protocol audit\n\n'
            'The runtime derives protocol state from actual retrieval calls, the '
            'evidence matrix, and registered job lifecycles. Do not create, inspect, '
            'or edit internal protocol-state files. Put exactly one locked finalist '
            'identifier in every targeted retrieval query and use the configured '
            'evidence-role text verbatim in the evidence matrix.'
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

    @staticmethod
    def _query_text(entry: dict[str, Any]) -> str:
        arguments = entry.get('arguments') or {}
        if not isinstance(arguments, dict):
            return ''
        parts: list[str] = []
        for key in ('question', 'query', 'words', 'keywords'):
            value = arguments.get(key)
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, list):
                parts.extend(str(item) for item in value)
        return ' '.join(parts)

    @staticmethod
    def _mentions(text: str, finalist: str) -> bool:
        pattern = rf'(?<![A-Za-z0-9]){re.escape(finalist)}(?![A-Za-z0-9])'
        return re.search(pattern, text, re.IGNORECASE) is not None

    @staticmethod
    def _retrieval_parameters(entry: dict[str, Any]) -> dict[str, Any]:
        arguments = entry.get('arguments') or {}
        if not isinstance(arguments, dict):
            return {}
        return {
            key: value
            for key, value in arguments.items()
            if key not in {'question', 'query', 'words', 'keywords'}
        }

    def validate_finish(
        self, workspace: str | Path, journal: list[dict], jobs: Any
    ) -> tuple[list[str], dict[str, Any]]:
        if not self.active:
            return [], {}
        root = Path(workspace)
        state_path = root / '_tmp' / 'protocol_state.json'
        errors: list[str] = []
        required_files = ('evidence_matrix.csv', 'outer_loop_decision.md',
                          'base_host_ga.csv', 'next_batch.csv', 'run_result.json')
        for name in required_files:
            path = root / name
            if not path.is_file() or not path.stat().st_size:
                errors.append(f'missing or empty artifact: {name}')

        result_path = root / 'run_result.json'
        result: dict[str, Any] = {}
        if result_path.is_file() and result_path.stat().st_size:
            try:
                result = json.loads(result_path.read_text(encoding='utf-8'))
            except Exception as exc:
                errors.append(f'invalid run_result.json: {exc}')
        if result.get('abstained') is not True and not isinstance(
            result.get('primary_element'), str
        ):
            errors.append('structured primary_element is missing')
        finalists = result.get('finalists') or []
        if not isinstance(finalists, list):
            finalists = []
        finalists = [str(value).strip() for value in finalists if str(value).strip()]
        if len(set(finalists)) != len(finalists):
            errors.append('finalists must be unique')
        expected_count = int(self.protocol.get('finalist_count') or 0)
        if len(finalists) != expected_count:
            errors.append(f'expected {expected_count} finalists; found {len(finalists)}')

        evidence_rows: list[dict[str, str]] = []
        evidence = root / 'evidence_matrix.csv'
        if evidence.is_file() and evidence.stat().st_size:
            try:
                with evidence.open(encoding='utf-8-sig', newline='') as handle:
                    reader = csv.DictReader(handle)
                    fields = set(reader.fieldnames or [])
                    evidence_rows = list(reader)
                needed = {
                    'source', 'composition', 'dose', 'observation', 'scope',
                    'evidence_role', 'candidate',
                }
                if needed - fields:
                    errors.append(
                        'evidence matrix missing fields: '
                        + ', '.join(sorted(needed - fields))
                    )
                for index, row in enumerate(evidence_rows, 2):
                    missing = [key for key in needed if not str(row.get(key) or '').strip()]
                    if missing:
                        errors.append(
                            f'evidence row {index} has empty fields: '
                            + ', '.join(sorted(missing))
                        )
            except Exception as exc:
                errors.append(f'cannot read evidence matrix: {exc}')

        roles = [str(role) for role in self.protocol.get('finalist_rounds') or []]
        source_n = int(
            self.protocol.get('inspected_records_per_finalist_per_round') or 0
        )
        evidence_sources: dict[str, dict[str, list[str]]] = {}
        for role in roles:
            evidence_sources[role] = {}
            for finalist in finalists:
                rows = [
                    row for row in evidence_rows
                    if str(row.get('evidence_role') or '').strip() == role
                    and str(row.get('candidate') or '').strip() == finalist
                ]
                evidence_sources[role][finalist] = [
                    str(row.get('source') or '').strip() for row in rows
                ]
                if len(rows) != source_n:
                    errors.append(
                        f'evidence role "{role}", finalist "{finalist}" '
                        f'has {len(rows)}/{source_n} inspected records'
                    )
        observed_candidates = {
            str(row.get('candidate') or '').strip()
            for row in evidence_rows if str(row.get('candidate') or '').strip()
        }
        if finalists and observed_candidates != set(finalists):
            errors.append('evidence-matrix candidates differ from locked finalists')

        retrieval_calls = [
            entry for entry in journal
            if str(entry.get('tool', '')).startswith('mat_sn_search-')
            and entry.get('status') == 'success'
        ]
        broad_n = len(self.protocol.get('broad_searches') or [])
        first_targeted = next(
            (
                index for index, entry in enumerate(retrieval_calls)
                if any(
                    self._mentions(self._query_text(entry), finalist)
                    for finalist in finalists
                )
            ),
            len(retrieval_calls),
        )
        broad = retrieval_calls[:first_targeted]
        targeted = retrieval_calls[first_targeted:]
        if len(broad) < broad_n:
            errors.append(f'broad searches incomplete: {len(broad)}/{broad_n}')
            errors.append(
                'named finalist retrieval began before broad searches completed'
            )
        for index, entry in enumerate(broad, 1):
            named = [
                finalist for finalist in finalists
                if self._mentions(self._query_text(entry), finalist)
            ]
            if named:
                errors.append(
                    f'broad search {index} names a locked finalist: {", ".join(named)}'
                )

        query_n = int(self.protocol.get('calls_per_finalist_per_round') or 0)
        group_size = len(finalists) * query_n
        required_targeted = len(roles) * group_size
        if len(targeted) < required_targeted:
            errors.append(
                f'targeted searches incomplete: {len(targeted)}/{required_targeted}'
            )
        if group_size and len(targeted) % group_size:
            errors.append('targeted searches end with an incomplete symmetric round')
        group_count = len(targeted) // group_size if group_size else 0
        if group_count > len(roles) and not self.protocol.get('symmetric_gap_filling'):
            errors.append('unconfigured targeted retrieval rounds were used')

        targeted_counts = {finalist: 0 for finalist in finalists}
        rounds: list[dict[str, Any]] = []
        for group_index in range(group_count):
            chunk = targeted[
                group_index * group_size:(group_index + 1) * group_size
            ]
            role = (
                roles[group_index]
                if group_index < len(roles)
                else f'gap_fill_{group_index - len(roles) + 1}'
            )
            records = {
                finalist: {'query_steps': [], 'inspected_sources': []}
                for finalist in finalists
            }
            parameters = []
            for entry in chunk:
                text = self._query_text(entry)
                matches = [
                    finalist for finalist in finalists
                    if self._mentions(text, finalist)
                ]
                if len(matches) != 1:
                    errors.append(
                        f'targeted round "{role}" query must name exactly one finalist'
                    )
                    continue
                finalist = matches[0]
                targeted_counts[finalist] += 1
                records[finalist]['query_steps'].append(entry.get('step'))
                parameters.append(self._retrieval_parameters(entry))
            for finalist, record in records.items():
                if len(record['query_steps']) != query_n:
                    errors.append(
                        f'targeted round "{role}" is asymmetric for "{finalist}"'
                    )
                if group_index < len(roles):
                    record['inspected_sources'] = evidence_sources.get(role, {}).get(
                        finalist, []
                    )
            if parameters and any(value != parameters[0] for value in parameters[1:]):
                errors.append(
                    f'targeted round "{role}" uses inconsistent retrieval parameters'
                )
            rounds.append({'name': role, 'candidates': records})

        if any(str(entry.get('tool')) == 'monitor_job' for entry in journal):
            errors.append('monitor_job was used in a native-lifecycle run')
        submit_entries = [
            entry for entry in journal
            if str(entry.get('tool', '')).startswith('mat_compdart_submit_')
            and entry.get('status') == 'success'
        ]
        submitted_constraints: list[dict[str, Any]] = []
        if submit_entries:
            arguments = submit_entries[-1].get('arguments') or {}
            if isinstance(arguments, dict):
                values = arguments.get('constraints')
                if isinstance(values, list):
                    submitted_constraints = values
        compdart_config = self.protocol.get('compdart') or {}
        if (
            isinstance(compdart_config, dict)
            and compdart_config.get('require_agent_authored_constraints')
        ):
            valid_constraints = bool(submitted_constraints) and all(
                isinstance(item, dict)
                and isinstance(item.get('target'), (str, list))
                and bool(item.get('target'))
                and isinstance(item.get('condition'), str)
                and bool(item.get('condition').strip())
                for item in submitted_constraints
            )
            if not valid_constraints:
                errors.append(
                    'CompDART submit lacks agent-authored constraints in tool schema'
                )
        compdart_jobs = [
            job for job in getattr(jobs, 'jobs', {}).values()
            if str(job.source_tool).startswith('mat_compdart_submit_')
        ]
        successful_jobs = [
            job for job in compdart_jobs
            if job.lifecycle_state == 'succeeded' and bool(job.results)
        ]
        if not successful_jobs:
            errors.append('no CompDART job completed its native lifecycle')
        chosen_job = successful_jobs[-1] if successful_jobs else None
        dart = {
            'submitted': bool(compdart_jobs),
            'job_id': chosen_job.job_id if chosen_job else None,
            'status': chosen_job.raw_status if chosen_job else None,
            'results_retrieved': chosen_job is not None,
            'submitted_constraints': submitted_constraints,
            'result_payload': chosen_job.results if chosen_job else None,
        }

        state = {
            'contract': {
                key: self.contracts[0][key]
                for key in ('name', 'package', 'entrypoint', 'sha256')
            },
            'protocol_sha256': self.protocol_sha256,
            'phase': 'complete' if not errors else 'synthesis',
            'broad_queries': [
                {
                    'step': entry.get('step'),
                    'arguments': entry.get('arguments') or {},
                }
                for entry in broad
            ],
            'finalists': finalists,
            'rounds': rounds,
            'inspected_sources': evidence_sources,
            'violations': errors,
            'protocol_pass': not errors,
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')

        if result:
            result['finalists'] = finalists
            result['search_calls'] = {
                'broad': len(broad),
                'targeted': targeted_counts,
            }
            result['inspected_sources'] = {
                finalist: sum(
                    len(evidence_sources.get(role, {}).get(finalist, []))
                    for role in roles
                )
                for finalist in finalists
            }
            result['dart'] = dart
            result['protocol_pass'] = not errors
            result.setdefault('usage', {})
            result_path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        return errors, {'protocol_state': str(state_path), 'errors': errors}
