from __future__ import annotations

from pathlib import Path

from matmaster.context.system_prompt import SystemPromptBuilder
from matmaster.core.playground import PlaygroundContext


def test_system_prompt_builder_builds_base_prompt() -> None:
    ctx = PlaygroundContext(
        workdir=Path("/tmp/test"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )
    builder = SystemPromptBuilder()

    result = builder.build_system_prompt(
        ctx,
        system_prompt="Base persona.",
        identity="Identity text.",
    )

    assert "Base persona." in result
    assert "Identity text." in result
