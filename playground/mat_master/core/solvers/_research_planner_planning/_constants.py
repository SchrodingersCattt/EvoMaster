"""Shared class-level constants for ``ResearchPlannerPlanningMixin``."""

from ...constants import MANUSCRIPT_FAIL_MARKERS


class _ResearchPlannerPlanningConstants:
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
