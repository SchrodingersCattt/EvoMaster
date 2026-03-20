"""Runtime context prompt and CRP plan-safety validation."""

import json
import re
from typing import Any

from ..direct_solver import _get_available_tool_names
from ..plan_utils import _get_mat_master_config


class ResearchPlannerPlanningContextMixin:
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
