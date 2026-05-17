from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from matmaster.context.assembly import ContextRenderOptions
from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.context.system_prompt import SystemPromptBuilder
from matmaster.core.playground import PlaygroundContext
from matmaster.core.runtime_context_assembly import (
    build_runtime_context_assembly,
    build_session_context_factory,
    empty_skill_resolver,
)
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.runtime import AgentRuntimeSpec


class _Provider:
    async def __aenter__(self) -> _Provider:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="ok", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="ok", finish_reason="stop")


def test_build_session_context_factory_invokes_resolver_per_call() -> None:
    captured: list[tuple[SessionEvent, ...]] = []

    def resolver(events: tuple[SessionEvent, ...]) -> tuple[ActiveSkill, ...]:
        captured.append(events)
        return (ActiveSkill(name="pxrd"),)

    factory = build_session_context_factory(skill_resolver=resolver)
    events = (SessionEvent(id=1, event_type="query", source="User", content={}),)
    builder = factory(events)

    assert captured == [events]
    assert builder.active_skills == (ActiveSkill(name="pxrd"),)


def test_empty_skill_resolver_returns_empty_tuple() -> None:
    assert empty_skill_resolver(()) == ()
    assert (
        empty_skill_resolver(
            (
                SessionEvent(
                    id=1,
                    event_type="skill_hit",
                    source=None,
                    content={"skill_name": "x"},
                ),
            )
        )
        == ()
    )


def test_runtime_context_assembly_ignores_run_meta_tool_render_ghosts(tmp_path) -> None:
    spec = AgentRuntimeSpec(
        llm_provider=_Provider(),
        system_prompt_builder=SystemPromptBuilder(),
    )
    ctx = PlaygroundContext(
        workdir=tmp_path,
        session_type="local",
        session_id="sess-1",
        cache_area=tmp_path / "cache",
        run_meta={
            "legal_mcp_servers": {"mat_xrd"},
            "schemas_by_server": {"mat_xrd": [{"name": "read"}]},
            "split_turn_attachments": True,
        },
    )

    assembly = build_runtime_context_assembly(
        spec=spec,
        ctx=ctx,
        skill_resolver=empty_skill_resolver,
        spawn_id=None,
        logger=logging.getLogger(__name__),
    )

    assert assembly.context_assembler is not None
    assert assembly.context_assembler._render_options == ContextRenderOptions()
    assert assembly.context_assembler._session_context_factory is not None
    builder = assembly.context_assembler._session_context_factory(())
    assert builder.legal_mcp_servers is None
    assert builder.schemas_by_server is None
