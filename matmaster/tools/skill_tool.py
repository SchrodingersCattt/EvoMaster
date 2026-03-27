"""MatMaster-native SkillTool — implements the Tool Protocol for skill dispatch.

Bridges SkillRegistry to the AgentKernel tool interface, providing three
actions: get_info (retrieve skill documentation), get_reference (fetch a
specific reference file), and run_script (execute a skill script via session).

Decoupled from evomaster: uses matmaster.skills.registry.Skill directly.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import shlex
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from matmaster.skills.registry import Skill, SkillRegistry

logger = logging.getLogger(__name__)


class SkillTool:
    """Tool that dispatches skill actions: get_info, get_reference, run_script.

    Satisfies the matmaster ``Tool`` Protocol (name/description/json_schema/execute).
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        session: Any,
        on_skill_hit: Callable[[str], None] | None = None,
    ) -> None:
        self._registry = skill_registry
        self._session = session
        self._on_skill_hit = on_skill_hit

    # -- Tool Protocol properties -------------------------------------------

    @property
    def name(self) -> str:
        return "use_skill"

    @property
    def description(self) -> str:
        return (
            "Use a skill by name. Three actions: "
            "'get_info' retrieves full skill documentation, "
            "'get_reference' fetches a specific reference file, "
            "'run_script' executes a script from the skill."
        )

    @property
    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill name in kebab-case (e.g. deep-survey)",
                },
                "action": {
                    "type": "string",
                    "enum": ["get_info", "get_reference", "run_script"],
                    "description": (
                        "Action to perform: get_info, get_reference, or run_script"
                    ),
                },
                "reference_name": {
                    "type": "string",
                    "description": "Reference file name (required for get_reference)",
                },
                "script_name": {
                    "type": "string",
                    "description": "Script file name (required for run_script unless skill has exactly one script)",
                },
                "script_args": {
                    "type": "string",
                    "description": "Space-separated script arguments (optional for run_script)",
                },
                "script_timeout": {
                    "type": "integer",
                    "description": "Script execution timeout in seconds (optional for run_script)",
                },
            },
            "required": ["skill_name", "action"],
        }

    # -- Tool Protocol execute ----------------------------------------------

    async def execute(self, arguments: dict[str, Any]) -> str:
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: dict[str, Any]) -> str:
        """Synchronous execution body -- wrapped by execute() via to_thread."""
        try:
            skill_name = arguments["skill_name"]
            action = arguments["action"]

            skill = self._registry.get_skill(skill_name)
            if skill is None:
                return f"Error: Skill '{skill_name}' not found"

            logger.info(
                "Skill hit: skill_name=%s action=%s ref=%s script=%s",
                skill_name,
                action,
                arguments.get("reference_name", "-"),
                arguments.get("script_name", "-"),
            )

            if action == "get_info":
                return self._get_info(skill)
            elif action == "get_reference":
                return self._get_reference(skill, arguments.get("reference_name"))
            elif action == "run_script":
                return self._run_script(
                    skill,
                    arguments.get("script_name"),
                    arguments.get("script_args"),
                    arguments.get("script_timeout"),
                )
            else:
                return f"Error: Unknown action '{action}'"

        except Exception as e:
            logger.error("Skill tool execution failed: %s", e, exc_info=True)
            return f"Error: {e}"

    # -- get_info -----------------------------------------------------------

    def _get_info(self, skill: Skill) -> str:
        full_info = skill.get_full_info()

        # Trigger callback for lazy MCP schema injection
        mcp_server = skill.meta_info.extras.get("mcp_server")
        if mcp_server and self._on_skill_hit:
            self._on_skill_hit(mcp_server)

        # Cascade: if skill declares depends_on, also trigger for each dep
        depends_on = skill.meta_info.extras.get("depends_on")
        if depends_on and self._on_skill_hit:
            dep_names = [d.strip() for d in depends_on.split(",") if d.strip()]
            for dep_name in dep_names:
                dep_skill = self._registry.get_skill(dep_name)
                if dep_skill is not None:
                    dep_mcp = dep_skill.meta_info.extras.get("mcp_server")
                    if dep_mcp:
                        self._on_skill_hit(dep_mcp)

        return f"# Skill: {skill.meta_info.name}\n\n{full_info}"

    # -- get_reference ------------------------------------------------------

    def _get_reference(self, skill: Skill, reference_name: str | None) -> str:
        if not reference_name:
            return "Error: reference_name is required for action='get_reference'"

        try:
            content = skill.get_reference(reference_name)
            observation = f"# Reference: {reference_name}\n\n{content}"

            co_hint = self._get_co_template_hint(skill, reference_name)
            if co_hint:
                observation += co_hint

            return observation

        except FileNotFoundError as e:
            full_info = skill.get_full_info()
            return (
                f"Error: {e}\n\n"
                f"Fallback to skill info:\n\n"
                f"# Skill: {skill.meta_info.name}\n\n{full_info}"
            )

    def _get_co_template_hint(
        self,
        skill: Skill,
        reference_name: str,
    ) -> str | None:
        """Check for co-template hints and return a formatted reminder."""
        hints_path: Path | None = None
        for candidate in (
            skill.skill_path / "references" / "_co_templates.json",
            skill.skill_path / "reference" / "_co_templates.json",
            skill.skill_path / "_co_templates.json",
        ):
            if candidate.exists():
                hints_path = candidate
                break

        if hints_path is None:
            return None

        try:
            hints = _json.loads(hints_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        entry = hints.get(reference_name)
        if not entry:
            return None

        hint_text = entry.get("hint", "")
        related = entry.get("related", [])
        if not hint_text and not related:
            return None

        lines = [
            "",
            "",
            "=" * 72,
            ">>> IMPORTANT — CO-TEMPLATE REMINDER <<<",
        ]
        if hint_text:
            lines.append(hint_text)
        if related:
            lines.append(
                "Related templates you should ALSO fetch (use get_reference for each):"
            )
            for r in related:
                lines.append(f"  - {r}")
        lines.append(
            "Do NOT skip fetching related templates. Do NOT try to manually "
            "construct these sections by querying the manual."
        )
        lines.append("=" * 72)
        return "\n".join(lines)

    # -- run_script ---------------------------------------------------------

    def _run_script(
        self,
        skill: Skill,
        script_name: str | None,
        script_args: str | None,
        script_timeout: int | None,
    ) -> str:
        # Auto-infer script_name for single-script skills
        if not script_name:
            scripts = skill.available_scripts
            if len(scripts) == 1:
                script_name = scripts[0].name
            else:
                available = ", ".join(s.name for s in scripts) if scripts else "(none)"
                return (
                    f"Error: script_name is required for action='run_script'. "
                    f"Skill '{skill.meta_info.name}' has scripts: {available}."
                )

        # Resolve script path
        script_path = skill.get_script_path(script_name)
        if script_path is None:
            available_scripts = ", ".join(s.name for s in skill.available_scripts)
            return (
                f"Error: Script '{script_name}' not found in skill "
                f"'{skill.meta_info.name}'. Available scripts: {available_scripts}"
            )
        script_path = script_path.resolve()

        project_root = self._find_project_root(skill)
        cmd = self._build_command(
            script_path, project_root, script_args, self._session,
        )

        from matmaster.tools.script_env import inject as inject_env

        cmd = inject_env(cmd, self._session)

        result = self._session.exec_bash(cmd, timeout=script_timeout)

        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        exit_code = result.get("exit_code", 0)

        output = f"Script output:\n{stdout}"
        if stderr:
            output += f"\n\nStderr:\n{stderr}"
        if exit_code != 0:
            output += f"\n\nExit code: {exit_code}"

        # Auto-inject per-mode prompt
        prompts_dir = skill.skill_path / "prompts"
        if prompts_dir.exists() and script_args:
            try:
                tokens = shlex.split(script_args.strip())
            except ValueError:
                tokens = script_args.strip().split()
            for token in tokens:
                candidate = prompts_dir / f"{token}.md"
                if candidate.exists():
                    try:
                        prompt_content = candidate.read_text(encoding="utf-8")
                        output += (
                            f"\n\n{'=' * 60}\n"
                            f"MANDATORY WORKFLOW (auto-loaded: prompts/{token}.md):\n"
                            f"{'=' * 60}\n\n"
                            f"{prompt_content}"
                        )
                    except Exception:
                        pass
                    break  # inject only the first match

        return output

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _find_project_root(skill: Skill) -> Path | None:
        """Walk up from skill_path looking for a directory containing matmaster/."""
        try:
            path = skill.skill_path
            for parent in (path,) + tuple(path.parents):
                if (parent / "matmaster").is_dir():
                    return parent.resolve()
        except Exception:
            pass
        return None

    @staticmethod
    def _build_command(
        script_path: Path,
        project_root: Path | None,
        script_args: str | None,
        session: Any,
    ) -> str:
        """Build shell command for script execution.

        Supports local and remote sessions. If session.remote_project_root is
        set and project_root exists, remaps paths to remote POSIX paths.
        """
        remote_root = getattr(session, "remote_project_root", None)

        if remote_root is not None and project_root is not None:
            # Remote execution: remap paths
            try:
                rel = script_path.relative_to(project_root).as_posix()
            except ValueError:
                rel = script_path.name
            remote_script = str(PurePosixPath(remote_root) / rel)

            suffix = script_path.suffix
            if suffix == ".py":
                cmd = (
                    f"PYTHONPATH={shlex.quote(remote_root)} "
                    f"python {shlex.quote(remote_script)}"
                )
            elif suffix == ".sh":
                cmd = f"bash {shlex.quote(remote_script)}"
            elif suffix == ".js":
                cmd = f"node {shlex.quote(remote_script)}"
            else:
                return f"echo 'Error: Unsupported script type: {suffix}'"
        else:
            # Local execution
            suffix = script_path.suffix
            if suffix == ".py":
                py_prefix = ""
                if project_root is not None:
                    py_prefix = f"PYTHONPATH={shlex.quote(str(project_root))} "
                cmd = f"{py_prefix}python {shlex.quote(str(script_path))}"
            elif suffix == ".sh":
                cmd = f"bash {shlex.quote(str(script_path))}"
            elif suffix == ".js":
                cmd = f"node {shlex.quote(str(script_path))}"
            else:
                return f"echo 'Error: Unsupported script type: {suffix}'"

        if script_args and script_args.strip():
            try:
                parts = shlex.split(script_args.strip())
                cmd += " " + " ".join(shlex.quote(p) for p in parts)
            except ValueError:
                cmd += " " + script_args.strip()

        return cmd
