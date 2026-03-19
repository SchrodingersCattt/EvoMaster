"""Composed execution mixin for ``ResearchPlanner``."""

from ._precheck import ResearchPlannerPrecheckMixin
from ._replan import ResearchPlannerReplanMixin
from ._step import ResearchPlannerStepExecutionMixin


class ResearchPlannerExecutionMixin(
    ResearchPlannerStepExecutionMixin,
    ResearchPlannerReplanMixin,
    ResearchPlannerPrecheckMixin,
):
    """Composed mixin: step execution, replan/context, and pre-check."""
