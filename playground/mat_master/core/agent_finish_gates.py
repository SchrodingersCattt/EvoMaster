"""Finish precheck and LLM quality gate for MatMasterAgent."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from .agent_finish_message import extract_json_from_reply
from .step_verifier import StepContract, verify_step_deterministic

_EXPECTED_ARTIFACT_RE = re.compile(
    r"(?<![\w/.-])([A-Za-z0-9_.-]+\.(?:json|md|csv|txt|yaml|yml|cif|xyz|png|pdf))\b",
    re.IGNORECASE,
)
_ASYNC_TASK_HINTS = (
    'dart ga',
    'ga optimization',
    'run ga',
    'run a ga',
    'pareto',
    'surrogate model',
    'optimization campaign',
)


class MatMasterFinishGatesMixin:
    """Mixin: ``_precheck_finish_gates`` and ``_llm_finish_gate_check``."""

    @staticmethod
    def _extract_expected_artifacts(task_description: str) -> list[str]:
        seen: set[str] = set()
        artifacts: list[str] = []
        for name in _EXPECTED_ARTIFACT_RE.findall(task_description or ''):
            if name not in seen:
                seen.add(name)
                artifacts.append(name)
        return artifacts[:50]

    @staticmethod
    def _artifact_basenames(paths: list[str], limit: int = 20) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            name = Path(raw).name or str(raw)
            if name and name not in seen:
                seen.add(name)
                names.append(name)
            if len(names) >= limit:
                break
        return names

    def _collect_finish_gate_evidence(
        self, task_description: str, workspace_path: str
    ) -> dict[str, Any]:
        workspace = Path(workspace_path).resolve() if workspace_path else None
        workspace_files: list[str] = []
        if workspace and workspace.exists():
            try:
                workspace_files = sorted(
                    p.name for p in workspace.iterdir() if p.is_file()
                )[:50]
            except Exception:
                workspace_files = []

        journal = getattr(self, '_execution_journal', None)
        journal_entries = journal.entries if journal is not None else []
        produced_paths: list[str] = []
        seen_paths: set[str] = set()
        successful_tools: dict[str, int] = {}
        for entry in journal_entries:
            tool = str(entry.get('tool') or '').strip()
            status = str(entry.get('status') or '').strip()
            if tool and status == 'success':
                successful_tools[tool] = successful_tools.get(tool, 0) + 1

            saved_path = entry.get('saved_path') or entry.get('auto_saved_path')
            if isinstance(saved_path, str) and saved_path and saved_path not in seen_paths:
                seen_paths.add(saved_path)
                produced_paths.append(saved_path)

            for item in entry.get('downloaded_files') or []:
                if isinstance(item, str):
                    local_path = item
                elif isinstance(item, dict):
                    local_path = item.get('local_path') or item.get('path') or ''
                else:
                    local_path = ''
                if local_path and local_path not in seen_paths:
                    seen_paths.add(local_path)
                    produced_paths.append(local_path)

        expected_artifacts = self._extract_expected_artifacts(task_description)
        deterministic = {
            'artifact_match': False,
            'produced_artifacts': [],
            'missing_artifacts': [],
            'completion_ratio': 1.0,
            'drift_reason': '',
        }
        if expected_artifacts:
            deterministic = verify_step_deterministic(
                StepContract(expected_artifacts=expected_artifacts),
                workspace or Path('.'),
                produced_files=produced_paths,
                journal_entries=journal_entries,
            )

        task_lower = (task_description or '').lower()
        has_successful_submit = any(
            '_submit_' in tool for tool in successful_tools
        )
        task_mentions_async_work = any(hint in task_lower for hint in _ASYNC_TASK_HINTS)

        return {
            'workspace_path': str(workspace) if workspace else '',
            'workspace_files': workspace_files,
            'journal_entries': len(journal_entries),
            'produced_artifacts': self._artifact_basenames(produced_paths),
            'expected_artifacts': expected_artifacts,
            'deterministic': deterministic,
            'successful_tools': successful_tools,
            'has_successful_submit': has_successful_submit,
            'task_mentions_async_work': task_mentions_async_work,
        }

    def _precheck_finish_gates(
        self, requested_task_completed: str
    ) -> tuple[list[str], dict[str, Any]]:
        """Validate finish gates before executing the finish tool.

        Quality gates (manuscript, survey) are subject to a safety cap: after
        ``finish_block_max`` consecutive blocks the gates are force-passed so
        the agent never loops indefinitely. Async-job gates are **not** capped
        because orphan jobs can leak resources.
        """
        blocked_msgs: list[str] = []
        gate_info: dict[str, Any] = {}
        if requested_task_completed not in ('true', 'partial'):
            return blocked_msgs, gate_info

        run_contracts = getattr(self, '_run_contracts', None)
        if run_contracts is not None and run_contracts.active:
            workspace = getattr(self.session.config, 'workspace_path', '') or ''
            journal = getattr(self, '_execution_journal', None)
            errors, contract_info = run_contracts.validate_finish(
                workspace,
                journal.entries if journal is not None else [],
                self._job_registry,
            )
            if errors:
                if run_contracts.errors_are_irrecoverable(errors):
                    contract_info['irrecoverable_protocol_failure'] = True
                    gate_info.update(contract_info)
                else:
                    self._finish_block_count += 1
                    contract_info['finish_block_count'] = self._finish_block_count
                    return (
                        [
                            '[run_contract_gate] Blocked: '
                            + '; '.join(errors[:12])
                        ],
                        contract_info,
                    )

        self._job_registry.refresh_pending()
        can_finish, job_gate_info = self._job_registry.can_finish()
        gate_info.update(job_gate_info)

        # When task_completed='partial' the agent is explicitly acknowledging that work
        # is incomplete (e.g. job still running), so the pending-jobs gate is skipped.
        # Only block on pending jobs when the agent claims full completion ('true').
        if not can_finish and requested_task_completed == 'true':
            blocked_msgs.append(
                '[finish_attempt_gate] Blocked: pending async jobs still running. '
                'Continue monitoring until pending_jobs_check passes, '
                "or finish with task_completed='partial' to yield while the job runs."
            )

        if gate_info.get('irrecoverable_protocol_failure'):
            return blocked_msgs, gate_info

        force_pass = self._finish_block_count >= self._finish_block_max
        if force_pass:
            self.logger.warning(
                '[finish_attempt_gate] Quality gates force-passed after %d '
                'consecutive blocks (cap=%d).',
                self._finish_block_count,
                self._finish_block_max,
            )
            gate_info['finish_force_passed'] = True
        else:
            workspace = getattr(self.session.config, 'workspace_path', '') or ''
            task_description = getattr(self, '_current_task_description', '')

            _is_planner_step = (
                '[Task of This Step]' in task_description
                or '[Original Intent]' in task_description
            )
            if _is_planner_step:
                self.logger.debug(
                    '[finish_attempt_gate] Planner step detected — '
                    'skipping LLM gate (planner already verified completion).'
                )
            else:
                gate_eval = self._llm_finish_gate_check(
                    task_description=task_description,
                    requested_task_completed=requested_task_completed,
                    workspace_path=workspace,
                )

                if not gate_eval.get('approved', False):
                    reason = gate_eval.get(
                        'reason', 'Task completion requirements not met'
                    )
                    blocked_msgs.append(
                        f'[finish_attempt_gate] Blocked: {reason}\n'
                        f'Task: {task_description[:100]}{"..." if len(task_description) > 100 else ""}'
                    )
        if blocked_msgs:
            self._finish_block_count += 1
            gate_info['finish_block_count'] = self._finish_block_count
            gate_info['finish_block_max'] = self._finish_block_max
        else:
            self._finish_block_count = 0

        if blocked_msgs and requested_task_completed == 'true':
            gate_info.setdefault(
                'finish_hint',
                (
                    'If you are blocked by unavailable/paywalled web sources (403/404/etc.), '
                    "switch to alternative open sources or finish with task_completed='partial' "
                    'and include explicit limitations/caveats.'
                ),
            )

        return blocked_msgs, gate_info

    def _llm_finish_gate_check(
        self,
        task_description: str,
        requested_task_completed: str,
        workspace_path: str,
    ) -> dict[str, Any]:
        """Use deterministic workspace evidence before asking an LLM to judge finish."""
        if not task_description or not task_description.strip():
            return {'approved': True, 'reason': ''}

        evidence = self._collect_finish_gate_evidence(task_description, workspace_path)
        deterministic = evidence['deterministic']
        missing_artifacts = deterministic.get('missing_artifacts') or []
        if missing_artifacts:
            missing_str = ', '.join(missing_artifacts[:10])
            return {
                'approved': False,
                'reason': f'Requested artifacts missing in workspace: {missing_str}',
            }

        if evidence['expected_artifacts'] and deterministic.get('artifact_match', False):
            if (
                not evidence['task_mentions_async_work']
                or evidence['has_successful_submit']
            ):
                return {
                    'approved': True,
                    'reason': '',
                    'approval_source': 'deterministic_workspace_evidence',
                }

        tool_lines = [
            f'{tool} x{count}'
            for tool, count in sorted(evidence['successful_tools'].items())[:20]
        ]
        prompt = f"""TASK DESCRIPTION:
{task_description}

