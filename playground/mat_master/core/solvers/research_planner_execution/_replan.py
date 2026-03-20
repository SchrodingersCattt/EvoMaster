"""Replan, execution window, step context and LLM verifier for ``ResearchPlanner``."""

import json
from typing import Any

from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from ....prompts.build_prompt import LANGUAGE_RULE
from ..plan_utils import _extract_json_from_content


class ResearchPlannerReplanMixin:
    """Mixin for replanning, execution window, history summary and step context."""

    def _llm_verify_step_outcome(
        self,
        intent: str,
        result_summary: str,
        *,
        state: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Use LLM to judge whether a step was completed from the reported outcome."""
        prompt = f"""STEP INTENT:
{intent}

REPORTED STEP RESULT:
{result_summary}

Return exactly one JSON object:
{{
  "status": "completed",
  "needs_replan": false,
  "reason": ""
}}

Rules:
- status must be one of: completed, partial, blocked, unclear.
- Mark completed only if the reported result explicitly says the requested deliverable was achieved.
- Mark blocked only if the result explicitly shows failure, fallback-only recovery, or inability to finish the requested deliverable.
- Mark unclear when there is not enough evidence either way.
- Set needs_replan=true only for blocked, or for partial when the step clearly cannot satisfy the plan without revision.
- Do not infer missing files or hidden outputs that are not stated in the reported result.
"""
        dialog = Dialog(
            messages=[
                SystemMessage(
                    content=f"You are a strict step completion validator. Output only JSON.\n\n{LANGUAGE_RULE}"
                ),
                UserMessage(content=prompt),
            ],
            tools=[],
        )
        default = {'status': 'unclear', 'needs_replan': False, 'reason': ''}
        try:
            if state is not None and task_id is not None:
                if not self._consume_turns(state, task_id, 1):
                    self._fail_max_turns_exceeded(task_id, state)
                    return default
            reply = self.agent.llm.query(dialog)
            raw = _extract_json_from_content(reply.content or '')
            if not raw:
                return default
            result = json.loads(raw)
            status = str(result.get('status', 'unclear') or 'unclear').lower()
            if status not in {'completed', 'partial', 'blocked', 'unclear'}:
                status = 'unclear'
            return {
                'status': status,
                'needs_replan': bool(result.get('needs_replan', False)),
                'reason': str(result.get('reason', '') or ''),
            }
        except Exception as e:
            self.logger.debug('Step LLM verifier skipped: %s', e)
            return default

    def _get_next_execution_window(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """Return next batch of pending steps respecting dependencies and window size."""
        steps = plan.get('steps', [])
        resolved_ids = {
            step['step_id']
            for step in steps
            if step.get('status') in ('done', 'failed')
        }
        window: list[dict[str, Any]] = []
        blocked_reasons: list[str] = []
        for step in steps:
            if step.get('status') != 'pending':
                continue
            deps = step.get('depends_on') or []
            unresolved = [dep for dep in deps if dep not in resolved_ids]
            if unresolved:
                blocked_reasons.append(
                    f"step_{step['step_id']} blocked by unresolved deps: {unresolved}"
                )
                continue
            window.append(step)
            if len(window) >= self.window_size:
                break
        self.logger.info(
            '[Planner] ExecutionWindow: resolved_ids=%s, window=[%s], blocked=%d',
            sorted(resolved_ids),
            ', '.join(f"step_{step['step_id']}" for step in window),
            len(blocked_reasons),
        )
        if blocked_reasons:
            self.logger.debug('[Planner] Blocked steps: %s', '; '.join(blocked_reasons))
        if not window:
            pending_count = sum(1 for step in steps if step.get('status') == 'pending')
            self.logger.warning(
                '[Planner] Empty execution window: pending_steps=%d, resolved_ids=%s',
                pending_count,
                sorted(resolved_ids),
            )
        return window

    def _summarize_history(
        self,
        history: list[dict[str, Any]],
        max_summary_len: int = 120,
    ) -> str:
        """Build a text summary of execution history."""
        if not history:
            return '(no steps executed yet)'
        lines = []
        for entry in history:
            step_id = entry.get('step', '?')
            if entry.get('error'):
                err = entry['error']
                if max_summary_len and len(err) > max_summary_len:
                    err = err[:max_summary_len] + '...'
                lines.append(f"  Step {step_id}: FAILED — {err}")
            else:
                summary = entry.get('result_summary', 'done')
                if max_summary_len and len(summary) > max_summary_len:
                    summary = summary[:max_summary_len] + '...'
                lines.append(f"  Step {step_id}: OK — {summary}")
            produced = entry.get('produced_artifacts', [])
            if produced:
                lines.append(f"    Files: {', '.join(produced[-10:])}")
        return '\n'.join(lines)

    def _build_step_context(
        self,
        step: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> str:
        """Build pre-step context injection block for step prompt enrichment.

        Injects ALL previous step results as a structured summary (not just the last
        step), so the executor has full awareness of what has already been done.
        """
        parts: list[str] = []

        history = state.get('history', [])
        if history:
            lines: list[str] = []
            for entry in history:
                prev_id = entry.get('step', '?')
                if entry.get('error'):
                    err = str(entry['error'])[:120]
                    lines.append(f'  Step {prev_id} [FAILED]: {err}')
                else:
                    summary = str(entry.get('result_summary', 'done'))[:2000]
                    lines.append(f'  Step {prev_id} [OK]: {summary}')
                produced = entry.get('produced_artifacts', [])
                if produced:
                    lines.append(f'    Produced files: {", ".join(produced[-10:])}')
            parts.append('[Previous Steps Results]\n' + '\n'.join(lines))

        artifacts = state.get('artifacts') or {}
        journal_raw = artifacts.get('research_journal', '')
        journal_path = (
            journal_raw
            if journal_raw
            else f"{self._planner_artifact_dir(task_id)}/research_journal.md"
        )
        if self._file_io.exists(journal_path):
            try:
                journal_text = self._file_io.read_text(journal_path)
                original_len = len(journal_text)
                if len(journal_text) > 2000:
                    journal_text = '...(truncated)\n' + journal_text[-2000:]
                parts.append(f"[Research Journal (recent)]:\n{journal_text}")
                self.logger.debug(
                    '[Planner] _build_step_context: read journal '
                    'step=%s chars=%d (original=%d) path=%s',
                    step.get('step_id', '?'),
                    len(journal_text),
                    original_len,
                    journal_path,
                )
            except Exception as e:
                self.logger.warning(
                    '[Planner] _build_step_context: failed to read journal: %s',
                    e,
                )

        lit_raw = artifacts.get('literature_index', '')
        lit_path = (
            lit_raw
            if lit_raw
            else f"{self._planner_artifact_dir(task_id)}/literature_index.jsonl"
        )
        if self._file_io.exists(lit_path):
            try:
                lit_content = self._file_io.read_text(lit_path).strip()
                lines = lit_content.splitlines() if lit_content else []
                recent_entries = lines[-20:]
                lit_text = '\n'.join(recent_entries)
                parts.append(
                    f"[Literature Index (recent {len(recent_entries)} entries)]:\n{lit_text}"
                )
                self.logger.debug(
                    '[Planner] _build_step_context: read literature_index '
                    'step=%s entries=%d (total_lines=%d) path=%s',
                    step.get('step_id', '?'),
                    len(recent_entries),
                    len(lines),
                    lit_path,
                )
            except Exception as e:
                self.logger.warning(
                    '[Planner] _build_step_context: failed to read literature_index: %s',
                    e,
                )

        if not parts:
            return ''
        return '\n\n'.join(parts)

    def _get_remaining_steps_text(self, plan: dict[str, Any]) -> str:
        """Build text listing of remaining non-done steps for replan context."""
        steps = plan.get('steps', [])
        remaining = [step for step in steps if step.get('status') != 'done']
        if not remaining:
            return '(none)'
        lines = []
        for step in remaining:
            lines.append(
                f"  Step {step.get('step_id')}: [{step.get('compute_cost', '?')}] {step.get('intent', '')[:100]}"
            )
        return '\n'.join(lines)

    def _llm_replan_check(
        self, state: dict[str, Any], task_id: str, step_result: dict[str, Any]
    ) -> bool:
        """Lightweight LLM check: ask if the latest result warrants replanning."""
        if not self.auto_replan:
            return False
        history_summary = self._summarize_history(state.get('history', []))
        plan = state.get('plan', {})
        remaining = self._get_remaining_steps_text(plan)
        latest = json.dumps(step_result, ensure_ascii=False, default=str)[:500]
        prompt = f"""You are a research planner evaluating whether the current execution plan needs revision.

Goal: {state.get('goal', '')}

Execution history:
{history_summary}

Latest step result:
{latest}

Remaining planned steps:
{remaining}

Question: Based on the latest result, do the remaining steps still make sense, or should the plan be revised?
Answer with a single JSON object: {{"needs_replan": true/false, "reason": "brief explanation"}}"""
        dialog = Dialog(
            messages=[
                SystemMessage(
                    content=f"You are a concise research plan evaluator. Output only JSON.\n\n{LANGUAGE_RULE}"
                ),
                UserMessage(content=prompt),
            ],
            tools=[],
        )
        try:
            if not self._consume_turns(state, task_id, 1):
                self._fail_max_turns_exceeded(task_id, state)
                return False
            reply = self.agent.llm.query(dialog)
            raw = _extract_json_from_content(reply.content or '')
            if raw:
                result = json.loads(raw)
                if result.get('needs_replan'):
                    self.logger.info(
                        '[Planner] LLM replan check: %s',
                        result.get('reason', ''),
                    )
                    return True
        except Exception as e:
            self.logger.debug('LLM replan check failed (non-critical): %s', e)
        return False

    def _needs_replanning(
        self, state: dict[str, Any], step_result: dict[str, Any]
    ) -> tuple[bool, str]:
        """Evaluate whether execution should pause for replanning.

        The returned reason string is prefixed with ``[failure]`` or
        ``[adaptive]`` so that ``_phase_replanning`` can increment the
        correct counter (failure_replan_count / adaptive_replan_count).
        """
        if not self.auto_replan:
            return False, ''
        if (
            self.replan_on_failure
            and step_result.get('status') == 'failed'
            and not step_result.get('fallback_succeeded')
        ):
            detail = str(step_result.get('replan_reason') or '').strip()
            if detail:
                if detail.startswith('[failure]') or detail.startswith('[adaptive]'):
                    return True, detail
                return True, f'[failure] {detail}'
            return (
                True,
                f"[failure] Step {step_result.get('step_id', '?')} failed without successful fallback",
            )
        if self.replan_on_new_skill and step_result.get('new_skill_registered'):
            return (
                True,
                f"[adaptive] New skill registered: {step_result.get('skill_path', 'unknown')}; subsequent steps may benefit",
            )
        if step_result.get('replan_requested'):
            return True, '[failure] ' + step_result.get(
                'replan_reason',
                'explicit replan requested by executor',
            )
        task_id = str(state.get('task_id') or '')
        if task_id and self._llm_replan_check(state, task_id, step_result):
            return True, '[adaptive] LLM heuristic detected plan deviation'
        return False, ''

    def _replan_from_results(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Feed execution results back to planner LLM for mid-flight revision."""
        current_plan = state['plan']
        history_summary = self._summarize_history(state.get('history', []))
        remaining = self._get_remaining_steps_text(current_plan)
        replan_reason = state.get(
            'replan_reason',
            'execution results require path adjustment',
        )
        revision_prompt = (
            'MID-EXECUTION REPLAN REQUEST\n'
            f"Original goal: {goal}\n\n"
            f"Steps completed so far:\n{history_summary}\n\n"
            f"Remaining planned steps:\n{remaining}\n\n"
            f"Reason for replan: {replan_reason}\n\n"
            'Revise the REMAINING steps only. Do NOT modify steps already marked as done.\n'
            'You may add new steps (with step_id after the last existing step) or remove now-unnecessary steps.\n'
            'Output the full revised plan as a single JSON object (same schema: execution_graph, fidelity_level). No other text.'
        )
        return self._revise_plan(
            goal,
            current_plan,
            revision_prompt,
            state=state,
            task_id=task_id,
        )
