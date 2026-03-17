"""ResearchPlanner: pre-check -> plan -> preflight -> execute -> replan."""

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from evomaster.core.exp import BaseExp

from ...prompts.build_prompt import LANGUAGE_RULE
from ..async_tool_registry import AsyncToolRegistry
from ._research_planner_execution import ResearchPlannerExecutionMixin
from ._research_planner_phases import ResearchPlannerPhaseMixin
from ._research_planner_planning import ResearchPlannerPlanningMixin
from ._research_planner_runtime import ResearchPlannerRuntimeMixin
from .direct_solver import DirectSolver
from .plan_utils import (
    _get_async_registry,
    _get_mat_master_config,
    _load_pre_check_system_prompt,
)


class ResearchPlanner(
    ResearchPlannerRuntimeMixin,
    ResearchPlannerPlanningMixin,
    ResearchPlannerExecutionMixin,
    ResearchPlannerPhaseMixin,
    BaseExp,
):
    """State-machine planner that executes validated research plans via ``DirectSolver``."""

    def __init__(
        self,
        agent,
        config,
        input_fn=None,
        output_callback=None,
        config_dir: str | Path | None = None,
    ):
        super().__init__(agent, config)
        self.logger = logging.getLogger('MatMaster.Planner')
        mat = _get_mat_master_config(config)
        planner_cfg = mat.get('planner') or {}
        self._config_dir = Path(config_dir).resolve() if config_dir else None
        self._planner_prompt_file = str(
            planner_cfg.get('system_prompt_file', 'prompts/planner_system_prompt.txt')
        )
        self._pre_check_system = _load_pre_check_system_prompt(
            self._config_dir
        ).replace('{language_rule}', LANGUAGE_RULE)
        self.state_file = planner_cfg.get('state_file', 'research_state.json')
        self.max_steps = planner_cfg.get('max_steps', 20)
        self.human_check = planner_cfg.get('human_check_step', True)
        self.max_replans = planner_cfg.get('max_replans', 5)
        self.window_size = planner_cfg.get('window_size', 1)
        self.auto_replan = planner_cfg.get('auto_replan', True)
        self.replan_on_failure = planner_cfg.get('replan_on_failure', True)
        self.replan_on_new_skill = planner_cfg.get('replan_on_new_skill', True)

        cfg_max_turns = planner_cfg.get('max_turns')
        if cfg_max_turns is None:
            cfg_max_turns = getattr(getattr(agent, 'config', None), 'max_turns', None)
        try:
            self._turn_budget_init: int = max(1, int(cfg_max_turns or 100))
        except Exception:
            self._turn_budget_init = 100
        self._turn_budget_remaining: int = int(self._turn_budget_init)
        self._turn_budget_lock = threading.Lock()

        exec_cfg = mat.get('execution') or {}
        self._planner_max_workers: int = max(
            1, exec_cfg.get('planner_max_workers', self.window_size)
        )
        self._rate_limit: float | None = exec_cfg.get('rate_limit')
        self._input_fn = input_fn
        self._output_callback: Callable[[str, str, Any], None] | None = output_callback
        self._solver: DirectSolver | None = None
        self._registry: AsyncToolRegistry = _get_async_registry(config)
        quality_cfg = planner_cfg.get('quality_gates') or {}
        self._quality_gate_cfg: dict[str, int] = {
            'survey_min_references': max(
                1, int(quality_cfg.get('survey_min_references', 5))
            ),
            'survey_min_unique_dois': max(
                1, int(quality_cfg.get('survey_min_unique_dois', 3))
            ),
            'survey_min_substantial_lines': max(
                1, int(quality_cfg.get('survey_min_substantial_lines', 8))
            ),
            'survey_min_evidence_delta': max(
                1, int(quality_cfg.get('survey_min_evidence_delta', 1))
            ),
            'survey_min_line_length': max(
                1, int(quality_cfg.get('survey_min_line_length', 60))
            ),
        }
