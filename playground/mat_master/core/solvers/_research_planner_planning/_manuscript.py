"""Manuscript profile alignment and manuscript-scribe failure detection."""

import re
from typing import Any

from ....skills._common.longtask_runtime import parse_prefixed_result_line


class ResearchPlannerPlanningManuscriptMixin:
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
