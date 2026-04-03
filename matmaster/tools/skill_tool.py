"""MatMaster-native SkillTool — expands SKILL.md into the model context."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

if TYPE_CHECKING:
    from matmaster.skills.registry import Skill, SkillRegistry

logger = logging.getLogger(__name__)


class SkillTool:
    """Tool that loads a skill by name and returns its full documentation body."""

    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = ()
    capabilities: ClassVar[frozenset[str]] = frozenset({"skill.dispatch"})
    effect_level: ClassVar[str] = "local_mutation"
    fast_path_eligible: ClassVar[bool] = False
    max_result_chars: ClassVar[int] = 0
    plane: ClassVar[ToolPlane] = ToolPlane.CONTROL_PLANE
    state_mode: ClassVar[str] = "stateless"
    stop_mode: ClassVar[str] = "cancellable"
    exposed_to_model: ClassVar[bool] = True

    def __init__(
        self,
        skill_registry: SkillRegistry,
        on_skill_hit: Callable[[str], None] | None = None,
    ) -> None:
        self._registry = skill_registry
        self._on_skill_hit = on_skill_hit

    @property
    def name(self) -> str:
        return "use_skill"

    @property
    def description(self) -> str:
        return (
            "Activate a skill by name. Returns the skill's full documentation and "
            "workflow instructions from SKILL.md so you can follow it end-to-end."
        )

    def describe(self, ctx: ToolDescriptionContext | None = None) -> str:
        return self.description

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str | None:
        return None

    @property
    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill name in kebab-case (e.g. deep-survey)",
                },
            },
            "required": ["skill_name"],
        }

    async def execute(self, arguments: dict[str, Any]) -> str:
        return await asyncio.to_thread(self._execute_sync, arguments)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str:
        return await self.execute(arguments)

    def _execute_sync(self, arguments: dict[str, Any]) -> str:
        try:
            skill_name = arguments["skill_name"]
            skill = self._registry.get_skill(skill_name)
            if skill is None:
                return f"Error: Skill '{skill_name}' not found"

            body = skill.get_full_info()
            skill_dir = str(skill.skill_path.resolve())
            body = body.replace("${SKILL_DIR}", skill_dir)

            self._maybe_hit_mcp(skill)

            for dep_name in skill.meta_info.depends_on:
                dep_skill = self._registry.get_skill(dep_name)
                if dep_skill is not None:
                    self._maybe_hit_mcp(dep_skill)

            return f"Base directory for this skill: {skill_dir}\n\n{body}"
        except Exception as e:
            logger.error("Skill tool execution failed: %s", e, exc_info=True)
            return f"Error: {e}"

    def _maybe_hit_mcp(self, skill: Skill) -> None:
        mcp_server = skill.meta_info.mcp_server
        if mcp_server and self._on_skill_hit:
            self._on_skill_hit(mcp_server)
