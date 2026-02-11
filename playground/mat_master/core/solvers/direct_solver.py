"""DirectSolver: on-the-fly mode (即时模式).

Analyzes task -> routes to SkillEvolutionExp / WorkerExp by capability.
Mode is decoupled from Exp: any task can trigger any enabled capability.
Routing is deterministic: SKILL_EVOLUTION | STANDARD_EXECUTION.

Note: Resilient calculation logic (submit / monitor / diagnose / retry) has been
moved to the **job-manager** skill. The agent invokes it via use_skill within
STANDARD_EXECUTION; a separate RESILIENT_CALC route is no longer needed.
"""

import json
import logging
from typing import Any

from evomaster.core.exp import BaseExp
from evomaster.utils.types import Dialog, SystemMessage, UserMessage

from ..async_tool_registry import AsyncToolRegistry
from ..exp import SkillEvolutionExp, WorkerExp


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


def _get_available_tool_names(agent) -> list[str]:
    """Get list of tool names for router context. Empty if not available."""
    try:
        if agent is None or not hasattr(agent, "tools") or agent.tools is None:
            return []
        tools = agent.tools
        if hasattr(tools, "get_tool_names"):
            return list(tools.get_tool_names())
        if hasattr(tools, "get_tool_specs"):
            specs = tools.get_tool_specs()
            return [s.function.name for s in specs if hasattr(s, "function") and s.function]
        return []
    except Exception:
        return []


def _extract_first_json_object(text: str) -> str | None:
    """Extract first {...} with balanced braces from text. Returns None if not found."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_route(response: str) -> str:
    """Parse router LLM output: JSON with 'decision' or legacy tags [EVO]/[DEFAULT]."""
    text = (response or "").strip()

    # 1) Try JSON: {"decision": "SKILL_EVOLUTION" | "STANDARD_EXECUTION", "rationale": "..."}
    for raw in (text, _extract_first_json_object(text)):
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            decision = (obj.get("decision") or "").strip().upper()
            if "SKILL_EVOLUTION" in decision:
                return "evo"
            if "STANDARD_EXECUTION" in decision:
                return "default"
            # Legacy: treat RESILIENT_CALC as STANDARD_EXECUTION (handled by job-manager skill)
            if "RESILIENT_CALC" in decision:
                return "default"
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
            continue

    # 2) Legacy tag fallback
    upper = text.upper()
    if "[EVO]" in upper or "SKILL_EVOLUTION" in upper:
        return "evo"
    return "default"


def _build_router_system(registry: AsyncToolRegistry) -> str:
    """Build ROUTER_SYSTEM prompt with software names from registry (no hardcoding)."""
    sw = registry.software_list_str()
    sm = registry.server_mapping_str()
    block = registry.crp_block_str()
    return f"""You are a deterministic task routing module for MatMaster. Your sole function is to classify the user's task into one of two execution modes based on strict system constraints.

SYSTEM CONSTRAINTS:
1. Local Environment: The local sandbox supports Python scripting, data manipulation, and lightweight simulations (e.g., ASE, Pymatgen). It does NOT provide {block}, {sw} run services locally.
2. Remote Delegation: Heavy calculations ({sw}) are submitted via MCP tools ({sm}) and monitored via the job-manager skill. This is handled within STANDARD_EXECUTION; no separate routing is needed.
3. Tool Availability: Use the provided 'Available Tools' list to decide if a programmatic capability is missing (SKILL_EVOLUTION) or can be fulfilled by existing tools and skills (STANDARD_EXECUTION). Always check the full tool list before concluding a tool is missing.

ROUTING CATEGORIES:
A. [SKILL_EVOLUTION]: Choose this IF AND ONLY IF the task requires a programmatic tool or specific Python capability that is strictly absent from the 'Available Tools' list, necessitating the generation of a new script. Do NOT choose this if a matching tool already exists (e.g., mat_binary_calc_run_cp2k for CP2K tasks, job-manager for monitoring calculation jobs).
B. [STANDARD_EXECUTION]: Choose this for all other tasks. This includes literature searches, structure generation, data extraction, local Python scripting, remote calculation submission and monitoring (via MCP tools + job-manager skill), and utilizing existing MCP tools.

OUTPUT FORMAT:
You must output a strictly valid JSON object with exactly two keys. Do not include markdown formatting or explanatory text outside the JSON.
{{
    "decision": "<SKILL_EVOLUTION | STANDARD_EXECUTION>",
    "rationale": "<A precise, one-sentence logical deduction based on the constraints.>"
}}"""


class DirectSolver(BaseExp):
    """即时响应模式：分析任务 -> 动态路由到 SkillEvolutionExp / WorkerExp。

    与 Mode 解耦：不绑定具体 Exp，根据能力和任务描述路由。
    路由为确定性二分类：SKILL_EVOLUTION（技能进化）| STANDARD_EXECUTION（本地/同步/远程计算）。
    远程计算的弹性监控由 job-manager skill 在 STANDARD_EXECUTION 内部处理。
    """

    def __init__(self, agent, config):
        super().__init__(agent, config)
        self.logger = logging.getLogger(self.__class__.__name__)
        self._mat = _get_mat_master_config(config)
        caps = self._mat.get("capabilities") or {}
        self._evo_enabled = caps.get("skill_evolution", {}).get("enabled", True)
        # Build registry from config for dynamic router prompt
        try:
            cfg_dict = config.model_dump() if hasattr(config, "model_dump") else (dict(config) if config else {})
        except Exception:
            cfg_dict = {}
        self._registry = AsyncToolRegistry(cfg_dict)

    def _route_task(self, task_description: str) -> str:
        """One-shot LLM route: SKILL_EVOLUTION | STANDARD_EXECUTION -> evo | default."""
        available_tools = _get_available_tool_names(self.agent)
        tools_preview = available_tools[:80] if len(available_tools) > 80 else available_tools
        if len(available_tools) > 80:
            tools_preview.append("...")
        available_tools_str = ", ".join(tools_preview) if tools_preview else "(none)"

        user_content = f'''INPUT DATA:
Task: "{task_description[:800]}"
Available Tools: {available_tools_str}

Output the JSON object only (decision + rationale).'''

        router_system = _build_router_system(self._registry)
        dialog = Dialog(
            messages=[
                SystemMessage(content=router_system),
                UserMessage(content=user_content),
            ],
            tools=[],
        )
        try:
            reply = self.agent.llm.query(dialog)
            content = (reply.content or "").strip()
            route = _parse_route(content)
            self.logger.debug("Router raw: %s -> %s", content[:200], route)
            return route
        except Exception as e:
            self.logger.warning("Router LLM failed, using default: %s", e)
            return "default"

    def _create_sub_exp(self, sub_type: str) -> BaseExp:
        if sub_type == "evo":
            return SkillEvolutionExp(self.agent, self.config)
        return WorkerExp(self.agent, self.config)

    def run(self, task_description: str, task_id: str = "direct_task") -> dict[str, Any]:
        route = self._route_task(task_description)
        if route == "evo" and not self._evo_enabled:
            route = "default"

        sub_exp = self._create_sub_exp(route)
        if self.run_dir is not None:
            sub_exp.set_run_dir(self.run_dir)

        self.logger.info("[Direct] Route %s: %s", route, task_description[:80])
        result = sub_exp.run(task_description, task_id=task_id)
        return result
