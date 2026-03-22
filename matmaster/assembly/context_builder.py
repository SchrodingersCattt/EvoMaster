"""ContextBuilder -- sectioned system prompt assembler.

Constructs the system prompt from multiple sources in a fixed order.
LLM prompt caching benefits from stable prefix, so high-frequency change
sections (task, memory) are placed last.

Section order: identity -> mode_contract -> skills -> tools -> memory -> task

Used by all Exp.assemble() implementations to build system prompts for
AgentRuntimeSpec.
"""

from __future__ import annotations

from typing import Any

from matmaster.assembly.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext


class ContextBuilder:
    """Sectioned system prompt assembler.

    Section order (fixed): identity -> mode_contract -> skills -> tools -> memory -> task
    LLM prompt caching benefits from stable prefix, so high-frequency change sections
    (task, memory) are placed last.
    """

    SEPARATOR = "\n\n---\n\n"

    SECTION_ORDER = ("identity", "mode_contract", "skills", "tools", "memory", "task")

    _DEFAULT_IDENTITY = "You are a helpful AI assistant."

    _MODE_CONTRACTS: dict[str, str] = {
        "direct": (
            "You are in direct execution mode. "
            "Complete the user's task directly using available tools."
        ),
        "planner": (
            "You are in planner mode. "
            "Break down the task into steps, plan each step, then execute."
        ),
    }

    def build(
        self,
        ctx: PlaygroundContext,
        tool_registry: ToolRegistry,
        *,
        mode: str = "direct",
        identity: str | None = None,
        skill_registry: Any = None,
        memory_context: str | None = None,
        task_context: str | None = None,
        disabled_sections: set[str] | None = None,
    ) -> str:
        """Assemble system prompt from sections in fixed order.

        Args:
            ctx: PlaygroundContext from Playground.setup().
            tool_registry: ToolRegistry with registered tools.
            mode: Execution mode ('direct' or 'planner').
            identity: Custom identity text. Defaults to standard assistant identity.
            skill_registry: Optional skill registry with get_meta_info_context().
            memory_context: Optional memory/conversation summary text.
            task_context: Optional task description text.
            disabled_sections: Set of section names to skip.

        Returns:
            Assembled system prompt string with sections joined by SEPARATOR.
        """
        disabled = disabled_sections or set()

        # Map section names to their builder calls
        section_builders: dict[str, str] = {}

        for section_name in self.SECTION_ORDER:
            if section_name in disabled:
                continue

            content = self._build_section(
                section_name,
                identity=identity,
                mode=mode,
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
        identity: str | None,
        mode: str,
        skill_registry: Any,
        tool_registry: ToolRegistry,
        memory_context: str | None,
        task_context: str | None,
    ) -> str:
        """Dispatch to the appropriate section builder."""
        if name == "identity":
            return self._build_identity(identity or self._DEFAULT_IDENTITY)
        if name == "mode_contract":
            return self._build_mode_contract(mode)
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
        """Build the identity section."""
        return f"# Identity\n\n{identity}"

    def _build_mode_contract(self, mode: str) -> str:
        """Build the mode contract section.

        Supported modes: 'direct', 'planner'. Unknown modes fall back
        to the mode string itself as description.
        """
        contract_text = self._MODE_CONTRACTS.get(mode, f"Mode: {mode}")
        return f"# Mode Contract\n\n{contract_text}"

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
