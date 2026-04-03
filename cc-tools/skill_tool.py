"""Skill tool -- CC-style slash command execution engine.

Skills are named capabilities (e.g., /commit, /review-pr) defined as
files on disk. The Skill tool loads and executes them within the
current conversation context.

Supports two execution modes:
- inline: expand skill content into current context
- forked: spawn a sub-agent to execute the skill
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Awaitable, ClassVar

from .base import BuiltinTool, ToolResult


class SkillTool(BuiltinTool):
    """Execute slash-command skills within the conversation."""

    name: ClassVar[str] = "Skill"
    description: ClassVar[str] = (
        "Execute a skill within the main conversation.\n\n"
        "Skills provide specialized capabilities and domain knowledge. "
        'Users reference them as slash commands (e.g., "/commit", "/review-pr").\n\n'
        "How to invoke:\n"
        '- skill: "commit" -- invoke by name\n'
        '- skill: "commit", args: "-m \'Fix bug\'" -- with arguments\n'
        '- skill: "ms-office-suite:pdf" -- fully qualified name\n\n'
        "Important:\n"
        "- Available skills are listed in system-reminder messages\n"
        "- Invoke the skill BEFORE generating any response about the task\n"
        "- Do not invoke a skill that is already running"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": 'The skill name (e.g., "commit", "review-pr", "pdf")',
            },
            "args": {
                "type": "string",
                "description": "Optional arguments for the skill",
            },
        },
        "required": ["skill"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        skill_registry: dict[str, Any] | None = None,
        skill_dirs: list[Path] | None = None,
        execute_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        """
        Args:
            skill_registry: Map of skill_name -> skill definition/config.
            skill_dirs: Directories to search for skill definition files.
            execute_fn: Async function to execute a loaded skill.
        """
        super().__init__(session=session, workdir=workdir)
        self._registry = skill_registry or {}
        self._skill_dirs = skill_dirs or []
        self._execute_fn = execute_fn

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        skill_name: str = arguments.get("skill", "")
        args: str = arguments.get("args", "")

        if not skill_name:
            return "Error: skill name is required"

        # Resolve namespace:skill format
        namespace = None
        if ":" in skill_name:
            namespace, skill_name = skill_name.rsplit(":", 1)

        # Look up skill definition
        skill_def = self._find_skill(skill_name, namespace)
        if skill_def is None:
            available = ", ".join(sorted(self._registry.keys()))
            return (
                f"Error: skill '{skill_name}' not found. "
                f"Available: {available or '(none registered)'}"
            )

        # Load skill content
        content = self._load_skill_content(skill_def)
        if content is None:
            return f"Error: could not load content for skill '{skill_name}'"

        return ToolResult.ok(
            content,
            skill_name=skill_name,
            namespace=namespace,
            args=args,
            mode=skill_def.get("mode", "inline"),
        )

    def _find_skill(
        self, name: str, namespace: str | None
    ) -> dict[str, Any] | None:
        """Find a skill by name, optionally scoped by namespace."""
        # Check registry first
        if namespace:
            qualified = f"{namespace}:{name}"
            if qualified in self._registry:
                return self._registry[qualified]
        if name in self._registry:
            return self._registry[name]

        # Search skill directories for definition files
        for skill_dir in self._skill_dirs:
            # Try namespace/name or just name
            candidates = []
            if namespace:
                candidates.append(skill_dir / namespace / name)
                candidates.append(skill_dir / f"{namespace}-{name}")
            candidates.append(skill_dir / name)

            for candidate in candidates:
                # Check for SKILL.md or index file
                for suffix in ("", ".md", "/SKILL.md", "/index.md"):
                    path = Path(str(candidate) + suffix)
                    if path.is_file():
                        return {
                            "name": name,
                            "namespace": namespace,
                            "path": str(path),
                            "mode": "inline",
                        }
        return None

    @staticmethod
    def _load_skill_content(skill_def: dict[str, Any]) -> str | None:
        """Load skill content from definition."""
        # Direct content
        if "content" in skill_def:
            return skill_def["content"]

        # File-based
        path = skill_def.get("path")
        if path:
            p = Path(path)
            if p.is_file():
                return p.read_text(errors="replace")

        return None

    def register_skill(self, name: str, definition: dict[str, Any]) -> None:
        """Register a skill definition at runtime."""
        self._registry[name] = definition
