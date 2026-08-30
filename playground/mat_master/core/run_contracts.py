"""Required run-contract loading and deterministic completion checks."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
import threading
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
        self._locked_finalists: tuple[str, ...] | None = None
        self._retrieval_lock = threading.Lock()
        self._retrieval_started_count = 0
        self._targeted_started: list[dict[str, Any]] = []
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

    @staticmethod
    def errors_are_irrecoverable(errors: list[str]) -> bool:
        """Whether immutable journal history makes protocol recovery impossible."""
        markers = (
            'agent inspected or edited runtime-owned protocol state',
            'query must name exactly one finalist',
            'is asymmetric for',
            'uses inconsistent retrieval parameters',
            'named finalist retrieval began before broad searches completed',
            'finalists changed after runtime lock',
            'missing the required [ROLE:',
            'missing the required [FINALIST:',
            'uses evidence role',
            'query has evidence role',
            'query has finalist tag',
            'repeats finalist',
        )
        return any(any(marker in error for marker in markers) for error in errors)

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
        with self._retrieval_lock:
            self._locked_finalists = None
            self._retrieval_started_count = 0
            self._targeted_started = []
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
    def _targeted_tag(text: str, name: str) -> str:
        match = re.search(
            rf'\[{re.escape(name)}:\s*([^\]]+?)\s*\]',
            text,
            re.IGNORECASE,
        )
        return match.group(1).strip() if match else ''

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

    def validate_retrieval_start(
        self,
        workspace: str | Path,
        journal: list[dict[str, Any]],
        arguments: dict[str, Any],
    ) -> str | None:
        """Lock model-selected finalists before the first targeted retrieval."""
        if not self.active:
            return None
        with self._retrieval_lock:
            successful_count = sum(
                str(entry.get('tool', '')).startswith('mat_sn_search-')
                and entry.get('status') == 'success'
                for entry in journal
            )
            self._retrieval_started_count = max(
                self._retrieval_started_count,
                successful_count,
            )
            broad_n = len(self.protocol.get('broad_searches') or [])
            if self._retrieval_started_count < broad_n:
                self._retrieval_started_count += 1
                return None

            entry = {'arguments': arguments}
            query_text = self._query_text(entry)
            if self._locked_finalists is None:
                if query_text.lstrip().lower().startswith('[broad]'):
                    self._retrieval_started_count += 1
                    return None
                result_path = Path(workspace) / 'run_result.json'
                try:
                    result = json.loads(result_path.read_text(encoding='utf-8'))
                except Exception:
                    result = {}
                values = result.get('finalists') if isinstance(result, dict) else None
                finalists = (
                    [str(value).strip() for value in values if str(value).strip()]
                    if isinstance(values, list)
                    else []
                )
                expected = int(self.protocol.get('finalist_count') or 0)
                if len(finalists) != expected or len(set(finalists)) != expected:
                    return (
                        'Before targeted retrieval, write run_result.json with exactly '
                        f'{expected} unique model-selected finalists. Optional '
                        'additional candidate-neutral broad queries must start with '
                        '[BROAD].'
                    )
                self._locked_finalists = tuple(finalists)
                state_path = Path(workspace) / '_tmp' / 'protocol_state.json'
                try:
                    state = json.loads(state_path.read_text(encoding='utf-8'))
                except Exception:
                    state = {}
                state.update(
                    {
                        'phase': 'finalists_locked',
                        'finalists': list(self._locked_finalists),
                    }
                )
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    json.dumps(state, indent=2) + '\n',
                    encoding='utf-8',
                )
            else:
                result_path = Path(workspace) / 'run_result.json'
                try:
                    current = json.loads(result_path.read_text(encoding='utf-8'))
                    values = current.get('finalists')
                    current_finalists = tuple(
                        str(value).strip() for value in values if str(value).strip()
                    )
                except Exception:
                    current_finalists = ()
                if current_finalists != self._locked_finalists:
                    return 'The finalist set is runtime-locked and cannot be changed.'

            tagged_role = self._targeted_tag(query_text, 'ROLE')
            tagged_finalist = self._targeted_tag(query_text, 'FINALIST')
            roles = [str(role) for role in self.protocol.get('finalist_rounds') or []]
            query_n = int(
                self.protocol.get('calls_per_finalist_per_round') or 0
            )
            group_size = len(self._locked_finalists) * query_n
            group_index = (
                len(self._targeted_started) // group_size if group_size else 0
            )
            expected_role = (
                roles[group_index] if group_index < len(roles) else 'gap-fill'
            )
            if not tagged_role:
                return (
                    'Targeted retrieval is missing the required '
                    f'[ROLE: {expected_role}] tag.'
                )
            if tagged_role.casefold() != expected_role.casefold():
                return (
                    f'Targeted retrieval uses evidence role "{tagged_role}"; '
                    f'expected "{expected_role}".'
                )
            if not tagged_finalist:
                return (
                    'Targeted retrieval is missing the required '
                    '[FINALIST: <locked identifier>] tag.'
                )
            canonical = next(
                (
                    finalist for finalist in self._locked_finalists
                    if finalist.casefold() == tagged_finalist.casefold()
                ),
                None,
            )
            if canonical is None:
                return (
                    f'Targeted retrieval names unlocked finalist '
                    f'"{tagged_finalist}".'
                )
            matches = [
                finalist for finalist in self._locked_finalists
                if self._mentions(query_text, finalist)
            ]
            if len(matches) != 1 or matches[0] != canonical:
                return (
                    'Each targeted retrieval query must name exactly one locked '
                    'finalist identifier, matching its FINALIST tag.'
                )

            current_group = (
                self._targeted_started[-(len(self._targeted_started) % group_size):]
                if group_size and len(self._targeted_started) % group_size
                else []
            )
            repeated = sum(
                entry['finalist'] == canonical for entry in current_group
            )
            if repeated >= query_n:
                return (
                    f'Targeted round "{expected_role}" repeats finalist '
                    f'"{canonical}" before covering every finalist.'
                )
            parameters = self._retrieval_parameters({'arguments': arguments})
            if current_group and parameters != current_group[0]['parameters']:
                return (
                    f'Targeted round "{expected_role}" uses inconsistent '
                    'retrieval parameters.'
                )
            self._targeted_started.append(
                {
                    'role': expected_role,
                    'finalist': canonical,
                    'parameters': parameters,
                }
            )
            self._retrieval_started_count += 1
            return None

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
        if (
            self._locked_finalists is not None
            and tuple(finalists) != self._locked_finalists
        ):
            errors.append('finalists changed after runtime lock')
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
                tagged_role = self._targeted_tag(text, 'ROLE')
                tagged_finalist = self._targeted_tag(text, 'FINALIST')
                expected_tag_role = role if group_index < len(roles) else 'gap-fill'
                if tagged_role.casefold() != expected_tag_role.casefold():
                    errors.append(
                        f'targeted round "{role}" query has evidence role '
                        f'"{tagged_role or "(missing)"}"'
                    )
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
                if tagged_finalist.casefold() != finalist.casefold():
                    errors.append(
                        f'targeted round "{role}" query has finalist tag '
                        f'"{tagged_finalist or "(missing)"}", expected "{finalist}"'
                    )
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

        protected_state_access = []
        for entry in journal:
            arguments = entry.get('arguments') or {}
            serialized = json.dumps(arguments, sort_keys=True, default=str)
            if (
                '_tmp/protocol_state.json' in serialized
                or '_tmp/execution_journal' in serialized
            ):
                protected_state_access.append(
                    {'step': entry.get('step'), 'tool': entry.get('tool')}
                )
        if protected_state_access:
            errors.append(
                'agent inspected or edited runtime-owned protocol state: '
                + json.dumps(protected_state_access, sort_keys=True)
            )
        if any(str(entry.get('tool')) == 'monitor_job' for entry in journal):
            errors.append('monitor_job was used in a native-lifecycle run')
        submit_entries = [
            entry for entry in journal
            if str(entry.get('tool', '')).startswith('mat_compdart_submit_')
            and entry.get('status') == 'success'
        ]
        submitted_constraints: list[dict[str, Any]] = []
        unconstrained_submit_steps: list[Any] = []
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
            for entry in submit_entries:
                arguments = entry.get('arguments') or {}
                values = (
                    arguments.get('constraints')
                    if isinstance(arguments, dict)
                    else None
                )
                valid = bool(values) and all(
                    isinstance(item, dict)
                    and isinstance(item.get('target'), (str, list))
                    and bool(item.get('target'))
                    and isinstance(item.get('condition'), str)
                    and bool(item.get('condition').strip())
                    for item in values
                )
                if not valid:
                    unconstrained_submit_steps.append(entry.get('step'))
            if unconstrained_submit_steps:
                errors.append(
                    'CompDART submit omitted required agent-authored constraints '
                    'at steps: ' + ', '.join(map(str, unconstrained_submit_steps))
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
