"""DirectSolver: on-the-fly mode (即时模式).

Analyzes task -> routes to SkillEvolutionExp / WorkerExp by capability.
Mode is decoupled from Exp: any task can trigger any enabled capability.
Routing is deterministic: SKILL_EVOLUTION | STANDARD_EXECUTION.

Note: Resilient calculation logic (submit / monitor / diagnose / retry) is now
handled by the **monitor_job** built-in tool. The agent invokes it within
STANDARD_EXECUTION; a separate RESILIENT_CALC route is no longer needed.
"""

import json
import logging
from typing import Any, Optional

from evomaster.core.exp import BaseExp
from evomaster.utils.types import Dialog, SystemMessage, TaskInstance, UserMessage

from ..async_tool_registry import AsyncToolRegistry
from ..exp import SkillEvolutionExp, WorkerExp

logger = logging.getLogger(__name__)


def _get_mat_master_config(config) -> dict:
    """Get mat_master section as dict."""
    try:
        if hasattr(config, 'model_dump'):
            d = config.model_dump()
        else:
            d = dict(config) if config else {}
        return d.get('mat_master') or {}
    except Exception as exc:
        logger.warning('Failed to parse mat_master config: %s', exc)
        return {}


def _get_available_tool_names(agent) -> list[str]:
    """Get list of tool names for router context (respects async policy filtering).

    Uses ``agent._get_tool_specs()`` so the router sees the same filtered tool
    surface as the agent (submit-only for async tools, lifecycle tools hidden).
    """
    try:
        if agent is None:
            return []
        # Prefer the agent-level filtered specs (applies AsyncExecutionPolicy).
        if hasattr(agent, '_get_tool_specs'):
            specs = agent._get_tool_specs()
            return [
                s.function.name for s in specs if hasattr(s, 'function') and s.function
            ]
        if not hasattr(agent, 'tools') or agent.tools is None:
            return []
        tools = agent.tools
        if hasattr(tools, 'get_tool_names'):
            return list(tools.get_tool_names())
        if hasattr(tools, 'get_tool_specs'):
            specs = tools.get_tool_specs()
            return [
                s.function.name for s in specs if hasattr(s, 'function') and s.function
            ]
        return []
    except Exception:
        return []


def _extract_first_json_object(text: str) -> str | None:
    """Extract first {...} with balanced braces from text. Returns None if not found."""
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_route(response: str) -> str:
    """Parse router LLM output: JSON with 'decision' or legacy tags [EVO]/[DEFAULT]."""
    text = (response or '').strip()

    # 1) Try JSON: {"decision": "SKILL_EVOLUTION" | "STANDARD_EXECUTION", "rationale": "..."}
    for raw in (text, _extract_first_json_object(text)):
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            decision = (obj.get('decision') or '').strip().upper()
            if 'SKILL_EVOLUTION' in decision:
                return 'evo'
            if 'STANDARD_EXECUTION' in decision:
                return 'default'
            # Legacy: treat RESILIENT_CALC as STANDARD_EXECUTION (monitor_job handles the lifecycle)
            if 'RESILIENT_CALC' in decision:
                return 'default'
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
            continue

    # 2) Legacy tag fallback
    upper = text.upper()
    if '[EVO]' in upper or 'SKILL_EVOLUTION' in upper:
        return 'evo'
    return 'default'


def _build_router_system(registry: AsyncToolRegistry, skills_str: str = '') -> str:
    """Build a project-independent router prompt."""
    return """Classify the task using only the runtime capabilities listed below.

Choose STANDARD_EXECUTION when the task can be completed with the registered capabilities and loaded run contracts.

Choose SKILL_EVOLUTION only when a necessary programmatic capability is absent.

Do not assume that an unavailable tool, skill, or service can be called.

Return the decision and a one-sentence rationale as JSON."""

