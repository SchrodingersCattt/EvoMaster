"""Single-step execution (run, summarize, prompts) for ``ResearchPlanner``."""

import json
import re
import traceback
from pathlib import Path
from typing import Any

from ..plan_utils import _get_mat_master_config

try:
    from ...exp import SkillEvolutionExp

    _HAS_EVOLUTION = True
except ImportError:
    SkillEvolutionExp = None
    _HAS_EVOLUTION = False

# status_stages carries intent for the UI/SSE; avoid multi‑MB payloads if a plan is malformed.
_STATUS_STAGES_INTENT_MAX = 32_768


def _intent_for_status_stages(raw: Any) -> str:
    if raw is None or raw == '':
        return ''
    s = raw if isinstance(raw, str) else str(raw)
    if len(s) <= _STATUS_STAGES_INTENT_MAX:
        return s
    return s[: _STATUS_STAGES_INTENT_MAX - 1] + '…'


class ResearchPlannerStepExecutionMixin:
    """Mixin for executing a single plan step: artifacts, summarize, prompt, run."""

    def _extract_produced_artifacts(self) -> list[str]:
        """Extract file paths produced during the most recent agent run.

        Reads ``saved_path`` and ``downloaded_files`` from the agent's
        ``ExecutionJournal`` entries, deduplicates, and returns a flat list.
        The journal is reset at the start of each ``agent.run()`` call, so
        after a step's ``DirectSolver.run()`` completes the journal contains
        exactly that step's tool-call records.
        """
        journal = getattr(self.agent, '_execution_journal', None)
        if journal is None:
            return []
        seen: set[str] = set()
        artifacts: list[str] = []
        for entry in journal.entries:
            path = entry.get('saved_path')
            if path and path not in seen:
                seen.add(path)
                artifacts.append(path)
            for f in entry.get('downloaded_files') or []:
                if isinstance(f, str) and f not in seen:
                    seen.add(f)
                    artifacts.append(f)
                elif isinstance(f, dict):
                    fp = f.get('local_path') or f.get('path') or ''
                    if fp and fp not in seen:
                        seen.add(fp)
                        artifacts.append(fp)
        return artifacts

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
            trajectory = result.get('trajectory')
            if trajectory is not None:
                final_answer = getattr(trajectory, 'final_answer', None)
                if isinstance(final_answer, str) and final_answer.strip():
                    return final_answer.strip()[:max_len]
                steps = getattr(trajectory, 'steps', None)
                if steps:
                    last_obs = getattr(steps[-1], 'observation', None)
                    if isinstance(last_obs, str) and last_obs.strip():
                        return last_obs.strip()[:max_len]
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
        """Build executor step prompt with [Original Intent] / [Task of This Step] structure."""
        parts: list[str] = []

        if original_intent:
            parts.append(
                '[Original Intent]\n'
                f'The user\'s overall goal: {original_intent}\n'
                '(Read-only context — do NOT attempt to complete the full goal in this step.)'
            )

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

        if plan_summary:
            parts.append(
                '[Overall Plan — for context only, do NOT execute ahead]\n'
                f'{plan_summary}'
            )

        parts.append(
            'At the end of the step, explicitly report one of: completed, partial, or blocked. '
            'List the concrete outputs you produced or saved, and do not claim completion '
            'unless the requested deliverable was actually achieved.'
        )

        return '\n\n'.join(parts)

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
                'intent': _intent_for_status_stages(intent),
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

        use_sub_agent = (
            getattr(self, '_sub_agent_factory', None) is not None
            and self._sub_agent_factory.enabled
        )

        if use_sub_agent:
            history = state.get('history', [])
            if history:
                lines: list[str] = []
                for entry in history:
                    prev_id = entry.get('step', '?')
                    if entry.get('error'):
                        err = str(entry['error'])[:120]
                        lines.append(f'  Step {prev_id} [FAILED]: {err}')
                    else:
                        summary = str(entry.get('result_summary', 'done'))[:120]
                        lines.append(f'  Step {prev_id} [OK]: {summary}')
                    produced = entry.get('produced_artifacts', [])
                    if produced:
                        lines.append(f'    Produced files: {", ".join(produced[-10:])}')
                step_context = '[Previous Steps Results]\n' + '\n'.join(lines)
            else:
                step_context = ''
        else:
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

        if use_sub_agent:
            step_prompt = self._build_step_prompt(
                intent_with_context,
                fallback,
                original_intent='',
                step_id=step_id,
                total_steps=len(steps_list),
                plan_summary='',
            )
        else:
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

        _post_run_checkpoint = 'start'
        try:
            if use_sub_agent:
                handle = self._sub_agent_factory.create(
                    step_id=step_id,
                    workspaces=workspaces,
                )
                handle.prepare()
                result = handle.run(step_prompt, f"{task_id}_step_{step_id}")
                solver = handle.solver
            else:
                solver.set_run_dir(workspaces)
                step_task = self._build_task_with_dialog_history(
                    step_prompt,
                    f"{task_id}_step_{step_id}",
                )
                if step_task is not None:
                    result = solver.run(task=step_task)
                else:
                    result = solver.run(
                        step_prompt, task_id=f"{task_id}_step_{step_id}"
                    )
            _post_run_checkpoint = 'after_run'
            self.logger.info(
                '[Planner] Step %s post-run checkpoint: %s',
                step_id,
                _post_run_checkpoint,
            )
            summary = self._summarize_solver_result(result, max_len=2000)
            result_info['result_summary'] = summary[:2000]
            preview = (summary[:500] + '…') if len(summary) > 500 else summary
            self.logger.info(
                '[Planner] Step %s summarize_solver_result: len=%d preview=%r',
                step_id,
                len(summary),
                preview,
            )
            if isinstance(result, dict):
                self.logger.debug(
                    '[Planner] Step %s solver.run result keys=%s status=%r',
                    step_id,
                    sorted(result.keys()),
                    result.get('status'),
                )
            _post_run_checkpoint = 'after_summarize'
            self.logger.info(
                '[Planner] Step %s post-run checkpoint: %s',
                step_id,
                _post_run_checkpoint,
            )
            produced = self._extract_produced_artifacts()
            if produced:
                result_info['produced_artifacts'] = produced
            _post_run_checkpoint = 'after_extract_artifacts'
            self._emit(
                'Planner',
                'status_stages',
                {
                    'total': len(steps_list),
                    'current': step_id,
                    'step_id': step_id,
                    'intent': _intent_for_status_stages(intent),
                    'status': 'done',
                },
            )
            _post_run_checkpoint = 'before_llm_verify'
            self.logger.info(
                '[Planner] Step %s post-run checkpoint: %s',
                step_id,
                _post_run_checkpoint,
            )
            llm_verifier = self._llm_verify_step_outcome(
                intent,
                summary,
                state=state,
                task_id=task_id,
            )
            _post_run_checkpoint = 'after_llm_verify'
            self.logger.info(
                '[Planner] Step %s post-run checkpoint: %s',
                step_id,
                _post_run_checkpoint,
            )
            result_info['llm_verifier'] = llm_verifier
            self.logger.info(
                '[Planner] Step %s LLM verifier result: status=%s needs_replan=%s reason=%s',
                step_id,
                llm_verifier.get('status', ''),
                llm_verifier.get('needs_replan', False),
                (llm_verifier.get('reason') or '')[:200],
            )
            if llm_verifier.get('needs_replan'):
                result_info['status'] = 'failed'
                result_info['replan_requested'] = True
                result_info['replan_reason'] = (
                    f"Step {step_id}: {llm_verifier.get('reason', 'step outcome requires replanning')}"
                )
                return result_info
            if self._is_quality_critical_step(intent, state=state, task_id=task_id):
                _post_run_checkpoint = 'inside_quality_critical'
                workspace_dir = Path(self._agent_workspace_dir(task_id))
                quality_files = self._collect_quality_files(
                    step_dir, workspace_dir, summary
                )
                _qf_paths = [str(p) for p in quality_files]
                _qf_max = 40
                if len(_qf_paths) > _qf_max:
                    _qf_log = _qf_paths[:_qf_max] + [
                        f'… (+{len(_qf_paths) - _qf_max} more)'
                    ]
                else:
                    _qf_log = _qf_paths
                self.logger.info(
                    '[Planner] Step %s quality_files for literature_index: n=%d paths=%s',
                    step_id,
                    len(quality_files),
                    _qf_log,
                )
                from evomaster.agent.session.ssh import SSHSession  # noqa: PLC0415

                _sess = getattr(getattr(self, 'agent', None), 'session', None)
                _file_io = getattr(self, '_file_io', None)
                _use_remote_read = (
                    isinstance(_sess, SSHSession) and _file_io is not None
                )
                self.logger.info(
                    '[Planner] Step %s literature_index ingest: use_remote_read=%s '
                    '(SSHSession + _file_io) summary_chars=%d',
                    step_id,
                    _use_remote_read,
                    len(summary or ''),
                )
                before_count = int(state.get('literature_entry_count', 0) or 0)
                summary_added = self._append_literature_index(
                    state,
                    task_id,
                    source=f"step:{step_id}:summary",
                    text=summary,
                )
                self.logger.info(
                    '[Planner] Step %s literature_index after summary: added_urls=%d '
                    'literature_entry_count=%d',
                    step_id,
                    summary_added,
                    int(state.get('literature_entry_count', 0) or 0),
                )
                files_added_sum = 0
                files_read_ok = 0
                files_read_fail = 0
                for quality_file in quality_files:
                    path_str = str(quality_file)
                    text = None
                    try:
                        text = quality_file.read_text(encoding='utf-8')
                    except Exception as e:
                        files_read_fail += 1
                        self.logger.warning(
                            '[Planner] Step %s literature_index quality_file read failed '
                            '(Path.read_text local only; on SSH workspace this often '
                            'misses remote paths): path=%s err=%s: %s',
                            step_id,
                            path_str,
                            type(e).__name__,
                            e,
                        )
                        continue
                    files_read_ok += 1
                    slice_len = min(len(text), 200000)
                    added = self._append_literature_index(
                        state,
                        task_id,
                        source=f"step:{step_id}:file:{quality_file.name}",
                        text=text[:200000],
                    )
                    files_added_sum += added
                    self.logger.info(
                        '[Planner] Step %s literature_index quality_file ok: name=%s '
                        'path=%s chars=%d slice_used=%d added_urls=%d',
                        step_id,
                        quality_file.name,
                        path_str,
                        len(text),
                        slice_len,
                        added,
                    )
                after_count = int(state.get('literature_entry_count', 0) or 0)
                evidence_delta = after_count - before_count
                self.logger.info(
                    '[Planner] Step %s literature_index step totals: before=%d after=%d '
                    'delta=%d summary_added=%d files_read_ok=%d files_read_fail=%d '
                    'files_added_sum=%d',
                    step_id,
                    before_count,
                    after_count,
                    evidence_delta,
                    summary_added,
                    files_read_ok,
                    files_read_fail,
                    files_added_sum,
                )
                survey_failed, survey_reason = self._detect_survey_quality_failure(
                    intent=intent,
                    quality_files=quality_files,
                    evidence_delta=max(0, evidence_delta),
                    state=state,
                    task_id=task_id,
                )
                if survey_failed:
                    self.logger.info(
                        '[Planner] Step %s marked failed by survey_quality_failure: %s',
                        step_id,
                        (survey_reason or '')[:200],
                    )
                    if not quality_files:
                        survey_dir = workspace_dir / '_tmp' / 'surveys'
                        survey_exists = survey_dir.exists()
                        survey_mds = (
                            list(survey_dir.glob('*.md')) if survey_exists else []
                        )
                        self.logger.info(
                            '[Planner] Step %s quality_files empty: workspace_dir=%s step_dir=%s '
                            'survey_dir=%s survey_dir.exists=%s survey_dir.glob(*.md)=%s',
                            step_id,
                            workspace_dir,
                            step_dir,
                            survey_dir,
                            survey_exists,
                            [str(p) for p in survey_mds[:20]],
                        )
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
            _post_run_checkpoint = 'before_manuscript_validation'
            failed, fail_reason = self._detect_manuscript_validation_failure(
                intent,
                summary,
            )
            if failed:
                self.logger.info(
                    '[Planner] Step %s marked failed by manuscript_validation_failure: %s',
                    step_id,
                    (fail_reason or '')[:200],
                )
                result_info['status'] = 'failed'
                result_info['replan_requested'] = True
                result_info['replan_reason'] = f"Step {step_id}: {fail_reason}"
        except Exception as e:
            self.logger.exception(
                '[Planner] Step %s failed at checkpoint %s: %s',
                step_id,
                _post_run_checkpoint,
                e,
            )
            print(
                '\033[93m[Planner] Step failed at checkpoint %s. Attempting fallback...\033[0m'
                % _post_run_checkpoint
            )
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
                tb_lines = traceback.format_exc().strip().split('\n')
                tb_tail = (
                    '\n'.join(tb_lines[-4:])
                    if len(tb_lines) >= 4
                    else traceback.format_exc()
                )
                exc_str = str(e)[:150]
                result_info['result_summary'] = (
                    f"checkpoint={_post_run_checkpoint}; exception={exc_str}; traceback_tail={tb_tail[:400]}"
                )
                result_info['replan_reason'] = (
                    f"Step {step_id} failed at {_post_run_checkpoint}: {str(e)[:200]}"
                )
                print('\033[91m[Planner] Step and fallback failed.\033[0m')
        return result_info
