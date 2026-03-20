"""Planning and quality-gate helpers for ``ResearchPlanner``."""

from ._constants import _ResearchPlannerPlanningConstants
from ._context_safety import ResearchPlannerPlanningContextMixin
from ._manuscript import ResearchPlannerPlanningManuscriptMixin
from ._plan_generation import ResearchPlannerPlanningPlanGenMixin
from ._quality import ResearchPlannerPlanningQualityMixin
from ._revision import ResearchPlannerPlanningRevisionMixin


class ResearchPlannerPlanningMixin(
    _ResearchPlannerPlanningConstants,
    ResearchPlannerPlanningContextMixin,
    ResearchPlannerPlanningPlanGenMixin,
    ResearchPlannerPlanningRevisionMixin,
    ResearchPlannerPlanningManuscriptMixin,
    ResearchPlannerPlanningQualityMixin,
):
    """Compose planning, revision, manuscript alignment, and quality-gate behavior."""


__all__ = ['ResearchPlannerPlanningMixin']
