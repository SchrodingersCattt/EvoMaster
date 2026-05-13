"""SkillTool -- activate a skill by name and return its full documentation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar

from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

if TYPE_CHECKING:
    from matmaster.skills.registry import Skill, SkillRegistry


_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class SkillTool(BuiltinTool):
    """Activate a skill by name and return its full documentation."""

    name: ClassVar[str] = "Skill"
    description: ClassVar[str] = (
        "Activate a skill by name and return its full documentation. "
        "Users' slash commands like /commit also invoke skills via this tool."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": 'The skill name. E.g. "commit" or "review-pr"',
            },
        },
        "required": ["skill"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = ()
    capabilities: ClassVar[frozenset[str]] = frozenset({"skill.dispatch"})
    effect_level: ClassVar[str] = "local_mutation"
    fast_path_eligible: ClassVar[bool] = False
    plane: ClassVar[ToolPlane] = ToolPlane.CONTROL_PLANE

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        skill_registry: SkillRegistry | None = None,
        on_skill_hit: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._registry = skill_registry
        self._on_skill_hit = on_skill_hit

    def prompt(self, ctx=None) -> str:
        return (
            "Execute a skill within the main conversation\n\n"
            "When users ask you to perform tasks, check if any of the available "
            "skills match. Skills provide specialized capabilities and domain knowledge.\n\n"
            'When users reference a "slash command" or "/<something>" '
            '(e.g. "/commit", "/review-pr"), they are referring to a skill. '
            "Use this tool to invoke it.\n\n"
            "How to invoke:\n"
            '- Use this tool with the skill name\n'
            '- Examples:\n'
            '  - `skill: "pdf"` - invoke the pdf skill\n'
            '  - `skill: "review-pr"` - invoke the review-pr skill\n\n'
            "Important:\n"
            "Skill scripts live in ${SKILL_DIR}/scripts; reference docs live in ${SKILL_DIR}/reference.\n\n"
            "- Available skills are listed in system-reminder messages in the conversation\n"
            "- When a skill matches the user's request, invoke it before generating "
            "any other response\n"
            "- Never mention a skill without actually calling this tool\n"
            "- Do not invoke a skill that is already running"
        )

    async def execute(self, arguments: dict[str, Any]) -> str:
        try:
            skill_name = (
                arguments.get("skill") or arguments.get("skill_name") or ""
            ).lstrip("/")

            if self._registry is None:
                return "Error: skill registry not available"

            skill = self._registry.get_skill(skill_name)
            if skill is None:
                return f"Error: Skill '{skill_name}' not found"

            body = skill.get_full_info()
            skill_dir = self._render_skill_dir(skill)
            body = body.replace("${SKILL_DIR}", skill_dir)

            self._maybe_hit_mcp(skill)
            for dep_name in skill.meta_info.depends_on:
                dep_skill = self._registry.get_skill(dep_name)
                if dep_skill is not None:
                    self._maybe_hit_mcp(dep_skill)

            return f"Base directory for this skill: {skill_dir}\n\n{body}"
        except Exception as e:
            self.logger.error("Skill tool failed: %s", e, exc_info=True)
            return f"Error: {e}"

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str:
        return await self.execute(arguments)

    def _maybe_hit_mcp(self, skill: Skill) -> None:
        mcp_server = skill.meta_info.mcp_server
        if mcp_server and self._on_skill_hit:
            self._on_skill_hit(mcp_server)

    def _render_skill_dir(self, skill: Skill) -> str:
        skill_path = skill.skill_path
        if getattr(skill, "is_remote", False):
            return str(skill_path)

        local_abs = skill_path if skill_path.is_absolute() else skill_path.resolve()

        session = self._session
        remote_project_root = getattr(session, "remote_project_root", None)
        if remote_project_root:
            try:
                rel = local_abs.relative_to(_PROJECT_ROOT)
                return str(PurePosixPath(remote_project_root) / rel.as_posix())
            except ValueError:
                pass

        return str(local_abs)

    def _execute(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError("SkillTool uses async execute() directly")


class LegacyUseSkillTool(SkillTool):
    """Backward-compatible alias for older ``use_skill`` callers."""

    name: ClassVar[str] = "use_skill"
    description: ClassVar[str] = "Legacy alias for the Skill tool."
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Legacy skill name field.",
            },
            "action": {
                "type": "string",
                "description": "Legacy action field; `get_info` maps to Skill info lookup.",
            },
            "skill": {
                "type": "string",
                "description": "Accepted for compatibility with the new Skill tool.",
            },
        },
        "required": ["skill_name"],
    }
    exposed_to_model: ClassVar[bool] = False

    async def execute(self, arguments: dict[str, Any]) -> str:
        action = (arguments.get("action") or "").strip()
        if action and action != "get_info":
            return f"Error: unsupported legacy use_skill action '{action}'"
        mapped = dict(arguments)
        if "skill" not in mapped and "skill_name" in mapped:
            mapped["skill"] = mapped["skill_name"]
        return await super().execute(mapped)
