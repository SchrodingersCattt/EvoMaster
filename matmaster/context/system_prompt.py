"""SystemPromptBuilder -- sectioned system prompt assembler.

All static text is caller-supplied; an empty string causes the section to be
skipped. LLM prompt caching benefits from a stable prefix, so high-frequency
sections (task, memory) are placed last.
"""

from __future__ import annotations

from typing import Any

_GENERIC_TOOLS_TEXT = (
    "Use the tools declared in function calling. "
    "Each tool's name, description, and parameter schema are "
    "provided in the function definitions."
)

_TEXT_SECTION_HEADINGS: dict[str, str] = {
    "system_prompt": "System",
    "identity": "Identity",
    "memory": "Memory",
    "task": "Task Context",
}


def _format_text_section(heading: str, text: str | None) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return ""
    return f"# {heading}\n\n{stripped}"


class SystemPromptBuilder:
    """Assemble system prompt sections in a fixed, cache-friendly order."""

    SEPARATOR = "\n\n---\n\n"

    SYSTEM_SECTION_ORDER = (
        "system_prompt",
        "identity",
        "skills",
        "tools",
        "memory",
        "task",
    )

    def build_system_prompt(
        self,
        tool_registry: Any = None,
        *,
        system_prompt: str = "",
        identity: str = "",
        skill_registry: Any = None,
        memory_context: str | None = None,
        task_context: str | None = None,
        disabled_sections: set[str] | None = None,
    ) -> str:
        disabled = disabled_sections or set()
        text_values: dict[str, str | None] = {
            "system_prompt": system_prompt,
            "identity": identity,
            "memory": memory_context,
            "task": task_context,
        }

        rendered: list[str] = []
        for name in self.SYSTEM_SECTION_ORDER:
            if name in disabled:
                continue
            if name == "skills":
                content = self._build_skills(skill_registry)
            elif name == "tools":
                content = f"# Tools\n\n{_GENERIC_TOOLS_TEXT}"
            else:
                content = _format_text_section(
                    _TEXT_SECTION_HEADINGS[name], text_values.get(name)
                )
            if content:
                rendered.append(content)

        return self.SEPARATOR.join(rendered)

    @staticmethod
    def _build_skills(skill_registry: Any) -> str:
        if skill_registry is None:
            return ""
        method = getattr(skill_registry, "get_meta_info_context", None)
        if method is None:
            return ""
        context = method()
        if not context:
            return ""
        return f"# Skills\n\n{context}"
