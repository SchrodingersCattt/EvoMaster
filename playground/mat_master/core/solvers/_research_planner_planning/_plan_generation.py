"""Plan generation, scope validation, and JSON repair."""

import json
from typing import Any

from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from ....prompts.build_prompt import LANGUAGE_RULE
from ..plan_utils import (
    _complete_truncated_json,
    _extract_json_from_content,
    _normalize_plan,
    _strip_last_incomplete_step,
    _try_parse_json,
)


class ResearchPlannerPlanningPlanGenMixin:
    def _validate_plan_against_user_phase(
        self,
        plan: dict[str, Any],
        goal: str,
        *,
        state: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> tuple[bool, str]:
        """Use LLM to judge whether the plan drifts beyond the user's requested scope."""
        if plan.get('status') == 'REFUSED':
            return True, ''
        steps = plan.get('steps') or []
        if not steps:
            return True, ''
        plan_json = json.dumps({'steps': steps}, ensure_ascii=False, indent=2)
        prompt = f"""USER GOAL:
{goal}

PLAN:
{plan_json}

Question: Does this plan clearly drift into a later-phase report/manuscript deliverable instead of staying focused on the user's current requested phase?

Return exactly one JSON object:
{{
  "passed": true,
  "reason": ""
}}

Rules:
- Be conservative. If the goal does not clearly define a current phase boundary, return passed=true.
- Fail only when the plan clearly shifts the main deliverable away from the user's currently requested deliverables.
- Do not invent missing deliverables or infer file names that are not explicitly stated.
"""
        dialog = Dialog(
            messages=[
                SystemMessage(
                    content=f"You are a concise plan-alignment validator. Output only JSON.\n\n{LANGUAGE_RULE}"
                ),
                UserMessage(content=prompt),
            ],
            tools=[],
        )
        try:
            if state is not None and task_id is not None:
                if not self._consume_turns(state, task_id, 1):
                    self._fail_max_turns_exceeded(task_id, state)
                    return True, ''
            reply = self.agent.llm.query(dialog)
            raw = _extract_json_from_content(reply.content or '')
            if not raw:
                return True, ''
            result = json.loads(raw)
            passed = bool(result.get('passed', True))
            reason = str(result.get('reason', '') or '')
            return passed, reason
        except Exception as e:
            self.logger.debug('Plan scope validation skipped: %s', e)
            return True, ''

    def _generate_plan(
        self, goal: str, state: dict[str, Any] | None = None, task_id: str | None = None
    ) -> dict[str, Any]:
        """Produce DEG via LLM with runtime context and safety validation."""
        system = self._load_system_prompt()
        user = self._build_context_prompt(goal)
        dialog = Dialog(
            messages=[SystemMessage(content=system), UserMessage(content=user)],
            tools=[],
        )
        if state is not None and task_id is not None:
            if not self._consume_turns(state, task_id, 1):
                self._fail_max_turns_exceeded(task_id, state)
                return {
                    'status': 'REFUSED',
                    'refusal_reason': 'max_turns_exceeded',
                }
        try:
            reply = self._stream_llm(dialog, 'Planner', 'planning')
            self._emit('Planner', 'thought', reply.content or '')
            if task_id is not None:
                try:
                    draft_path = (
                        f"{self._planner_hidden_dir(task_id)}/raw_plan_draft.txt"
                    )
                    self._file_io.write_text(draft_path, reply.content or '')
                except Exception as save_err:
                    self.logger.warning(
                        '[Planner] Failed to save raw_plan_draft.txt: %s',
                        save_err,
                    )
            raw = _extract_json_from_content(reply.content or '')
            if not raw:
                return {
                    'status': 'REFUSED',
                    'refusal_reason': 'Planner output contained no valid JSON.',
                }
            try:
                plan = _try_parse_json(raw, self.logger)
            except json.JSONDecodeError as e:
                self.logger.error(
                    'Plan JSON parse failed (all repair stages exhausted): %s',
                    e,
                )
                if task_id is not None:
                    self.logger.info(
                        '[Planner] Attempting LLM-based plan repair for truncated JSON...'
                    )
                    repaired = self._repair_plan_from_file(
                        task_id, reply.content or '', state=state
                    )
                    if repaired is not None:
                        plan = repaired
                    else:
                        return {
                            'status': 'REFUSED',
                            'refusal_reason': f"Invalid JSON: {e}",
                        }
                else:
                    return {'status': 'REFUSED', 'refusal_reason': f"Invalid JSON: {e}"}
        except json.JSONDecodeError as e:
            self.logger.error('Plan JSON parse failed: %s', e)
            return {'status': 'REFUSED', 'refusal_reason': f"Invalid JSON: {e}"}
        except Exception as e:
            self.logger.error('Plan generation failed: %s', e)
            return {'status': 'REFUSED', 'refusal_reason': str(e)}
        plan = _normalize_plan(plan, self.max_steps)
        if not plan.get('steps'):
            plan['status'] = 'REFUSED'
            plan['refusal_reason'] = (
                plan.get('refusal_reason') or 'Plan must have at least one step.'
            )
        plan = self._validate_plan_safety(plan)
        plan = self._enforce_manuscript_plan_consistency(plan, goal)
        if plan.get('status') != 'REFUSED':
            phase_ok, phase_reason = self._validate_plan_against_user_phase(
                plan,
                goal,
                state=state,
                task_id=task_id,
            )
            if not phase_ok and phase_reason:
                self.logger.info(
                    '[Planner] Scope validation: %s; requesting gentle revision.',
                    phase_reason[:80],
                )
                plan = self._revise_plan(
                    goal,
                    plan,
                    (
                        "The current plan appears to go beyond the user's requested "
                        f"scope. {phase_reason} Revise the plan so executable steps "
                        "stay within the user's current requested boundary, and move "
                        'later-phase or out-of-scope work into plan_report notes instead '
                        'of runnable steps.'
                    ),
                    state=state,
                    task_id=task_id,
                )
        return plan

    def _repair_plan_from_file(
        self,
        task_id: str,
        raw_content: str,
        *,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Repair truncated plan JSON using pure Python recovery heuristics."""
        raw = _extract_json_from_content(raw_content) or raw_content

        completed = _complete_truncated_json(raw)
        if completed is not None:
            try:
                plan = _try_parse_json(completed, self.logger)
                plan = _normalize_plan(plan, self.max_steps)
                if plan.get('steps'):
                    self.logger.info(
                        '[Planner] _repair_plan_from_file: bracket completion succeeded'
                    )
                    return plan
            except Exception:
                pass

        stripped = _strip_last_incomplete_step(raw)
        if stripped and stripped != raw:
            completed2 = _complete_truncated_json(stripped)
            target = completed2 if completed2 is not None else stripped
            try:
                plan = _try_parse_json(target, self.logger)
                plan = _normalize_plan(plan, self.max_steps)
                if plan.get('steps'):
                    self.logger.info(
                        '[Planner] _repair_plan_from_file: strip+complete succeeded'
                    )
                    return plan
            except Exception:
                pass

        self.logger.error(
            '[Planner] _repair_plan_from_file: all Python repair attempts failed'
        )
        return None
