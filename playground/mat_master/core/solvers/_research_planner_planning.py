"""Planning and quality-gate helpers for ``ResearchPlanner``."""

import json
import re
from pathlib import Path
from typing import Any

from evomaster.utils.types import (
    Dialog,
    FunctionSpec,
    SystemMessage,
    ToolSpec,
    UserMessage,
)

from ...prompts.build_prompt import LANGUAGE_RULE
from ...skills._common.longtask_runtime import parse_prefixed_result_line
from ..constants import MANUSCRIPT_FAIL_MARKERS
from .direct_solver import _get_available_tool_names
from .plan_utils import (
    _STR_REPLACE_TOOL_SPEC,
    _complete_truncated_json,
    _extract_json_from_content,
    _get_mat_master_config,
    _normalize_plan,
    _plan_to_external_schema,
    _str_replace_in_text,
    _strip_last_incomplete_step,
    _try_parse_json,
)


class ResearchPlannerPlanningMixin:
    _MAPPING_PATTERN_TEMPLATES: list[str] = [
        r'{sw}\s*(?:→|->|-->|=>)\s*\w',
        r'(?:map|convert|replace|switch|redirect|translate|migrate)\s+{sw}',
        r'{sw}\s+(?:to|into|with)\s+(?:{{ALLOW_ALT}}|open[\s-]?source)',
        r'(?:originally|formerly|previously|instead of|rather than|not)\s+(?:in\s+|using\s+)?{sw}',
        r'(?:mapped|equivalent)\s+.*{sw}',
    ]

    _STRICT_PROFILE_SECTIONS: dict[str, list[str]] = {
        'computational_report': ['Methods', 'Results and Discussion', 'References'],
        'patent': [
            'Technical Field',
            'Background Art',
            'Summary of Invention',
            'Detailed Description',
            'Claims',
            'Abstract',
        ],
    }

    _MANUSCRIPT_FAIL_MARKERS = MANUSCRIPT_FAIL_MARKERS

    def _build_context_prompt(self, task_description: str) -> str:
        """Build runtime context for the planner prompt."""
        mat = _get_mat_master_config(self.config)
        crp_cfg = mat.get('crp', {})
        active_licenses = crp_cfg.get('licenses', [])
        task_lower = task_description.lower()
        screening_kw = [
            'quick',
            'fast',
            'screen',
            'rough',
            'coarse',
            'preliminary',
            '快速',
            '粗略',
        ]
        exploratory_kw = ['初步', '探索', 'exploratory', '试试', 'try out', 'pilot']
        if any(word in task_lower for word in screening_kw):
            fidelity = 'Screening'
        elif any(word in task_lower for word in exploratory_kw):
            fidelity = 'Exploratory'
        else:
            fidelity = 'Production'
        context_data = {
            'RUNTIME_CONTEXT': {
                'Execution': {
                    'Planner_Runtime': 'local_service',
                    'Remote_Execution_Managed': True,
                },
                'License_Keys': active_licenses,
                'Internet_Access': True,
            },
            'REQUEST_CONFIG': {
                'Target_Fidelity': fidelity,
                'Max_Steps': self.max_steps,
            },
            'USER_INTENT': task_description,
        }
        tools_preview = _get_available_tool_names(self.agent)
        tools_str = ', '.join(tools_preview[:100]) if tools_preview else '(none)'
        return f"""# CURRENT RUNTIME STATE (JSON)
{json.dumps(context_data, indent=2, ensure_ascii=False)}

# AVAILABLE TOOLS (use exact names in tool_name)
{tools_str}

# INSTRUCTION
Analyze USER_INTENT against RUNTIME_CONTEXT and REQUEST_CONFIG. Generate the research plan in strict JSON format: plan_id, status, strategy_name, fidelity_level, execution_graph (each step has goal and step_type, no tool_name), and plan_report (summary, cost_assessment, risks, alternatives). No other text."""

    def _build_mapping_patterns(self) -> list[re.Pattern[str]]:
        """Build mapping patterns with allowed software names from the registry."""
        allow_alt = '|'.join(re.escape(name) for name in self._registry.software_names)
        patterns = []
        for template in self._MAPPING_PATTERN_TEMPLATES:
            filled = template.replace('{{ALLOW_ALT}}', allow_alt)
            patterns.append(re.compile(filled, re.IGNORECASE))
        return patterns

    def _is_mapping_context(self, text: str, sw: str) -> bool:
        """Return True if blocked software appears only in mapping/reference context."""
        for pattern in self._build_mapping_patterns():
            concrete = re.compile(
                pattern.pattern.replace('{sw}', re.escape(sw)), re.IGNORECASE
            )
            if concrete.search(text):
                return True
        return False

    def _validate_plan_safety(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Auto-redirect blocked software names to CRP-allowed alternatives."""
        if plan.get('status') == 'REFUSED':
            return plan
        crp_ctx = self._registry.crp_context_dict()
        block = crp_ctx['License_Registry']['Block_List']
        preferred = crp_ctx['Tool_Stack']
        redirect_map: dict[str, str] = {}
        for sw in block:
            sw_lower = sw.lower()
            if sw_lower in ('vasp', 'castep', 'wien2k'):
                redirect_map[sw] = preferred['Preferred_DFT']
            elif sw_lower == 'gaussian':
                redirect_map[sw] = preferred['Preferred_DFT']

        redirected_steps: list[int] = []
        for step in plan.get('steps', []):
            text = step.get('intent', '') or step.get('goal', '') or ''
            for sw in block:
                if sw.lower() not in text.lower():
                    continue
                if self._is_mapping_context(text, sw):
                    continue
                replacement = redirect_map.get(sw, preferred['Preferred_DFT'])
                step_id = step.get('step_id', '?')
                self.logger.info(
                    "[CRP] Auto-redirect step %s: '%s' → '%s'",
                    step_id,
                    sw,
                    replacement,
                )
                step['intent'] = re.sub(
                    re.escape(sw), replacement, step['intent'], flags=re.IGNORECASE
                )
                redirected_steps.append(step_id)

        if redirected_steps:
            self.logger.info(
                '[CRP] Redirected %d step(s): %s',
                len(redirected_steps),
                redirected_steps,
            )
            self._emit(
                'Planner',
                'thought',
                f"[CRP] Auto-redirected blocked software in step(s) {redirected_steps} to CRP-allowed alternatives.",
            )
            for step in plan.get('steps', []):
                text = (step.get('intent', '') or '').lower()
                for sw in block:
                    if sw.lower() in text and not self._is_mapping_context(text, sw):
                        self.logger.warning(
                            "[CRP] Step %s still references '%s' after redirect (may be in context description); proceeding anyway.",
                            step.get('step_id'),
                            sw,
                        )
        return plan

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
                        self._task_workspace_dir(task_id) / 'raw_plan_draft.txt'
                    )
                    draft_path.write_text(reply.content or '', encoding='utf-8')
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
            self._task_workspace_dir(task_id) / 'current_plan.json'
            if task_id is not None
            else None
        )
        if plan_path is not None and plan_path.exists():
            try:
                plan_text = plan_path.read_text(encoding='utf-8')
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
                    plan_path.write_text(
                        json.dumps(
                            _plan_to_external_schema(revised),
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding='utf-8',
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

    @staticmethod
    def _infer_manuscript_profile(text: str) -> str | None:
        """Infer intended manuscript profile from user intent text."""
        lowered = (text or '').lower()
        if any(key in lowered for key in ('专利', 'patent')):
            return 'patent'
        if any(key in lowered for key in ('thesis', '论文', '学位')):
            return 'thesis_section'
        if any(key in lowered for key in ('computational report', '计算报告')):
            return 'computational_report'
        if any(key in lowered for key in ('review', '综述')):
            return 'review'
        if any(key in lowered for key in ('technical report', '技术报告')):
            return 'technical_report'
        if any(key in lowered for key in ('grant', '基金')):
            return 'grant'
        if any(
            key in lowered
            for key in (
                'paper',
                'article',
                'research',
                'manuscript',
                'nature',
                'generic',
                'imrad',
                '论文',
                '文章',
                '研究报告',
            )
        ):
            return 'research_paper'
        return None

    def _enforce_manuscript_plan_consistency(
        self, plan: dict[str, Any], goal: str
    ) -> dict[str, Any]:
        """Ensure manuscript plan uses the right profile and required sections."""
        target_profile = self._infer_manuscript_profile(goal)
        if not target_profile or plan.get('status') == 'REFUSED':
            return plan

        steps = plan.get('steps', [])
        manuscript_steps = [
            step
            for step in steps
            if 'manuscript-scribe' in (step.get('intent', '') or '').lower()
        ]
        if not manuscript_steps:
            return plan

        template_re = re.compile(
            r'--template\s+["\']?([a-zA-Z_]+)["\']?',
            flags=re.IGNORECASE,
        )
        profile_re = re.compile(
            r'--profile\s+["\']?([a-zA-Z_]+)["\']?',
            flags=re.IGNORECASE,
        )
        rewritten_ids: list[int] = []
        for step in manuscript_steps:
            intent = step.get('intent', '')
            new_intent = template_re.sub(f"--template {target_profile}", intent)
            new_intent = profile_re.sub(f"--profile {target_profile}", new_intent)
            if new_intent != intent:
                step['intent'] = new_intent
                rewritten_ids.append(step.get('step_id', -1))
        if rewritten_ids:
            self._emit(
                'Planner',
                'thought',
                f"[Planner] Auto-aligned profile to '{target_profile}' in step(s) {rewritten_ids}.",
            )

        required = self._STRICT_PROFILE_SECTIONS.get(target_profile)
        if required:
            all_intents = ' '.join(step.get('intent', '') for step in manuscript_steps)
            missing = [
                section
                for section in required
                if section.lower() not in all_intents.lower()
            ]
            if missing:
                plan['status'] = 'REFUSED'
                plan['refusal_reason'] = (
                    f"manuscript_consistency: {target_profile} plan missing required "
                    f"section mentions: {', '.join(missing)}"
                )

        return plan

    @staticmethod
    def _extract_longtask_result(result_text: str) -> dict[str, Any] | None:
        return parse_prefixed_result_line(result_text or '')

    @classmethod
    def _detect_manuscript_validation_failure(
        cls, intent: str, result_text: str
    ) -> tuple[bool, str]:
        """Detect manuscript-scribe failures encoded in textual tool output."""
        lowered_intent = (intent or '').lower()
        if 'manuscript-scribe' not in lowered_intent:
            return False, ''

        structured = cls._extract_longtask_result(result_text)
        if structured:
            status = str(structured.get('status', '')).strip().lower()
            message = str(structured.get('message', '')).strip()
            if status in {'retryable_error', 'fatal_error'}:
                return True, message or f"manuscript status={status}"
            if status == 'completed':
                return False, ''

        lowered_text = (result_text or '').lower()
        if (
            'assemble_manuscript' in lowered_intent
            or 'validate_content' in lowered_intent
        ):
            for marker in cls._MANUSCRIPT_FAIL_MARKERS:
                if marker in lowered_text:
                    return True, f"manuscript validation failed ({marker})"
        return False, ''

    def _is_quality_critical_step(
        self,
        intent: str,
        *,
        state: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> bool:
        """Ask LLM whether this step needs document-quality gating."""
        if not (intent or '').strip():
            return False
        prompt = f"""STEP INTENT:
{intent}

Question: Should this step be judged by long-form document quality criteria (survey/manuscript/report writing quality), rather than only by normal execution success?

Return exactly one JSON object:
{{
  "apply_quality_gate": false,
  "reason": ""
}}

Rules:
- Return true only when the step's primary deliverable is clearly a written document whose quality must be reviewed.
- Return false for data collection, extraction, normalization, scripts, tables, calculations, and generic execution steps.
- Be conservative. If the intent is ambiguous, return false.
"""
        dialog = Dialog(
            messages=[
                SystemMessage(
                    content=f"You are a strict step-quality classifier. Output only JSON.\n\n{LANGUAGE_RULE}"
                ),
                UserMessage(content=prompt),
            ],
            tools=[],
        )
        try:
            if state is not None and task_id is not None:
                if not self._consume_turns(state, task_id, 1):
                    self._fail_max_turns_exceeded(task_id, state)
                    return False
            reply = self.agent.llm.query(dialog)
            raw = _extract_json_from_content(reply.content or '')
            if not raw:
                return False
            result = json.loads(raw)
            return bool(result.get('apply_quality_gate', False))
        except Exception as e:
            self.logger.debug('Quality gate classification skipped: %s', e)
            return False

    @staticmethod
    def _extract_markdown_paths_from_text(text: str, workspace_dir: Path) -> list[Path]:
        candidates: list[Path] = []
        if not text:
            return candidates
        for raw in re.findall(r'([A-Za-z0-9_./\\:\-]+\.md)', text):
            cleaned = raw.strip().strip("`'\"")
            if not cleaned:
                continue
            path = Path(cleaned)
            if not path.is_absolute():
                path = workspace_dir / path
            if path.exists() and path.is_file():
                candidates.append(path)
        return candidates

    @staticmethod
    def _count_substantial_lines(content: str, min_length: int = 60) -> int:
        count = 0
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                continue
            if stripped.startswith('<!--'):
                continue
            if len(stripped) < min_length:
                continue
            count += 1
        return count

    @staticmethod
    def _reference_metrics(content: str) -> tuple[int, int]:
        lower = content.lower()
        match = re.search(
            r'^\s{0,3}#{1,6}\s+references?\s*$',
            lower,
            flags=re.MULTILINE,
        )
        ref_text = content[match.end() :] if match else content
        ref_entries = re.findall(r'(?m)^\s*\[(\d+)\]\s+', ref_text)
        if ref_entries:
            ref_count = len(set(ref_entries))
        else:
            cite_entries = re.findall(r'\[(\d+)\]\(https?://[^\)]+\)', ref_text)
            ref_count = len(set(cite_entries))
        doi_urls = set(re.findall(r'https?://(?:dx\.)?doi\.org/([^\s\)]+)', lower))
        bare_dois = set(re.findall(r'\b10\.\d{4,9}/[-._;()/:a-z0-9]+\b', lower))
        unique_doi_count = len({doi.rstrip('.,;:)') for doi in doi_urls | bare_dois})
        return ref_count, unique_doi_count

    def _collect_quality_files(
        self, step_dir: Path, workspace_dir: Path, result_text: str
    ) -> list[Path]:
        files: list[Path] = []
        seen: set[str] = set()

        def add(path: Path) -> None:
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                return
            seen.add(key)
            files.append(path)

        for path in step_dir.glob('*.md'):
            add(path)

        survey_dir = workspace_dir / '_tmp' / 'surveys'
        if survey_dir.exists():
            for path in survey_dir.glob('*.md'):
                add(path)
        for path in workspace_dir.glob('*_review_*.md'):
            add(path)
        for path in workspace_dir.glob('*_survey_*.md'):
            add(path)
        for path in self._extract_markdown_paths_from_text(result_text, workspace_dir):
            add(path)
        return files

    def _detect_survey_quality_failure(
        self,
        *,
        intent: str,
        quality_files: list[Path],
        evidence_delta: int,
        state: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> tuple[bool, str]:
        if not self._is_quality_critical_step(intent, state=state, task_id=task_id):
            return False, ''
        if not quality_files:
            return (
                True,
                'No markdown artifact found for quality-critical survey/literature step.',
            )

        min_line_len = self._quality_gate_cfg.get('survey_min_line_length', 60)
        best: tuple[int, int, int, Path] | None = None
        best_content = ''
        for path in quality_files:
            try:
                content = path.read_text(encoding='utf-8')
            except Exception:
                continue
            refs, dois = self._reference_metrics(content)
            substantial = self._count_substantial_lines(
                content,
                min_length=min_line_len,
            )
            score = (refs, dois, substantial, path)
            if best is None or score[:3] > best[:3]:
                best = score
                best_content = content

        if best is None:
            return True, 'Survey quality check found no readable markdown artifacts.'

        refs, dois, substantial, best_path = best
        if refs < self._quality_gate_cfg['survey_min_references']:
            return True, (
                f"Insufficient references in {best_path.name}: {refs} "
                f"(min {self._quality_gate_cfg['survey_min_references']})."
            )
        if dois < self._quality_gate_cfg['survey_min_unique_dois']:
            return True, (
                f"Insufficient unique DOIs in {best_path.name}: {dois} "
                f"(min {self._quality_gate_cfg['survey_min_unique_dois']})."
            )
        if substantial < self._quality_gate_cfg['survey_min_substantial_lines']:
            return True, (
                f"Insufficient substantive content in {best_path.name}: {substantial} lines "
                f"(min {self._quality_gate_cfg['survey_min_substantial_lines']})."
            )
        if evidence_delta < self._quality_gate_cfg['survey_min_evidence_delta']:
            return True, (
                f"No evidence growth in literature index: delta={evidence_delta} "
                f"(min {self._quality_gate_cfg['survey_min_evidence_delta']})."
            )
        if best_content and re.search(r'\btbd\b', best_content.lower()):
            return (
                True,
                f"Survey artifact still contains TBD placeholder: {best_path.name}",
            )
        return False, ''
