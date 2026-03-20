"""Plan revision via LLM or str_replace_editor on ``current_plan.json``."""

import json
from typing import Any

from evomaster.utils.types import (
    Dialog,
    FunctionSpec,
    SystemMessage,
    ToolSpec,
    UserMessage,
)

from ..plan_utils import (
    _STR_REPLACE_TOOL_SPEC,
    _extract_json_from_content,
    _normalize_plan,
    _plan_to_external_schema,
    _str_replace_in_text,
    _try_parse_json,
)


class ResearchPlannerPlanningRevisionMixin:
    def _revise_plan_from_file(
        self,
        goal: str,
        task_id: str | None,
        current_plan: dict[str, Any],
        user_feedback: str,
        *,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Revise the saved plan using only targeted ``str_replace_editor`` calls."""
        plan_path = (
            f"{self._planner_hidden_dir(task_id)}/current_plan.json"
            if task_id is not None
            else None
        )
        if plan_path is not None and self._file_io.exists(plan_path):
            try:
                plan_text = self._file_io.read_text(plan_path)
            except Exception as e:
                self.logger.warning(
                    '[Planner] _revise_plan_from_file: cannot read file: %s',
                    e,
                )
                plan_text = json.dumps(
                    _plan_to_external_schema(current_plan), ensure_ascii=False, indent=2
                )
        else:
            plan_text = json.dumps(
                _plan_to_external_schema(current_plan), ensure_ascii=False, indent=2
            )

        prompt = (
            f'Goal: {goal}\n\n'
            f'User revision request: {user_feedback}\n\n'
            'Here is the current current_plan.json:\n\n'
            f'{plan_text}\n\n'
            'Use the str_replace_editor tool to make the minimal targeted changes '
            'needed to apply the revision request. '
            'Do NOT rewrite the whole plan — only change what is necessary. '
            'You may call the tool multiple times for multiple changes.'
        )
        tool_spec = ToolSpec(
            type='function',
            function=FunctionSpec(
                name=_STR_REPLACE_TOOL_SPEC['function']['name'],
                description=_STR_REPLACE_TOOL_SPEC['function']['description'],
                parameters=_STR_REPLACE_TOOL_SPEC['function']['parameters'],
            ),
        )
        dialog = Dialog(messages=[UserMessage(content=prompt)], tools=[tool_spec])
        if state is not None and task_id is not None:
            if not self._consume_turns(state, task_id, 1):
                self._fail_max_turns_exceeded(task_id, state)
                return {
                    **current_plan,
                    'status': 'REFUSED',
                    'refusal_reason': 'max_turns_exceeded',
                }
        try:
            reply = self._stream_llm(dialog, 'Planner', 'revision')
            self._emit('Planner', 'thought', reply.content or '')

            tool_calls = reply.tool_calls or []
            if not tool_calls:
                self.logger.warning(
                    '[Planner] _revise_plan_from_file: LLM made no tool calls; keeping original plan'
                )
                return current_plan

            revised_text = plan_text
            applied = 0
            for tool_call in tool_calls:
                if tool_call.function.name != 'str_replace_editor':
                    self.logger.warning(
                        '[Planner] _revise_plan_from_file: unexpected tool call %r; skipping',
                        tool_call.function.name,
                    )
                    continue
                try:
                    args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    self.logger.warning(
                        '[Planner] _revise_plan_from_file: cannot parse tool args: %s',
                        e,
                    )
                    continue
                old_str = args.get('old_str', '')
                new_str = args.get('new_str', '')
                if not old_str:
                    self.logger.warning(
                        '[Planner] _revise_plan_from_file: empty old_str; skipping'
                    )
                    continue
                revised_text, err = _str_replace_in_text(
                    revised_text,
                    old_str,
                    new_str,
                    self.logger,
                )
                if err:
                    self.logger.warning(
                        '[Planner] _revise_plan_from_file: str_replace failed: %s',
                        err,
                    )
                else:
                    applied += 1

            if applied == 0:
                self.logger.warning(
                    '[Planner] _revise_plan_from_file: no str_replace succeeded; keeping original plan'
                )
                return current_plan

            try:
                revised = _try_parse_json(revised_text, self.logger)
            except json.JSONDecodeError as e:
                self.logger.error(
                    '[Planner] _revise_plan_from_file: revised_text is not valid JSON after %d str_replace(s): %s',
                    applied,
                    e,
                )
                self.logger.debug(
                    '[Planner] _revise_plan_from_file: revised_text snippet (first 500 chars): %s',
                    revised_text[:500],
                )
                return current_plan
            revised = _normalize_plan(revised, self.max_steps)
            if plan_path is not None:
                try:
                    self._file_io.write_text(
                        plan_path,
                        json.dumps(
                            _plan_to_external_schema(revised),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                except Exception as e:
                    self.logger.warning(
                        '[Planner] _revise_plan_from_file: cannot write back: %s',
                        e,
                    )
            self.logger.info(
                '[Planner] _revise_plan_from_file: applied %d str_replace(s) successfully',
                applied,
            )
            return revised
        except Exception as e:
            self.logger.error(
                '[Planner] _revise_plan_from_file failed (%s); keeping original plan',
                e,
            )
            return current_plan

    def _revise_plan(
        self,
        goal: str,
        current_plan: dict[str, Any],
        user_feedback: str,
        *,
        state: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Revise plan from feedback using the same schema as generation."""
        system = self._load_system_prompt()
        external = _plan_to_external_schema(current_plan)
        plan_json = json.dumps(external, ensure_ascii=False, indent=2)
        user = (
            f"REVISION REQUEST\nOriginal goal: {goal}\n\nCurrent plan (JSON):\n"
            f"{plan_json}\n\nUser feedback: {user_feedback}\n\n"
            'Output the revised plan as a single JSON object '
            '(same schema: execution_graph, fidelity_level). No other text.'
        )
        dialog = Dialog(
            messages=[SystemMessage(content=system), UserMessage(content=user)],
            tools=[],
        )
        if state is not None and task_id is not None:
            if not self._consume_turns(state, task_id, 1):
                self._fail_max_turns_exceeded(task_id, state)
                return {
                    **current_plan,
                    'status': 'REFUSED',
                    'refusal_reason': 'max_turns_exceeded',
                }
        try:
            reply = self._stream_llm(dialog, 'Planner', 'revision')
            self._emit('Planner', 'thought', reply.content or '')
            raw = _extract_json_from_content(reply.content or '')
            if not raw:
                return {
                    **current_plan,
                    'status': 'REFUSED',
                    'refusal_reason': 'Revision output contained no valid JSON.',
                }
            try:
                plan = _try_parse_json(raw, self.logger)
            except json.JSONDecodeError as e:
                self.logger.error(
                    'Revision JSON parse failed (all repair stages exhausted): %s',
                    e,
                )
                return {
                    **current_plan,
                    'status': 'REFUSED',
                    'refusal_reason': f"Invalid JSON: {e}",
                }
        except json.JSONDecodeError as e:
            self.logger.error('Revision JSON parse failed: %s', e)
            return {
                **current_plan,
                'status': 'REFUSED',
                'refusal_reason': f"Invalid JSON: {e}",
            }
        except Exception as e:
            self.logger.error('Plan revision failed: %s', e)
            return {**current_plan, 'status': 'REFUSED', 'refusal_reason': str(e)}
        plan = _normalize_plan(plan, self.max_steps)
        if not plan.get('steps'):
            plan['status'] = 'REFUSED'
            plan['refusal_reason'] = (
                plan.get('refusal_reason') or 'Plan must have at least one step.'
            )
        plan = self._validate_plan_safety(plan)
        plan = self._enforce_manuscript_plan_consistency(plan, goal)
        return plan
