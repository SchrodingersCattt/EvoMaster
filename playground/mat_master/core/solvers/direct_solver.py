"""DirectSolver: on-the-fly mode (即时模式).

Analyzes task -> routes to ResilientCalcExp / SkillEvolutionExp / WorkerExp by capability.
Mode is decoupled from Exp: any task can trigger any enabled capability.
"""

import logging
from typing import Any

from evomaster.core.exp import BaseExp
from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from ..exp import ResilientCalcExp, SkillEvolutionExp, WorkerExp


def _get_mat_master_config(config) -> dict:
    """Get mat_master section as dict."""
    try:
        if hasattr(config, "model_dump"):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get("mat_master") or {}
    except Exception:
        return {}


def _parse_route(response: str) -> str:
    """Parse router LLM output: [CALC] | [EVO] | [DEFAULT]."""
    text = (response or "").strip().upper()
    if "[CALC]" in text:
        return "calc"
    if "[EVO]" in text:
        return "evo"
    return "default"


class DirectSolver(BaseExp):
    """即时响应模式：分析任务 -> 动态路由到 ResilientCalcExp / SkillEvolutionExp / WorkerExp。

    与 Mode 解耦：不绑定具体 Exp，根据能力和任务描述路由。
    """

    def __init__(self, agent, config):
        super().__init__(agent, config)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._mat = _get_mat_master_config(config)
        caps = self._mat.get("capabilities") or {}
        self._calc_enabled = caps.get("resilient_calc", {}).get("enabled", True)
        self._evo_enabled = caps.get("skill_evolution", {}).get("enabled", True)

    def _route_task(self, task_description: str) -> str:
        """One-shot LLM route: calc | evo | default."""
        options = []
        if self._calc_enabled:
            options.append("[CALC] for complex calculation (e.g. VASP/LAMMPS)")
        if self._evo_enabled:
            options.append("[EVO] when a new tool/skill is needed")
        options.append("[DEFAULT] for normal tool-call tasks")
        options_text = "\n".join(f"- {o}" for o in options)

        system = (
            "You are a task router. Reply with exactly one tag and nothing else: "
            "[CALC], [EVO], or [DEFAULT]."
        )
        user = (
            f"Task: {task_description[:500]}\n\n"
            f"Which applies?\n{options_text}\n\n"
            "Reply with only one of: [CALC] or [EVO] or [DEFAULT]."
        )
        dialog = Dialog(
            messages=[
                SystemMessage(content=system),
                UserMessage(content=user),
            ],
            tools=[],
        )
        try:
            reply = self.agent.llm.query(dialog)
            content = (reply.content or "").strip()
            return _parse_route(content)
        except Exception as e:
            self.logger.warning("Router LLM failed, using default: %s", e)
            return "default"

    def _create_sub_exp(self, sub_type: str) -> BaseExp:
        if sub_type == "calc":
            return ResilientCalcExp(self.agent, self.config)
        if sub_type == "evo":
            return SkillEvolutionExp(self.agent, self.config)
        return WorkerExp(self.agent, self.config)

    def run(self, task_description: str, task_id: str = "direct_task") -> dict[str, Any]:
        route = self._route_task(task_description)
        if route == "calc" and not self._calc_enabled:
            route = "default"
        if route == "evo" and not self._evo_enabled:
            route = "default"

        sub_exp = self._create_sub_exp(route)
        if self.run_dir is not None:
            sub_exp.set_run_dir(self.run_dir)

        self.logger.info("[Direct] Route %s: %s", route, task_description[:80])
        result = sub_exp.run(task_description, task_id=task_id)
        return result
