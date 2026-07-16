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
        registry_provider: Callable[[], SkillRegistry | None] | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._registry = skill_registry
        self._on_skill_hit = on_skill_hit
        self._registry_provider = registry_provider

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
            skill_name = (arguments.get("skill") or "").lstrip("/")

            registry = self._current_registry()
            if registry is None:
                return "Error: skill registry not available"

            skill = registry.get_skill(skill_name)
            if skill is None:
                return f"Error: Skill '{skill_name}' not found"

            body = skill.get_full_info()
            skill_dir = self._render_dir(skill, skill.skill_path)
            body = body.replace("${SKILL_DIR}", skill_dir)
            if skill.plugin_dir is not None:
                body = body.replace(
                    "${PLUGIN_DIR}", self._render_dir(skill, skill.plugin_dir)
                )

            self._maybe_hit_mcp(skill)
            for dep_name in skill.meta_info.depends_on:
                dep_skill = registry.get_skill(dep_name)
                if dep_skill is not None:
                    self._maybe_hit_mcp(dep_skill)

            return f"Base directory for this skill: {skill_dir}\n\n{body}"
        except Exception as e:
            self.logger.error("Skill tool failed: %s", e, exc_info=True)
            return f"Error: {e}"

    def _current_registry(self) -> SkillRegistry | None:
        """Re-resolve the registry so remote roots discovered after a lazy
        Node acquisition take effect for later activations in the same run."""
        if self._registry_provider is not None:
            try:
                refreshed = self._registry_provider()
            except Exception:
                self.logger.warning(
                    "Skill registry refresh failed; using last known registry",
                    exc_info=True,
                )
                refreshed = None
            if refreshed is not None:
                self._registry = refreshed
        return self._registry

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

    def _render_dir(self, skill: Skill, path: Path | PurePosixPath) -> str:
        if getattr(skill, "is_remote", False):
            return str(path)
        return self._render_local_dir(path)

    def _render_local_dir(self, path: Path) -> str:
        local_abs = path if path.is_absolute() else path.resolve()

        session = self._session
        planned = self._render_planned_dir(session, local_abs)
        if planned is not None:
            return planned

        remote_project_root = getattr(session, "remote_project_root", None)
        if remote_project_root:
            try:
                rel = local_abs.relative_to(_PROJECT_ROOT)
                return str(PurePosixPath(remote_project_root) / rel.as_posix())
            except ValueError:
                pass

        return str(local_abs)

    @staticmethod
    def _render_planned_dir(session: Any, local_abs: Path) -> str | None:
        """Map a worker-local skill dir onto the Node root it materializes to.

        Cold DeferredBohriumSession runs resolve skills from worker-local
        roots; the planned map keeps rendered paths valid on the Node the run
        will lazily acquire.
        """
        planned_map = getattr(session, "planned_skill_root_map", None)
        if not isinstance(planned_map, (list, tuple)):
            return None
        for pair in planned_map:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                continue
            local_root, remote_root = pair
            if not (isinstance(local_root, str) and isinstance(remote_root, str)):
                continue
            try:
                rel = local_abs.relative_to(local_root)
            except ValueError:
                continue
            if rel == Path("."):
                return str(PurePosixPath(remote_root))
            return str(PurePosixPath(remote_root) / rel.as_posix())
        return None

    def _execute(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError("SkillTool uses async execute() directly")
