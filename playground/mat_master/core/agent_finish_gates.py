"""Finish precheck and LLM quality gate for MatMasterAgent."""

from __future__ import annotations

import json
from typing import Any

from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from .agent_finish_message import extract_json_from_reply


class MatMasterFinishGatesMixin:
    """Mixin: ``_precheck_finish_gates`` and ``_llm_finish_gate_check``."""

    def _precheck_finish_gates(
        self, requested_task_completed: str
    ) -> tuple[list[str], dict[str, Any]]:
        """Validate finish gates before executing the finish tool.

        Quality gates (manuscript, survey) are subject to a safety cap: after
        ``finish_block_max`` consecutive blocks the gates are force-passed so
        the agent never loops indefinitely.  Async-job gates are **not** capped
        because orphan jobs can leak resources.
        """
        blocked_msgs: list[str] = []
        gate_info: dict[str, Any] = {}
        if requested_task_completed not in ('true', 'partial'):
            return blocked_msgs, gate_info

        self._job_registry.refresh_pending()
        can_finish, gate_info = self._job_registry.can_finish()

        # When task_completed='partial' the agent is explicitly acknowledging that work
        # is incomplete (e.g. job still running), so the pending-jobs gate is skipped.
        # Only block on pending jobs when the agent claims full completion ('true').
        if not can_finish and requested_task_completed == 'true':
            blocked_msgs.append(
                '[finish_attempt_gate] Blocked: pending async jobs still running. '
                'Continue monitoring until pending_jobs_check passes, '
                "or finish with task_completed='partial' to yield while the job runs."
            )

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
        """Use LLM to decide whether finish is appropriate given task requirements."""
        if not task_description or not task_description.strip():
            return {'approved': True, 'reason': ''}

        prompt = f"""TASK DESCRIPTION:
{task_description}

USER REQUESTED: task_completed={requested_task_completed}

Question: Has the user's requested task been accomplished?
- Do NOT gate on mandatory manuscript validation or survey markdown quality.
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
                    content='You are a strict task completion validator. Output only JSON. Do not require manuscript quality gates or survey markdown gates unless the user explicitly asked for a written document.'
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
