"""ContextBuilder -- sectioned system prompt assembler.

Constructs the system prompt from multiple sources in a fixed order.
LLM prompt caching benefits from stable prefix, so high-frequency change
sections (task, memory) are placed last.

Section order: identity -> mode_contract -> skills -> tools -> memory -> task

All static text (identity, mode_contract) comes from the caller (toml config).
ContextBuilder has no default text of its own -- empty string means the
section is skipped entirely.
"""

from __future__ import annotations

from typing import Any

from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext


class ContextBuilder:
    """Sectioned system prompt assembler.

    Section order (fixed): identity -> mode_contract -> skills -> tools -> memory -> task
    LLM prompt caching benefits from stable prefix, so high-frequency change sections
    (task, memory) are placed last.

    All static text is caller-supplied. Empty string = section skipped.
    """

    SEPARATOR = "\n\n---\n\n"

    SECTION_ORDER = ("identity", "mode_contract", "skills", "tools", "memory", "task")

    def build(
        self,
        ctx: PlaygroundContext,
        tool_registry: ToolRegistry,
        *,
        identity: str = "",
        mode_contract: str = "",
        skill_registry: Any = None,
        memory_context: str | None = None,
        task_context: str | None = None,
        disabled_sections: set[str] | None = None,
    ) -> str:
        """Assemble system prompt from sections in fixed order.

        Args:
            ctx: PlaygroundContext from Playground.prepare().
            tool_registry: ToolRegistry with registered tools.
            identity: Identity text from toml developer_instructions.
            mode_contract: Mode contract text from toml mode_contract.
            skill_registry: Optional skill registry with get_meta_info_context().
            memory_context: Optional memory/conversation summary text.
            task_context: Optional task description text.
            disabled_sections: Set of section names to skip.

        Returns:
            Assembled system prompt string with sections joined by SEPARATOR.
        """
        disabled = disabled_sections or set()

        section_builders: dict[str, str] = {}

        for section_name in self.SECTION_ORDER:
            if section_name in disabled:
                continue

            content = self._build_section(
                section_name,
                identity=identity,
                mode_contract=mode_contract,
                skill_registry=skill_registry,
                tool_registry=tool_registry,
                memory_context=memory_context,
                task_context=task_context,
            )

            if content:
                section_builders[section_name] = content

        return self.SEPARATOR.join(section_builders.values())

    def _build_section(
        self,
        name: str,
        *,
        identity: str,
        mode_contract: str,
        skill_registry: Any,
        tool_registry: ToolRegistry,
        memory_context: str | None,
        task_context: str | None,
    ) -> str:
        """Dispatch to the appropriate section builder."""
        if name == "identity":
            return self._build_identity(identity)
        if name == "mode_contract":
            return self._build_mode_contract(mode_contract)
        if name == "skills":
            return self._build_skills(skill_registry)
        if name == "tools":
            return self._build_tools(tool_registry)
        if name == "memory":
            return self._build_memory(memory_context)
        if name == "task":
            return self._build_task(task_context)
        return ""

    @staticmethod
    def _build_identity(identity: str) -> str:
        """Build the identity section. Empty string = skip."""
        text = identity.strip()
        if not text:
            return ""
        return f"# Identity\n\n{text}"

    @staticmethod
    def _build_mode_contract(mode_contract: str) -> str:
        """Build the mode contract section. Empty string = skip."""
        text = mode_contract.strip()
        if not text:
            return ""
        return f"# Mode Contract\n\n{text}"

    @staticmethod
    def _build_skills(skill_registry: Any) -> str:
        """Build the skills section from skill registry.

        Returns empty string if skill_registry is None or lacks
        get_meta_info_context().
        """
        if skill_registry is None:
            return ""
        method = getattr(skill_registry, "get_meta_info_context", None)
        if method is None:
            return ""
        context = method()
        if not context:
            return ""
        return f"# Skills\n\n{context}"

    @staticmethod
    def _build_tools(tool_registry: ToolRegistry) -> str:
        """Build the available tools section.

        Lists each tool as a bullet with name and description.
        """
        tools = tool_registry.all_tools
        if not tools:
            return ""
        lines = [f"- {tool.name}: {tool.description}" for tool in tools]
        return "# Available Tools\n\n" + "\n".join(lines)

    @staticmethod
    def _build_memory(memory_context: str | None) -> str:
        """Build the memory section. Returns empty string if no context."""
        if not memory_context:
            return ""
        return f"# Memory\n\n{memory_context}"

    @staticmethod
    def _build_task(task_context: str | None) -> str:
        """Build the task context section. Returns empty string if no context."""
        if not task_context:
            return ""
        return f"# Task Context\n\n{task_context}"