USER REQUESTED: task_completed={requested_task_completed}

WORKSPACE EVIDENCE:
- Workspace path: {evidence['workspace_path'] or '(unknown)'}
- Top-level workspace files: {', '.join(evidence['workspace_files']) if evidence['workspace_files'] else '(none detected)'}
- Expected artifacts parsed from task description: {', '.join(evidence['expected_artifacts']) if evidence['expected_artifacts'] else '(none explicitly named)'}
- Deterministic artifact check: artifact_match={deterministic.get('artifact_match', False)}, completion_ratio={deterministic.get('completion_ratio', 1.0):.2f}, missing={deterministic.get('missing_artifacts', [])}
- Produced artifacts recorded by execution journal: {', '.join(evidence['produced_artifacts']) if evidence['produced_artifacts'] else '(none recorded)'}
- Successful tools recorded by execution journal: {', '.join(tool_lines) if tool_lines else '(none recorded)'}
- Successful async submit observed: {evidence['has_successful_submit']}

Question: Has the user's requested task been accomplished?
- Do NOT gate on mandatory manuscript validation or survey markdown quality.
- Treat the workspace evidence above as the source of truth.
- Only block if the core deliverable requested by the user is clearly missing or incomplete.
- Be permissive: if the task is substantially done, return approved=true.

Return exactly one JSON object:
{{
  "approved": true,
  "reason": ""
}}

If NOT approved, reason should be specific (e.g. "Requested CSV file not found in workspace").
"""

        dialog = Dialog(
            messages=[
                SystemMessage(
                    content='You are a strict task completion validator. Output only JSON. Use the provided workspace evidence as the source of truth. Do not require manuscript quality gates or survey markdown gates unless the user explicitly asked for a written document.'
                ),
                UserMessage(content=prompt),
            ],
            tools=[],
        )

        default = {'approved': True, 'reason': ''}
        try:
            reply = self.llm.query(dialog)
            raw = extract_json_from_reply(reply.content or '')
            if not raw:
                return default
            result = json.loads(raw)
            approved = bool(result.get('approved', True))
            reason = str(result.get('reason', '') or '')
            return {'approved': approved, 'reason': reason}
        except Exception as e:
            self.logger.debug('LLM finish gate check failed: %s', e)
            return default