class DirectSolver(BaseExp):
    """即时响应模式：分析任务 -> 动态路由到 SkillEvolutionExp / WorkerExp。

    与 Mode 解耦：不绑定具体 Exp，根据能力和任务描述路由。
    路由为确定性二分类：SKILL_EVOLUTION（技能进化）| STANDARD_EXECUTION（本地/同步/远程计算）。
    远程计算的弹性监控由内置工具 monitor_job 在 STANDARD_EXECUTION 内部处理。
    """

    def __init__(self, agent, config):
        super().__init__(agent, config)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._mat = _get_mat_master_config(config)
        caps = self._mat.get('capabilities') or {}
        self._evo_enabled = caps.get('skill_evolution', {}).get('enabled', True)
        # Build registry from config for dynamic router prompt
        try:
            cfg_dict = (
                config.model_dump()
                if hasattr(config, 'model_dump')
                else (dict(config) if config else {})
            )
        except Exception:
            cfg_dict = {}
        self._registry = AsyncToolRegistry(cfg_dict)
        general = ((cfg_dict.get('agents') or {}).get('general') or {})
        self._execution_mode = str(
            general.get('execution_mode') or 'auto'
        ).strip().lower()

    def _route_task(self, task_description: str) -> str:
        """One-shot LLM route: SKILL_EVOLUTION | STANDARD_EXECUTION -> evo | default."""
        available_tools = _get_available_tool_names(self.agent)
        tools_preview = (
            available_tools[:80] if len(available_tools) > 80 else available_tools
        )
        if len(available_tools) > 80:
            tools_preview.append('...')
        available_tools_str = ', '.join(tools_preview) if tools_preview else '(none)'

        # Extract skills from the agent's skill_registry so the router knows what
        # is callable via use_skill and does NOT require SKILL_EVOLUTION.
        skills_str = ''
        registry = getattr(self.agent, 'skill_registry', None)
        if registry is not None:
            try:
                names = [s.meta_info.name for s in registry.get_all_skills()]
                if names:
                    skills_str = ', '.join(names[:40])
            except Exception:
                pass

        user_content = f'''INPUT DATA:
Task: "{task_description[:800]}"
Available Tools: {available_tools_str}
Available Skills (callable via use_skill): {skills_str if skills_str else "(none)"}

Output the JSON object only (decision + rationale).'''

        router_system = _build_router_system(self._registry, skills_str=skills_str)
        dialog = Dialog(
            messages=[
                SystemMessage(content=router_system),
                UserMessage(content=user_content),
            ],
            tools=[],
        )
        try:
            reply = self.agent.llm.query(dialog)
            content = (reply.content or '').strip()
            route = _parse_route(content)
            self.logger.debug('Router raw: %s -> %s', content[:200], route)
            return route
        except Exception as e:
            self.logger.warning('Router LLM failed, using default: %s', e)
            return 'default'

    def _create_sub_exp(self, sub_type: str) -> BaseExp:
        if sub_type == 'evo':
            return SkillEvolutionExp(self.agent, self.config)
        return WorkerExp(self.agent, self.config)

    def run(
        self,
        task_description: str = '',
        task_id: str = 'direct_task',
        task: Optional[TaskInstance] = None,
        append_result: bool = True,
    ) -> dict[str, Any]:
        """执行任务：按路由选择 SkillEvolutionExp 或 WorkerExp，透传 append_result 避免对话场景下 results 无限增长。"""
        if task is not None:
            desc = task.description or ''
            tid = task.task_id
        else:
            desc = task_description
            tid = task_id
        route = (
            'default'
            if self._execution_mode == 'direct'
            else self._route_task(desc)
        )
        if route == 'evo' and not self._evo_enabled:
            route = 'default'

        sub_exp = self._create_sub_exp(route)
        if self.run_dir is not None:
            sub_exp.set_run_dir(self.run_dir)

        self.logger.info('[Direct] Route %s: %s', route, (desc[:80] if desc else ''))
        if task is not None:
            result = sub_exp.run(task=task, append_result=append_result)
        else:
            result = sub_exp.run(desc, task_id=tid, append_result=append_result)
        return result
