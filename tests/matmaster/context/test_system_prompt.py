from __future__ import annotations

from matmaster.context.system_prompt import SystemPromptBuilder


def test_system_prompt_builder_builds_base_prompt() -> None:
    builder = SystemPromptBuilder()

    result = builder.build_system_prompt(
        system_prompt="Base persona.",
        identity="Identity text.",
    )

    assert "Base persona." in result
    assert "Identity text." in result
