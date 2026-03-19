"""Phase machine and entrypoint for ``ResearchPlanner``."""

import json
import re
from typing import Any, Optional

from evomaster.utils.types import TaskInstance

from ..execution import BatchExecutor, ExecutionTask
from .direct_solver import DirectSolver
from .plan_utils import _is_deg_plan, _plan_to_external_schema


class ResearchPlannerPhaseMixin:
    def _phase_pre_check(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Assess readiness before planning and run prerequisites if needed."""
        self.logger.info('[Planner] Pre-check: assessing readiness for: %s', goal[:80])
        self._emit('Planner', 'phase_change', {'from': 'init', 'to': 'pre_check'})

        assessment = self._assess_readiness(goal, state, task_id)
        prerequisites = assessment.get('prerequisites') or []
        reasoning = assessment.get('reasoning', '')

        if assessment.get('ready_to_plan'):
            hint_context = (
                json.dumps(prerequisites, ensure_ascii=False) if prerequisites else ''
            )
            self.logger.info('[Pre-check] Ready to plan: %s', reasoning)
            self._emit('Planner', 'thought', f"[Pre-check] Ready to plan. {reasoning}")
            self._append_journal(
                state,
                task_id,
                phase='pre_check',
                title='Ready to plan',
                body=reasoning,
            )
            if hint_context:
                state['pre_check_context'] = hint_context
            state['phase'] = 'planning'
            self._emit(
                'Planner', 'phase_change', {'from': 'pre_check', 'to': 'planning'}
            )
            return state

        self.logger.info(
            '[Pre-check] Prerequisites needed (%d): %s',
            len(prerequisites),
            reasoning,
        )
        self._emit(
            'Planner',
            'thought',
            f"[Pre-check] Not ready to plan yet. {reasoning}\n"
            f"Running {len(prerequisites)} prerequisite(s) first...",
        )
        self._append_journal(
            state,
            task_id,
            phase='pre_check',
            title='Prerequisites required',
            body=f"{reasoning}\ncount={len(prerequisites)}",
        )

        pre_check_context = self._execute_prerequisites(
            prerequisites, goal, task_id, state
        )
        state['pre_check_context'] = pre_check_context

        self.logger.info('[Pre-check] Prerequisites completed. Proceeding to planning.')
        self._emit(
            'Planner',
            'thought',
            '[Pre-check] Prerequisites completed. Now generating plan with enriched context.',
        )
        self._append_journal(
            state,
            task_id,
            phase='pre_check',
            title='Prerequisites completed',
            body=pre_check_context[:2000],
        )
        state['phase'] = 'planning'
        self._emit('Planner', 'phase_change', {'from': 'pre_check', 'to': 'planning'})
        return state

    def _phase_planning(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Generate the initial plan or reuse the existing resumable one."""
        plan = state.get('plan')
        if _is_deg_plan(plan) and state.get('goal') == goal:
            checked = self._enforce_manuscript_plan_consistency(plan, goal)
            if checked.get('status') != 'REFUSED':
                state['plan'] = checked
                state['phase'] = 'preflight'
                return state
            self.logger.info(
                '[Planner] Existing plan rejected by consistency guard; regenerating. reason=%s',
                checked.get('refusal_reason', 'unknown'),
            )

        enriched_goal = goal
        pre_check_context = state.get('pre_check_context', '')
        if pre_check_context:
            enriched_goal = (
                f"{goal}\n\n"
                '# PRE-CHECK RESULTS (extracted context — use this to make a more precise plan)\n'
                f"{pre_check_context}"
            )

        self.logger.info('[Planner] Designing flight plan for: %s', goal[:80])
        plan = self._generate_plan(enriched_goal, state=state, task_id=task_id)

        max_auto_fix = 2
        for attempt in range(max_auto_fix):
            if plan.get('status') != 'REFUSED':
                break
            reason = plan.get('refusal_reason', 'Unknown')
            self.logger.warning(
                '[CRP] Plan refused (attempt %d/%d): %s',
                attempt + 1,
                max_auto_fix,
                reason,
            )
            self._emit(
                'Planner',
                'thought',
                f"[CRP] Plan was refused: {reason}. Auto-revising (attempt {attempt + 1}/{max_auto_fix})...",
            )
            if reason.startswith('Invalid JSON') or 'truncated' in reason.lower():
                feedback = (
                    'The plan was REFUSED because your output was truncated or contained '
                    f'malformed JSON: {reason}\n'
                    'Please output a SHORTER, COMPLETE plan JSON. '
                    'Reduce the number of steps or shorten intent descriptions if needed. '
                    'Output ONLY the JSON object with no other text. '
                    'Ensure the JSON is complete with all braces and brackets closed.'
                )
            elif 'manuscript_consistency' in str(reason):
                feedback = (
                    f"The plan was REFUSED for this reason: {reason}\n"
                    'Fix manuscript workflow consistency now:\n'
                    '1) Keep manuscript profile consistent with USER_INTENT (e.g., patent task -> --template patent / --profile patent).\n'
                    '2) Do not mix review sections into a patent plan.\n'
                    '3) Include per-section writing goals for all required patent sections: '
                    'Technical Field, Background Art, Summary of Invention, Detailed Description, Claims, Abstract.\n'
                    '4) Keep validate_content and assemble_manuscript goals.\n'
                    'Return the revised plan in the same JSON schema.'
                )
            else:
                allow_str = self._registry.software_list_str()
                block_str = self._registry.crp_block_str()
                feedback = (
                    f"The plan was REFUSED for this reason: {reason}\n"
                    'Please fix the offending steps to use ONLY CRP-allowed software '
                    f'({allow_str}). Do NOT use or mention {block_str} as execution targets. '
                    "You may reference them only in mapping descriptions (e.g., 'mapped from VASP → ABACUS'). "
                    'Return the revised plan in the same JSON schema.'
                )
            plan = self._revise_plan(goal, plan, feedback, state=state, task_id=task_id)

        state['plan'] = plan
        if plan.get('status') == 'REFUSED':
            reason = plan.get('refusal_reason', 'Unknown')
            self.logger.warning(
                '[CRP] Mission refused after %d auto-fix attempts: %s',
                max_auto_fix,
                reason,
            )
            state['phase'] = 'failed'
            state['fail_reason'] = reason
        else:
            state['phase'] = 'preflight'
        self._emit(
            'Planner', 'phase_change', {'from': 'planning', 'to': state['phase']}
        )
        return state

    def _phase_preflight(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Human confirmation loop or auto-pass before execution."""
        plan = state['plan']
        self._print_plan_report(plan)
        if task_id:
            try:
                plan_path = f"{self._planner_hidden_dir(task_id)}/current_plan.json"
                self._file_io.write_text(
                    plan_path,
                    json.dumps(
                        _plan_to_external_schema(plan), ensure_ascii=False, indent=2
                    ),
                )
            except Exception as e:
                self.logger.warning('[Planner] Failed to save current_plan.json: %s', e)

        if self.human_check:
            while True:
                fid = plan.get('fidelity_level', '')
                header = f"[Planner] {plan.get('strategy_name', '')}" + (
                    f" (fidelity: {fid})" if fid else ''
                )
                print(f"\033[92m{header}\033[0m")
                print('-' * 50)
                step_lines = [header, '-' * 50]
                for step in plan.get('steps', []):
                    cost = f"[{step.get('compute_cost', '?')}]"
                    stype = (
                        f" ({step.get('step_type', 'normal')})"
                        if step.get('step_type') == 'skill_evolution'
                        else ''
                    )
                    status_tag = ' [DONE]' if step.get('status') == 'done' else ''
                    line = (
                        f"  {step.get('step_id')}. {cost:10}{stype} "
                        f"{step.get('intent')}{status_tag}"
                    )
                    print(line)
                    step_lines.append(line)
                step_lines.append('-' * 50)
                print('-' * 50)
                self._emit('Planner', 'thought', '\n'.join(step_lines))
                ans = self._ask_human(
                    "Type 'go' to execute, 'abort' to quit, or describe changes to revise the plan.",
                    mode='block',
                )
                ans_lower = ans.strip().lower()
                if ans_lower == 'go':
                    break
                if ans_lower == 'abort':
                    state['phase'] = 'aborted'
                    self._emit(
                        'Planner',
                        'phase_change',
                        {'from': 'preflight', 'to': 'aborted'},
                    )
                    return state
                if not ans.strip():
                    continue
                self.logger.info(
                    '[Planner] Revising plan from user feedback: %s',
                    ans[:100],
                )
                plan = self._revise_plan_from_file(
                    goal,
                    task_id,
                    plan,
                    ans,
                    state=state,
                )
                state['plan'] = plan
                self._save_state(task_id, state)
                if plan.get('status') == 'REFUSED':
                    reason = plan.get('refusal_reason', 'Unknown')
                    self.logger.warning(
                        '[CRP] Revised plan refused: %s — attempting auto-fix',
                        reason,
                    )
                    self._emit(
                        'Planner',
                        'thought',
                        f"[CRP] Revised plan refused: {reason}. Auto-fixing...",
                    )
                    fix_feedback = (
                        f"The revised plan was REFUSED: {reason}\n"
                        'Fix the offending steps to use ONLY CRP-allowed software. '
                        'Return the corrected plan JSON.'
                    )
                    plan = self._revise_plan(
                        goal,
                        plan,
                        fix_feedback,
                        state=state,
                        task_id=task_id,
                    )
                    state['plan'] = plan
                    self._save_state(task_id, state)
                    if plan.get('status') == 'REFUSED':
                        self.logger.warning(
                            '[CRP] Auto-fix failed. Plan still refused: %s',
                            plan.get('refusal_reason'),
                        )
                        state['phase'] = 'failed'
                        state['fail_reason'] = plan.get('refusal_reason')
                        self._emit(
                            'Planner',
                            'phase_change',
                            {'from': 'preflight', 'to': 'failed'},
                        )
                        return state
        else:
            fid = plan.get('fidelity_level', '')
            header = f"[Planner] {plan.get('strategy_name', '')}" + (
                f" (fidelity: {fid})" if fid else ''
            )
            step_lines = [header, '-' * 50]
            for step in plan.get('steps', []):
                cost = f"[{step.get('compute_cost', '?')}]"
                stype = (
                    f" ({step.get('step_type', 'normal')})"
                    if step.get('step_type') == 'skill_evolution'
                    else ''
                )
                step_lines.append(
                    f"  {step.get('step_id')}. {cost:10}{stype} {step.get('intent')}"
                )
            step_lines.append('-' * 50)
            self._emit('Planner', 'thought', '\n'.join(step_lines))

        state['phase'] = 'executing'
        self._emit('Planner', 'phase_change', {'from': 'preflight', 'to': 'executing'})
        return state

    def _execute_single_step_for_batch(
        self,
        step: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
        workspaces: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Adapter that wraps ``_execute_single_step`` for ``BatchExecutor``."""
        result = self._execute_single_step(step, state, task_id, workspaces)
        return result, {}

    def _phase_executing(self, state: dict[str, Any], task_id: str) -> dict[str, Any]:
        """Run the next execution window and observe results."""
        plan = state['plan']
        pending_steps = [
            step for step in plan.get('steps', []) if step.get('status') == 'pending'
        ]
        history_len = len(state.get('history', []))
        self.logger.info(
            '[Planner] _phase_executing entered: pending_steps=%d, history_len=%d, '
            'turn_budget_remaining=%d',
            len(pending_steps),
            history_len,
            state.get('turn_budget_remaining', -1),
        )
        if history_len == 0 and pending_steps:
            self.logger.info(
                '[Planner] Resume detected: phase=executing but history is empty. '
                'Will execute from first pending step.'
            )

        workspaces = self._run_dir_path() / 'workspaces' / task_id
        workspaces.mkdir(parents=True, exist_ok=True)

        window = self._get_next_execution_window(plan)
        if window:
            self._append_journal(
                state,
                task_id,
                phase='executing',
                title=f"Execution window: {[step['step_id'] for step in window]}",
                body='\n'.join(
                    f"step_{step['step_id']}: {step.get('intent', '')[:100]}"
                    for step in window
                ),
            )
        if not window:
            if self._is_goal_achieved(state):
                state['phase'] = 'completed'
                self._emit(
                    'Planner', 'phase_change', {'from': 'executing', 'to': 'completed'}
                )
            else:
                state['phase'] = 'failed'
                state['fail_reason'] = (
                    'No executable steps remaining (dependency deadlock or all failed)'
                )
                self._emit(
                    'Planner', 'phase_change', {'from': 'executing', 'to': 'failed'}
                )
            return state

        batch_tasks: list[ExecutionTask] = []
        for step in window:
            batch_tasks.append(
                ExecutionTask(
                    task_id=str(step.get('step_id', 0)),
                    func=self._execute_single_step_for_batch,
                    kwargs={
                        'step': step,
                        'state': state,
                        'task_id': task_id,
                        'workspaces': workspaces,
                    },
                    meta={'step': step},
                )
            )

        executor = BatchExecutor(
            max_workers=self._planner_max_workers,
            rate_limit=self._rate_limit,
        )
        exec_results = executor.execute_batch(batch_tasks)

        for res, step in zip(exec_results, window):
            if res.status == 'success':
                step_result = res.output
            else:
                step_result = {
                    'step_id': step.get('step_id', 0),
                    'status': 'failed',
                    'fallback_succeeded': False,
                    'new_skill_registered': False,
                    'skill_path': '',
                    'result_summary': res.error or 'Executor-level failure',
                    'replan_requested': False,
                    'replan_reason': '',
                }

            if step_result['status'] == 'done':
                step['status'] = 'done'
            elif step_result['status'] == 'skipped':
                step['status'] = 'done'
            elif step_result['status'] == 'failed':
                step['status'] = 'failed'

            # Fix 4: If the agent did future steps' work during this step,
            # mark those steps as done instead of re-executing them.
            if step_result['status'] == 'done':
                result_summary = step_result.get('result_summary', '')
                if result_summary:
                    for future_step in plan.get('steps', []):
                        if future_step.get('status') != 'pending':
                            continue
                        future_id = future_step.get('step_id', 0)
                        if future_id <= step_result.get('step_id', 0):
                            continue
                        future_tool = future_step.get('tool_name', '')
                        future_intent = future_step.get('intent', '')
                        summary_lower = result_summary.lower()

                        # 1. Check explicit tool_name field
                        tool_mentioned = bool(
                            future_tool
                            and future_tool.lower() in summary_lower
                        )

                        # 2. Extract MCP tool names mentioned anywhere in the
                        #    intent (e.g. "mat_dpa_submit_optimize_structure")
                        #    and check if any appear in the summary.
                        intent_tool_names = re.findall(
                            r'mat_[a-z0-9_]+', future_intent.lower()
                        )
                        intent_tool_mentioned = any(
                            t in summary_lower for t in intent_tool_names
                        )

                        # 3. Keyword match: words with len>4 from the full
                        #    intent (not just first 6 words).
                        intent_words = [
                            w.lower() for w in future_intent.split()
                            if len(w) > 4
                            and not w.startswith('mat_')  # skip tool names
                        ]
                        # Require at least 2 distinct content words to match
                        # (avoids false positives on common words like "step").
                        matched_words = [
                            w for w in intent_words if w in summary_lower
                        ]
                        keyword_match = (
                            len(intent_words) >= 2
                            and len(matched_words) >= min(2, len(intent_words))
                        )

                        if tool_mentioned or intent_tool_mentioned or keyword_match:
                            self.logger.info(
                                '[Planner] Step %s already completed during step %s '
                                '(tool=%s intent_tools=%s keywords=%s) — marking done.',
                                future_id,
                                step_result.get('step_id'),
                                future_tool or '-',
                                intent_tool_names,
                                matched_words[:5],
                            )
                            future_step['status'] = 'done'
                            state['history'].append({
                                'step': future_id,
                                'tool_name': future_tool,
                                'intent': future_intent[:200],
                                'result_summary': (
                                    f"Achieved during step {step_result.get('step_id')}: "
                                    f"{result_summary[:300]}"
                                ),
                            })

            history_entry: dict[str, Any] = {
                'step': step_result['step_id'],
                'tool_name': step.get('tool_name', ''),
                'intent': step.get('intent', '')[:200],
            }
            if step_result['status'] == 'failed':
                history_entry['error'] = step_result['result_summary']
            else:
                history_entry['result_summary'] = step_result['result_summary']
            if step_result.get('new_skill_registered'):
                history_entry['new_skill_registered'] = True
                history_entry['skill_path'] = step_result.get('skill_path', '')
            if step_result.get('produced_artifacts'):
                history_entry['produced_artifacts'] = step_result['produced_artifacts']
            state['history'].append(history_entry)
            self._save_state(task_id, state)

            stop_event = getattr(self.agent, '_stop_event', None)
            if (
                step_result.get('status') == 'failed'
                and stop_event is not None
                and stop_event.is_set()
            ):
                self.logger.info(
                    '[Planner] Stop requested during step %s, aborting.',
                    step_result.get('step_id'),
                )
                state['phase'] = 'aborted'
                self._emit(
                    'Planner',
                    'phase_change',
                    {'from': 'executing', 'to': 'aborted'},
                )
                return state

            should_replan, reason = self._needs_replanning(state, step_result)
            if should_replan:
                state['phase'] = 'replanning'
                state['replan_reason'] = reason
                self._emit(
                    'Planner',
                    'replan_triggered',
                    {'reason': reason, 'after_step': step_result['step_id']},
                )
                self._emit(
                    'Planner',
                    'phase_change',
                    {'from': 'executing', 'to': 'replanning'},
                )
                return state

            if step_result['status'] == 'failed' and not should_replan:
                step_id = step_result.get('step_id', '?')
                summary = step_result.get('result_summary', 'unknown error')[:200]
                ans = self._ask_human(
                    f"Step {step_id} failed: {summary}\n"
                    'Options:\n'
                    "  'skip'    — skip this step and continue with the next\n"
                    "  'retry'   — provide modified suggestions for retrying\n"
                    "  'abort'   — abort the mission\n"
                    '  Or describe modifications/suggestions.\n'
                    "(Default: 'skip' — skip this step and continue)",
                    mode='timeout',
                    timeout_sec=120,
                    default='skip',
                )
                ans_lower = ans.strip().lower()
                if ans_lower == 'abort':
                    state['phase'] = 'aborted'
                    self._emit(
                        'Planner',
                        'phase_change',
                        {'from': 'executing', 'to': 'aborted'},
                    )
                    return state
                if ans_lower in ('skip', ''):
                    self.logger.info(
                        '[Planner] Human chose to skip failed step %s',
                        step_id,
                    )
                elif ans_lower == 'retry' or (
                    ans_lower not in ('skip', 'abort', '') and len(ans) > 2
                ):
                    feedback = (
                        ans
                        if ans_lower != 'retry'
                        else 'Please retry the failed step with a different approach.'
                    )
                    state['phase'] = 'replanning'
                    state['replan_reason'] = (
                        f"Human feedback after step {step_id} failure: {feedback}"
                    )
                    self._emit(
                        'Planner',
                        'replan_triggered',
                        {'reason': state['replan_reason'], 'after_step': step_id},
                    )
                    self._emit(
                        'Planner',
                        'phase_change',
                        {'from': 'executing', 'to': 'replanning'},
                    )
                    return state

            branch = step.get('conditional_branch')
            if branch:
                if step_result['status'] == 'done' and branch.get('if_success'):
                    self._skip_to_step(plan, branch['if_success'])
                elif step_result['status'] == 'failed' and branch.get('if_fail'):
                    self._skip_to_step(plan, branch['if_fail'])

        if self._is_goal_achieved(state):
            state['phase'] = 'completed'
            self._emit(
                'Planner', 'phase_change', {'from': 'executing', 'to': 'completed'}
            )
        return state

    def _skip_to_step(self, plan: dict[str, Any], target_step_id: int) -> None:
        """Mark all pending steps before ``target_step_id`` as skipped."""
        for step in plan.get('steps', []):
            if step.get('step_id') == target_step_id:
                break
            if step.get('status') == 'pending':
                step['status'] = 'done'

    def _phase_replanning(
        self, state: dict[str, Any], goal: str, task_id: str
    ) -> dict[str, Any]:
        """Revise the remaining plan after execution feedback.

        Increments either ``failure_replan_count`` or ``adaptive_replan_count``
        depending on the ``[failure]`` / ``[adaptive]`` prefix in
        ``state['replan_reason']``.  The legacy ``replan_count`` field is kept
        as the sum of both for backward-compatible logging and state files.
        """
        replan_reason = state.get('replan_reason', '')
        is_adaptive = replan_reason.startswith('[adaptive]')
        counter_key = 'adaptive_replan_count' if is_adaptive else 'failure_replan_count'
        replan_type_label = 'adaptive' if is_adaptive else 'failure'

        self.logger.info(
            '[Planner] Mid-flight replan #%d (%s): %s',
            state.get('replan_count', 0) + 1,
            replan_type_label,
            replan_reason,
        )
        old_steps = [step.copy() for step in (state.get('plan') or {}).get('steps', [])]

        revised_plan = self._replan_from_results(state, goal, task_id)

        if revised_plan.get('status') == 'REFUSED':
            self.logger.warning(
                '[CRP] Revised plan refused: %s',
                revised_plan.get('refusal_reason'),
            )
            state['phase'] = 'executing'
            self._emit(
                'Planner', 'phase_change', {'from': 'replanning', 'to': 'executing'}
            )
            return state

        done_ids = {
            step['step_id'] for step in old_steps if step.get('status') == 'done'
        }
        for step in revised_plan.get('steps', []):
            if step.get('step_id') in done_ids:
                step['status'] = 'done'

        state['plan'] = revised_plan
        # Increment the type-specific counter and keep the legacy total in sync.
        state[counter_key] = state.get(counter_key, 0) + 1
        state['replan_count'] = (
            state.get('failure_replan_count', 0) + state.get('adaptive_replan_count', 0)
        )
        state['phase'] = 'executing'

        new_steps = revised_plan.get('steps', [])
        self._emit(
            'Planner',
            'plan_revised',
            {
                'replan_count': state['replan_count'],
                'reason': state.get('replan_reason', ''),
                'old_step_count': len(old_steps),
                'new_step_count': len(new_steps),
            },
        )
        self._emit('Planner', 'phase_change', {'from': 'replanning', 'to': 'executing'})

        self._print_plan_report(revised_plan)
        fid = revised_plan.get('fidelity_level', '')
        header = (
            f"[Planner] REVISED PLAN #{state['replan_count']}: "
            f"{revised_plan.get('strategy_name', '')}"
            + (f" (fidelity: {fid})" if fid else '')
        )
        step_lines = [header, '-' * 50]
        for step in revised_plan.get('steps', []):
            cost = f"[{step.get('compute_cost', '?')}]"
            stype = (
                f" ({step.get('step_type', 'normal')})"
                if step.get('step_type') == 'skill_evolution'
                else ''
            )
            status_tag = ' [DONE]' if step.get('status') == 'done' else ''
            step_lines.append(
                f"  {step.get('step_id')}. {cost:10}{stype} {step.get('intent')}{status_tag}"
            )
        step_lines.append('-' * 50)
        self.logger.info('\n'.join(step_lines))
        self._emit('Planner', 'thought', '\n'.join(step_lines))

        return state

    def _build_execution_summary(self, state: dict[str, Any]) -> dict[str, Any]:
        """Build a comprehensive execution summary for final reporting."""
        plan = state.get('plan') or {}
        steps = plan.get('steps', [])
        history = state.get('history', [])

        completed_steps = []
        failed_steps = []
        skipped_steps = []
        for step in steps:
            status = step.get('status', 'pending')
            step_info = {
                'step_id': step.get('step_id'),
                'intent': step.get('intent', ''),
                'compute_cost': step.get('compute_cost', ''),
                'status': status,
            }
            if status == 'done':
                completed_steps.append(step_info)
            elif status == 'failed':
                failed_steps.append(step_info)
            else:
                skipped_steps.append(step_info)

        step_results_detail = []
        approximations_and_simplifications = []
        for entry in history:
            detail = {
                'step_id': entry.get('step', '?'),
                'intent': entry.get('intent', ''),
                'tool_name': entry.get('tool_name', ''),
            }
            if entry.get('error'):
                detail['status'] = 'FAILED'
                detail['error'] = entry['error']
            else:
                detail['status'] = 'OK'
                detail['result_summary'] = entry.get('result_summary', '')
            if entry.get('new_skill_registered'):
                detail['new_skill_registered'] = True
                detail['skill_path'] = entry.get('skill_path', '')
            if entry.get('produced_artifacts'):
                detail['produced_artifacts'] = entry['produced_artifacts']
            step_results_detail.append(detail)

            result_text = (entry.get('result_summary', '') or '').lower()
            if 'fallback' in result_text:
                approximations_and_simplifications.append(
                    f"Step {entry.get('step', '?')}: Executed via fallback strategy (original approach failed)."
                )
            if 'coarse' in result_text or 'screening' in result_text:
                approximations_and_simplifications.append(
                    f"Step {entry.get('step', '?')}: Used coarse/screening-level settings (not production quality)."
                )

        replan_info = {
            'replan_count': state.get('replan_count', 0),
            'failure_replan_count': state.get('failure_replan_count', 0),
            'adaptive_replan_count': state.get('adaptive_replan_count', 0),
            'max_replans': self.max_replans,
            'max_adaptive_replans': self.max_adaptive_replans,
        }

        summary = {
            'overall_status': state.get('phase', 'unknown'),
            'total_steps': len(steps),
            'completed_count': len(completed_steps),
            'failed_count': len(failed_steps),
            'skipped_count': len(skipped_steps),
            'completed_steps': completed_steps,
            'failed_steps': failed_steps,
            'skipped_steps': skipped_steps,
            'step_results_detail': step_results_detail,
            'approximations_and_simplifications': approximations_and_simplifications,
            'replan_info': replan_info,
            'fail_reason': state.get('fail_reason', ''),
        }

        self._emit('Planner', 'execution_summary', summary)
        return summary

    def run(
        self,
        task_description: str = '',
        task_id: str = 'planner_task',
        task: Optional[TaskInstance] = None,
        append_result: bool = True,
    ) -> dict[str, Any]:
        """State-machine driven execution with resume support."""
        if task is not None:
            task_description = task.description or ''
            task_id = task.task_id
            self._task_with_history = task
        else:
            self._task_with_history = None

        state = self._initialize_state(task_description, task_id)

        self._solver = DirectSolver(self.agent, self.config)
        if self.run_dir is not None:
            self._solver.set_run_dir(self.run_dir)

        # ── Sub-agent factory (context isolation per step) ───────────────
        if self._sub_agent_enabled:
            from .step_sub_agent import StepSubAgentFactory

            original_max_turns = getattr(
                getattr(self.agent, 'config', None), 'max_turns', 200
            )
            self._sub_agent_factory = StepSubAgentFactory(
                self.agent,
                self.config,
                enabled=True,
                step_turn_budget=self._sub_agent_step_turn_budget,
                original_max_turns=original_max_turns,
            )
        else:
            self._sub_agent_factory = None

        self.logger.info(
            '[Planner] State machine started (phase=%s, replan_count=%d, history_len=%d, pending_steps=%d)',
            state['phase'],
            state.get('replan_count', 0),
            len(state.get('history', [])),
            sum(
                1
                for step in (state.get('plan') or {}).get('steps', [])
                if step.get('status') == 'pending'
            ),
        )

        while state['phase'] not in ('completed', 'failed', 'aborted'):
            stop_event = getattr(self.agent, '_stop_event', None)
            if stop_event is not None and stop_event.is_set():
                self.logger.info('[Planner] Stop requested by user, aborting.')
                phase = state['phase']
                state['phase'] = 'aborted'
                self._emit('Planner', 'phase_change', {'from': phase, 'to': 'aborted'})
                self._save_state(task_id, state)
                break

            phase = state['phase']

            if phase == 'pre_check':
                state = self._phase_pre_check(state, task_description, task_id)
            elif phase == 'planning':
                state = self._phase_planning(state, task_description, task_id)
            elif phase == 'preflight':
                state = self._phase_preflight(state, task_description, task_id)
            elif phase == 'executing':
                state = self._phase_executing(state, task_id)
            elif phase == 'replanning':
                # Determine which budget applies to this replan request.
                replan_reason = state.get('replan_reason', '')
                is_adaptive = replan_reason.startswith('[adaptive]')
                if is_adaptive:
                    current_count = state.get('adaptive_replan_count', 0)
                    limit = self.max_adaptive_replans
                    limit_label = f"adaptive replan limit ({limit})"
                else:
                    current_count = state.get('failure_replan_count', 0)
                    limit = self.max_replans
                    limit_label = f"failure replan limit ({limit})"

                if current_count >= limit:
                    self.logger.warning(
                        '[Planner] %s reached (current=%d)',
                        limit_label,
                        current_count,
                    )
                    self._emit(
                        'Planner',
                        'thought',
                        f"[Planner] {limit_label.capitalize()} reached. Asking human for guidance...",
                    )
                    ans = self._ask_human(
                        f"{limit_label.capitalize()} reached. Options:\n"
                        "  'continue' — force continue with current plan\n"
                        "  'retry'    — allow one more replan attempt\n"
                        "  'abort'    — abort the mission\n"
                        '  Or describe changes/suggestions for the remaining plan.\n'
                        "(Default: 'continue' — skip replanning, proceed with current plan)",
                        mode='timeout',
                        timeout_sec=120,
                        default='continue',
                    )
                    ans_lower = ans.strip().lower()
                    if ans_lower in ('skip', 'continue', ''):
                        self.logger.info(
                            '[Planner] Human chose to continue with current plan'
                        )
                        state['phase'] = 'executing'
                    elif ans_lower == 'abort':
                        state['phase'] = 'aborted'
                        self._emit(
                            'Planner',
                            'phase_change',
                            {'from': 'replanning', 'to': 'aborted'},
                        )
                    elif ans_lower == 'retry':
                        self.logger.info('[Planner] Human granted one extra replan')
                        # Reset the relevant counter to allow one more attempt.
                        counter_key = 'adaptive_replan_count' if is_adaptive else 'failure_replan_count'
                        state[counter_key] = max(0, limit - 1)
                        state['replan_count'] = (
                            state.get('failure_replan_count', 0)
                            + state.get('adaptive_replan_count', 0)
                        )
                        state = self._phase_replanning(state, task_description, task_id)
                    else:
                        self.logger.info(
                            '[Planner] Human gave revision feedback: %s',
                            ans[:100],
                        )
                        state['replan_reason'] = f"[failure] Human feedback: {ans}"
                        counter_key = 'adaptive_replan_count' if is_adaptive else 'failure_replan_count'
                        state[counter_key] = max(0, limit - 1)
                        state['replan_count'] = (
                            state.get('failure_replan_count', 0)
                            + state.get('adaptive_replan_count', 0)
                        )
                        state = self._phase_replanning(state, task_description, task_id)
                else:
                    state = self._phase_replanning(state, task_description, task_id)
            else:
                self.logger.error('[Planner] Unknown phase: %s, aborting', phase)
                state['phase'] = 'failed'
                state['fail_reason'] = f"Unknown phase: {phase}"

            self._save_state(task_id, state)

        self.logger.info(
            '[Planner] State machine finished (phase=%s, replans=%d, steps_done=%d)',
            state['phase'],
            state.get('replan_count', 0),
            sum(
                1
                for step in (state.get('plan') or {}).get('steps', [])
                if step.get('status') == 'done'
            ),
        )
        self._task_with_history = None
        self._solver = None
        if self._sub_agent_factory is not None:
            self._sub_agent_factory.restore_agent_state()
            self._sub_agent_factory = None

        execution_summary = self._build_execution_summary(state)
        state['execution_summary'] = execution_summary

        result: dict[str, Any] = {
            'status': state['phase'],
            'plan': state.get('plan'),
            'state': state,
            'execution_summary': execution_summary,
        }
        if state.get('fail_reason'):
            result['reason'] = state['fail_reason']
        return result
