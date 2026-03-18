"""Execution and pre-check helpers for ``ResearchPlanner``."""

import json
import re
from pathlib import Path
from typing import Any

from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from ...prompts.build_prompt import LANGUAGE_RULE
from .direct_solver import DirectSolver
from .plan_utils import _extract_json_from_content, _get_mat_master_config

try:
    from ..exp import SkillEvolutionExp

    _HAS_EVOLUTION = True
except ImportError:
    SkillEvolutionExp = None
    _HAS_EVOLUTION = False


class ResearchPlannerExecutionMixin:
    def _is_goal_achieved(self, state: dict[str, Any]) -> bool:
        """A goal is achieved only when all plan steps are done."""
        plan = state.get('plan')
        if not plan or not isinstance(plan, dict):
            return False
        steps = plan.get('steps', [])
        if not steps:
            return False
        return all(step.get('status') == 'done' for step in steps)

    @staticmethod
    def _summarize_solver_result(result: Any, max_len: int = 1200) -> str:
        """Extract a concise text summary from DirectSolver output."""
        if isinstance(result, dict):
            for key in ('result_summary', 'summary', 'message', 'final_message'):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:max_len]
            if isinstance(result.get('execution_summary'), dict):
                return json.dumps(result['execution_summary'], ensure_ascii=False)[
                    :max_len
                ]
            if 'status' in result:
                return f"status={result.get('status')}"[:max_len]
            safe_result = {k: v for k, v in result.items() if k != 'trajectory'}
            return json.dumps(safe_result, ensure_ascii=False)[:max_len]
        return str(result)[:max_len]

    def _build_step_prompt(
        self,
        intent: str,
        fallback: str,
        *,
        original_intent: str = '',
        step_id: int = 0,
        total_steps: int = 0,
        plan_summary: str = '',
    ) -> str:
        """Build executor step prompt with [Original Intent] / [Task of This Step] structure.

        Injecting the original intent and step position prevents the executor from
        attempting to complete the full task in a single step.
        """
        parts: list[str] = []

        # ── Macro context: what the user ultimately wants (read-only) ──────────
        if original_intent:
            parts.append(
                '[Original Intent]\n'
                f'The user\'s overall goal: {original_intent}\n'
                '(Read-only context — do NOT attempt to complete the full goal in this step.)'
            )

        # ── Micro context: exactly what THIS step must achieve ──────────────────
        if total_steps > 0:
            next_step = step_id + 1
            parts.append(
                '[Task of This Step]\n'
                f'You are executing step {step_id} of {total_steps}.\n'
                f'Your ONLY job in this step: {intent}\n'
                f'If that fails: {fallback}\n'
                f'Do NOT execute step {next_step} or any later steps.'
            )
        else:
            parts.append(f'Achieve: {intent}. If that fails: {fallback}')

        # ── Overall plan summary (context only, not to be executed ahead) ───────
        if plan_summary:
            parts.append(
                '[Overall Plan — for context only, do NOT execute ahead]\n'
                f'{plan_summary}'
            )

        # ── Completion self-reporting requirement ────────────────────────────────
        parts.append(
            'At the end of the step, explicitly report one of: completed, partial, or blocked. '
            'List the concrete outputs you produced or saved, and do not claim completion '
            'unless the requested deliverable was actually achieved.'
        )

        return '\n\n'.join(parts)

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
            # Inject all previous steps as a structured summary list.
            # Each entry is kept short (~120 chars) to avoid context bloat.
            lines: list[str] = []
            for entry in history:
                prev_id = entry.get('step', '?')
                if entry.get('error'):
                    err = str(entry['error'])[:120]
                    lines.append(f'  Step {prev_id} [FAILED]: {err}')
                else:
                    summary = str(entry.get('result_summary', 'done'))[:120]
                    lines.append(f'  Step {prev_id} [OK]: {summary}')
            parts.append('[Previous Steps Results]\n' + '\n'.join(lines))

        artifacts = state.get('artifacts') or {}
        journal_raw = artifacts.get('research_journal', '')
        journal_path = (
            Path(journal_raw)
            if journal_raw
            else (self._planner_artifact_dir(task_id) / 'research_journal.md')
        )
        if journal_path.exists():
            try:
                journal_text = journal_path.read_text(encoding='utf-8')
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
            Path(lit_raw)
            if lit_raw
            else (self._planner_artifact_dir(task_id) / 'literature_index.jsonl')
        )
        if lit_path.exists():
            try:
                lines = lit_path.read_text(encoding='utf-8').strip().splitlines()
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
        """Evaluate whether execution should pause for replanning."""
        if not self.auto_replan:
            return False, ''
        if (
            self.replan_on_failure
            and step_result.get('status') == 'failed'
            and not step_result.get('fallback_succeeded')
        ):
            return (
                True,
                f"Step {step_result.get('step_id', '?')} failed without successful fallback",
            )
        if self.replan_on_new_skill and step_result.get('new_skill_registered'):
            return (
                True,
                f"New skill registered: {step_result.get('skill_path', 'unknown')}; subsequent steps may benefit",
            )
        if step_result.get('replan_requested'):
            return True, step_result.get(
                'replan_reason',
                'explicit replan requested by executor',
            )
        task_id = str(state.get('task_id') or '')
        if task_id and self._llm_replan_check(state, task_id, step_result):
            return True, 'LLM heuristic detected plan deviation'
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

    def _execute_single_step(
        self,
        step: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
        workspaces: Path,
    ) -> dict[str, Any]:
        """Execute one step and return a normalized result dict."""
        step_id = step.get('step_id', 0)
        tool_name = step.get('tool_name', '')
        intent = step.get('intent', '')
        fallback = step.get('fallback_logic', 'None')
        step_dir = workspaces / f"step_{step_id}"
        step_dir.mkdir(parents=True, exist_ok=True)

        result_info: dict[str, Any] = {
            'step_id': step_id,
            'status': 'done',
            'fallback_succeeded': False,
            'new_skill_registered': False,
            'skill_path': '',
            'result_summary': '',
            'replan_requested': False,
            'replan_reason': '',
        }

        if not self._consume_turns(state, task_id, 1):
            self._fail_max_turns_exceeded(task_id, state)
            result_info['status'] = 'failed'
            result_info['result_summary'] = 'max_turns_exceeded'
            result_info['replan_requested'] = False
            return result_info

        steps_list = (state.get('plan') or {}).get('steps', [])
        self._emit(
            'Planner',
            'status_stages',
            {
                'total': len(steps_list),
                'current': step_id,
                'step_id': step_id,
                'intent': intent[:120] if intent else '',
            },
        )

        if step.get('requires_human_confirm') or step.get('compute_cost') == 'High':
            ans = self._ask_human(
                f"Step {step_id} is HIGH COST. Proceed? (y/n)",
                mode='block',
            )
            if ans.strip().lower() not in ('y', 'yes', 'go'):
                result_info['status'] = 'skipped'
                result_info['result_summary'] = 'skipped_by_user'
                return result_info

        self.logger.info('[Planner] Step %s (goal): %s', step_id, intent[:80])

        if hasattr(self.agent, '_tool_guard'):
            self.agent._tool_guard.reset_loop_history()

        assert self._solver is not None, 'DirectSolver not initialized'
        solver = self._solver

        if tool_name == 'skill_evolution':
            if not _HAS_EVOLUTION:
                self.logger.warning(
                    '[Planner] skill_evolution requested but SkillEvolutionExp not available.'
                )
                print(
                    '\033[91m[Planner] Skill Evolution not available. Attempting fallback.\033[0m'
                )
                if self._execute_fallback(
                    step,
                    solver,
                    workspaces,
                    state.get('plan') if isinstance(state, dict) else None,
                ):
                    result_info['fallback_succeeded'] = True
                    result_info['result_summary'] = 'fallback_after_evo_unavailable'
                else:
                    result_info['status'] = 'failed'
                    result_info['result_summary'] = (
                        'skill_evolution_unavailable_no_fallback'
                    )
                return result_info

            print(
                '\033[95m[Autonomy] Missing capability; initiating Skill Evolution...\033[0m'
            )
            self._emit('Planner', 'exp_run', 'SkillEvolutionExp')
            evo_exp = SkillEvolutionExp(self.agent, self.config)
            evo_exp.set_run_dir(step_dir)
            try:
                evo_result = evo_exp.run(
                    intent,
                    task_id=f"{task_id}_step_{step_id}_evo",
                )
                if evo_result.get('status') == 'completed':
                    print('\033[92m[Autonomy] New skill created. Proceeding.\033[0m')
                    skill_path = evo_result.get('skill_path', '')
                    if skill_path:
                        self._emit('Planner', 'status_skill_produced', str(skill_path))
                    result_info['new_skill_registered'] = True
                    result_info['skill_path'] = str(skill_path or '')
                    result_info['result_summary'] = str(skill_path or evo_result)[:200]

                    try:
                        mat_cfg = _get_mat_master_config(self.config)
                        evo_cfg = mat_cfg.get('skill_evolution') or {}
                        persist_mode = evo_cfg.get('persist_new_skills', 'ask')
                        persist_flag = bool(step.get('persist_skill', False))
                        should_persist = persist_flag or persist_mode == 'always'

                        if not should_persist and persist_mode == 'ask' and skill_path:
                            ans = self._ask_human(
                                f"New skill created at {skill_path}.\n"
                                'Save to personal library (~/.evomaster-skills)? (y/n, default: y)',
                                mode='timeout',
                                timeout_sec=30,
                                default='y',
                            )
                            should_persist = ans.strip().lower() not in ('n', 'no')

                        if should_persist and skill_path:
                            skill_path_obj = Path(skill_path)
                            local_user_root = Path(
                                evo_cfg.get(
                                    'local_user_skills_root',
                                    '~/.evomaster-skills',
                                )
                            ).expanduser()
                            skill_name = skill_path_obj.name
                            skill_md = skill_path_obj / 'SKILL.md'
                            if skill_md.exists():
                                try:
                                    text = skill_md.read_text(encoding='utf-8')
                                    match = re.search(
                                        r'^name:\s*(.+)$',
                                        text,
                                        re.MULTILINE,
                                    )
                                    if match:
                                        skill_name = match.group(1).strip()
                                except Exception:
                                    pass
                            persisted = evo_exp._copy_to_user_skills(
                                skill_path_obj,
                                skill_name,
                                local_user_root,
                            )
                            result_info['persisted_path'] = persisted
                            registry = getattr(self.agent, 'skill_registry', None)
                            if registry and hasattr(registry, 'register_user_skill'):
                                registry.register_user_skill(Path(persisted))
                            print(
                                f"\033[96m[Autonomy] Skill persisted to {persisted}\033[0m"
                            )
                    except Exception as e:
                        self.logger.warning(
                            '[Planner] Skill persist step failed (non-fatal): %s',
                            e,
                        )
                else:
                    print(
                        '\033[93m[Autonomy] Evolution failed. Triggering fallback.\033[0m'
                    )
                    if self._execute_fallback(
                        step,
                        solver,
                        workspaces,
                        state.get('plan') if isinstance(state, dict) else None,
                    ):
                        result_info['fallback_succeeded'] = True
                        result_info['result_summary'] = 'fallback_after_evo_failed'
                    else:
                        result_info['status'] = 'failed'
                        result_info['result_summary'] = (
                            f"evo_failed_no_fallback: {str(evo_result)[:150]}"
                        )
            except Exception as e:
                self.logger.error(
                    '[Planner] Skill evolution step %s failed: %s',
                    step_id,
                    e,
                )
                if self._execute_fallback(
                    step,
                    solver,
                    workspaces,
                    state.get('plan') if isinstance(state, dict) else None,
                ):
                    result_info['fallback_succeeded'] = True
                    result_info['result_summary'] = 'fallback_after_evo_exception'
                else:
                    result_info['status'] = 'failed'
                    result_info['result_summary'] = str(e)[:200]
            return result_info

        self._emit('Planner', 'exp_run', 'DirectSolver')
        step_context = self._build_step_context(step, state, task_id)
        if step_context:
            self.logger.info(
                '[Planner] _execute_single_step: injecting step_context step=%s chars=%d',
                step_id,
                len(step_context),
            )
            intent_with_context = f"{step_context}\n\n---\n\n{intent}"
        else:
            intent_with_context = intent

        # Build plan summary for [Overall Plan] block (step id + intent, one line each)
        plan_summary_lines: list[str] = []
        for s in steps_list:
            sid = s.get('step_id', '?')
            s_intent = str(s.get('intent', '') or s.get('goal', ''))[:80]
            plan_summary_lines.append(f'  Step {sid}: {s_intent}')
        plan_summary = '\n'.join(plan_summary_lines)

        step_prompt = self._build_step_prompt(
            intent_with_context,
            fallback,
            original_intent=state.get('goal', ''),
            step_id=step_id,
            total_steps=len(steps_list),
            plan_summary=plan_summary,
        )
        try:
            solver.set_run_dir(workspaces)
            step_task = self._build_task_with_dialog_history(
                step_prompt,
                f"{task_id}_step_{step_id}",
            )
            if step_task is not None:
                result = solver.run(task=step_task)
            else:
                result = solver.run(step_prompt, task_id=f"{task_id}_step_{step_id}")
            summary = self._summarize_solver_result(result, max_len=1000)
            result_info['result_summary'] = summary[:200]
            self._emit(
                'Planner',
                'status_stages',
                {
                    'total': len(steps_list),
                    'current': step_id,
                    'step_id': step_id,
                    'intent': intent[:120] if intent else '',
                    'status': 'done',
                },
            )
            llm_verifier = self._llm_verify_step_outcome(
                intent,
                summary,
                state=state,
                task_id=task_id,
            )
            result_info['llm_verifier'] = llm_verifier
            if llm_verifier.get('needs_replan'):
                result_info['status'] = 'failed'
                result_info['replan_requested'] = True
                result_info['replan_reason'] = (
                    f"Step {step_id}: {llm_verifier.get('reason', 'step outcome requires replanning')}"
                )
                return result_info
            if self._is_quality_critical_step(intent, state=state, task_id=task_id):
                workspace_dir = self._task_workspace_dir(task_id)
                quality_files = self._collect_quality_files(
                    step_dir, workspace_dir, summary
                )
                before_count = int(state.get('literature_entry_count', 0) or 0)
                self._append_literature_index(
                    state,
                    task_id,
                    source=f"step:{step_id}:summary",
                    text=summary,
                )
                for quality_file in quality_files:
                    try:
                        text = quality_file.read_text(encoding='utf-8')
                    except Exception:
                        continue
                    self._append_literature_index(
                        state,
                        task_id,
                        source=f"step:{step_id}:file:{quality_file.name}",
                        text=text[:200000],
                    )
                evidence_delta = (
                    int(state.get('literature_entry_count', 0) or 0) - before_count
                )
                survey_failed, survey_reason = self._detect_survey_quality_failure(
                    intent=intent,
                    quality_files=quality_files,
                    evidence_delta=max(0, evidence_delta),
                    state=state,
                    task_id=task_id,
                )
                if survey_failed:
                    result_info['status'] = 'failed'
                    result_info['replan_requested'] = True
                    result_info['replan_reason'] = f"Step {step_id}: {survey_reason}"
                    self._append_journal(
                        state,
                        task_id,
                        phase='executing',
                        title=f"Step {step_id} quality gate failed",
                        body=survey_reason,
                    )
                    return result_info
                self._append_journal(
                    state,
                    task_id,
                    phase='executing',
                    title=f"Step {step_id} quality gate passed",
                    body=f"evidence_delta={max(0, evidence_delta)}",
                )
            failed, fail_reason = self._detect_manuscript_validation_failure(
                intent,
                summary,
            )
            if failed:
                result_info['status'] = 'failed'
                result_info['replan_requested'] = True
                result_info['replan_reason'] = f"Step {step_id}: {fail_reason}"
        except Exception as e:
            self.logger.error('[Planner] Step %s failed: %s', step_id, e)
            print('\033[93m[Planner] Step failed. Attempting fallback...\033[0m')
            if self._execute_fallback(
                step,
                solver,
                workspaces,
                state.get('plan') if isinstance(state, dict) else None,
            ):
                result_info['fallback_succeeded'] = True
                if self._is_quality_critical_step(intent, state=state, task_id=task_id):
                    result_info['status'] = 'failed'
                    result_info['result_summary'] = (
                        'fallback_used_for_quality_critical_step'
                    )
                    result_info['replan_requested'] = True
                    result_info['replan_reason'] = (
                        f"Step {step_id}: fallback used in a quality-critical step; "
                        're-run primary deep-survey/literature path with stronger evidence accumulation.'
                    )
                else:
                    result_info['result_summary'] = 'completed_via_fallback'
            else:
                result_info['status'] = 'failed'
                result_info['result_summary'] = str(e)[:200]
                print('\033[91m[Planner] Step and fallback failed.\033[0m')
        return result_info

    def _scan_workspace_files(self) -> list[str]:
        """List top-level workspace files for pre-check context."""
        workspace = self._run_dir_path()
        files: list[str] = []
        try:
            for path in workspace.iterdir():
                if path.name.startswith('.') or path.name == '__pycache__':
                    continue
                if path.is_file():
                    files.append(str(path.relative_to(workspace)))
                elif path.is_dir():
                    for child in path.iterdir():
                        if child.is_file():
                            files.append(str(child.relative_to(workspace)))
        except Exception as e:
            self.logger.debug('Workspace scan failed (non-critical): %s', e)
        return files[:100]

    def _assess_readiness(
        self, task_description: str, state: dict[str, Any], task_id: str
    ) -> dict[str, Any]:
        """Use LLM to assess whether the task is ready to plan."""
        workspace_files = self._scan_workspace_files()
        files_str = (
            '\n'.join(f"  - {file}" for file in workspace_files)
            if workspace_files
            else '  (empty workspace)'
        )

        user_content = f"""TASK DESCRIPTION:
{task_description}

WORKSPACE FILES:
{files_str}

Assess whether this task can be planned immediately or needs preliminary work. Output JSON only."""

        dialog = Dialog(
            messages=[
                SystemMessage(content=self._pre_check_system),
                UserMessage(content=user_content),
            ],
            tools=[],
        )
        try:
            if not self._consume_turns(state, task_id, 1):
                self._fail_max_turns_exceeded(task_id, state)
                return {
                    'ready_to_plan': False,
                    'prerequisites': [],
                    'reasoning': 'max_turns_exceeded',
                }
            reply = self._stream_llm(dialog, 'Planner', 'pre_check')
            raw = _extract_json_from_content(reply.content or '')
            if raw:
                return json.loads(raw)
            self._emit('Planner', 'thought', f"[Pre-check] {reply.content or ''}")
        except Exception as e:
            self.logger.warning(
                '[Pre-check] Assessment failed, proceeding to plan: %s',
                e,
            )
        return {
            'ready_to_plan': False,
            'prerequisites': [],
            'reasoning': 'Assessment failed; defaulting to not ready.',
        }

    def _execute_prerequisites(
        self,
        prerequisites: list[dict[str, Any]],
        task_description: str,
        task_id: str,
        state: dict[str, Any],
    ) -> str:
        """Run prerequisite tasks via ``DirectSolver`` and collect context."""
        if not prerequisites:
            return ''

        workspaces = self._run_dir_path() / 'workspaces' / task_id
        pre_check_dir = workspaces / 'pre_check'
        pre_check_dir.mkdir(parents=True, exist_ok=True)

        solver = DirectSolver(self.agent, self.config)
        solver.set_run_dir(pre_check_dir)

        collected_context: list[str] = []
        for index, prereq in enumerate(prerequisites):
            prereq_type = prereq.get('type', 'unknown')
            description = prereq.get('description', '')
            target = prereq.get('target', '')

            if prereq_type == 'clarify_task':
                self.logger.info(
                    '[Pre-check] clarify_task (fast path, no agent): %s',
                    description[:120],
                )
                self._emit(
                    'Planner',
                    'thought',
                    f"[Pre-check] Clarification noted (no agent needed): {description}",
                )
                collected_context.append(
                    f"[Prerequisite {index + 1}: clarify_task] {description}"
                    + (f"\nTarget: {target}" if target else '')
                )
                self._append_journal(
                    state,
                    task_id,
                    phase='pre_check',
                    title=(
                        f"Prerequisite {index + 1}/{len(prerequisites)} "
                        '(clarify_task, fast path)'
                    ),
                    body=f"{description}" + (f"\nTarget: {target}" if target else ''),
                )
                continue

            if not self._consume_turns(state, task_id, 1):
                self._fail_max_turns_exceeded(task_id, state)
                break
            if hasattr(self.agent, '_tool_guard'):
                self.agent._tool_guard.reset_loop_history()

            self.logger.info(
                '[Pre-check] Running prerequisite %d/%d: [%s] %s',
                index + 1,
                len(prerequisites),
                prereq_type,
                description[:80],
            )
            self._emit(
                'Planner',
                'thought',
                f"[Pre-check] Prerequisite {index + 1}/{len(prerequisites)}: {description}",
            )
            self._emit('Planner', 'exp_run', 'DirectSolver (pre-check)')

            scope_prefix = (
                'SCOPE: You are in the PRE-CHECK phase. Your ONLY job is to collect '
                'the specific information described below to help the planner create '
                'a better plan. Do NOT attempt to execute the full user task, build '
                'datasets, generate reports, or perform any work beyond what is '
                'explicitly described in this prerequisite. When you have collected '
                'the requested information, call finish immediately.\n\n'
            )

            if prereq_type == 'parse_pdf':
                body = (
                    'Parse the following PDF file and extract all relevant information '
                    'for planning a research task. Use '
                    'mat_doc_extract_material_data_from_pdf (MCP tool) as the primary '
                    'method. Extract: crystal structures, computational methods, '
                    'software used, key parameters (k-mesh, cutoff, functional, '
                    f'pseudopotentials), target properties/results. File: {target}. '
                    'After extraction, summarize all findings clearly.'
                )
            elif prereq_type == 'parse_files':
                body = (
                    'Read and parse the following files to extract information needed '
                    f'for planning: {target}. For PDFs, use mat_doc MCP tools first. '
                    'Summarize key findings.'
                )
            else:
                body = (
                    f"Complete this prerequisite task: {description}. Target: {target}."
                )

            prompt = scope_prefix + body

            try:
                precheck_task = self._build_task_with_dialog_history(
                    prompt,
                    f"{task_id}_precheck_{index}",
                )
                if precheck_task is not None:
                    result = solver.run(task=precheck_task)
                else:
                    result = solver.run(prompt, task_id=f"{task_id}_precheck_{index}")
                summary = self._summarize_solver_result(result, max_len=2000)
                collected_context.append(
                    f"[Prerequisite {index + 1}: {prereq_type}] {description}\nResult: {summary}"
                )
                self._append_journal(
                    state,
                    task_id,
                    phase='pre_check',
                    title=f"Prerequisite {index + 1}/{len(prerequisites)} completed",
                    body=f"type={prereq_type}\n{description}\n\nResult summary:\n{summary}",
                )
                self._append_literature_index(
                    state,
                    task_id,
                    source=f"precheck:{prereq_type}",
                    text=summary,
                )
            except Exception as e:
                self.logger.warning(
                    '[Pre-check] Prerequisite %d failed: %s', index + 1, e
                )
                collected_context.append(
                    f"[Prerequisite {index + 1}: {prereq_type}] FAILED: {e}"
                )
                self._append_journal(
                    state,
                    task_id,
                    phase='pre_check',
                    title=f"Prerequisite {index + 1}/{len(prerequisites)} failed",
                    body=f"type={prereq_type}\n{description}\n\nError: {e}",
                )

        return '\n\n'.join(collected_context)
