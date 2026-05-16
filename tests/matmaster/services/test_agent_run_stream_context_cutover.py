from __future__ import annotations

import hashlib

import pytest

from matmaster.context.assembly import (
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import ContextAssemblyPorts, SessionEvent, UserInstructions
from matmaster.context.sections import ContextView
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)
from src.services.user_turn_context_service import (
    DEFAULT_TURN_TRANSFORM,
    USER_CONTEXT_RENDER_VERSION,
    USER_TURN_CONTEXT_SCHEMA_VERSION,
    UserInstructionsInfo,
    build_user_turn_context_payload,
    render_provider_facing_current_message_content,
    render_runtime_task_for_user_turn_context,
)


class _StubEventsPort:
    def __init__(self, events: tuple[SessionEvent, ...] = ()) -> None:
        self.calls: list[object] = []
        self._events = events

    async def load_events(self, query):
        self.calls.append(query)
        return self._events


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _info(text: str) -> UserInstructionsInfo:
    return UserInstructionsInfo(text=text, hash=_hash(text), truncated=False)


def _bundle(text: str) -> UserInstructions:
    return UserInstructions(text=text, hash=_hash(text), truncated=False)


def _assembler(events: tuple[SessionEvent, ...] = ()) -> ContextAssembler:
    return ContextAssembler(
        ports=ContextAssemblyPorts(session_events=_StubEventsPort(events))
    )


async def _phase2c_payload(
    *,
    user_text: str,
    instructions: UserInstructions,
    attachments: TurnAttachmentsSource | None = None,
) -> dict:
    result = await _assembler().assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text=user_text),
                attachments=attachments or TurnAttachmentsSource(),
                pre_turn_history_event_id=0,
            ),
            user_instructions=instructions,
        ),
    )
    rendered_message = result.user_turn_context.to_message(ContextView.RUNTIME)
    return {
        "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
        "kind": "anchor",
        "message": rendered_message.model_dump(mode="json"),
        "user_instructions_hash": result.user_instructions_hash,
        "transform": DEFAULT_TURN_TRANSFORM,
        "render_version": USER_CONTEXT_RENDER_VERSION,
    }


def _phase1_payload(
    *,
    user_text: str,
    instructions: UserInstructionsInfo,
    attachment_text: str = "",
) -> dict:
    rendered_runtime_task = render_runtime_task_for_user_turn_context(
        user_prompt=user_text,
        user_instructions=instructions,
        kind="anchor",
    )
    rendered_message_content = render_provider_facing_current_message_content(
        rendered_runtime_task=rendered_runtime_task,
        attachment_text=attachment_text,
    )
    return build_user_turn_context_payload(
        kind="anchor",
        rendered_message_content=rendered_message_content,
        images=[],
        user_instructions=instructions,
        transform=DEFAULT_TURN_TRANSFORM,
    )


@pytest.mark.asyncio
async def test_phase2c_base_prompt_delta_from_phase1_renderer_is_explicit() -> None:
    """Task 5.5 oracle: expose the legacy wrapper -> context wrapper delta."""
    old_payload = _phase1_payload(
        user_text="calculate lattice parameter",
        instructions=_info("Use SI units."),
    )
    new_payload = await _phase2c_payload(
        user_text="calculate lattice parameter",
        instructions=_bundle("Use SI units."),
    )

    old_content = old_payload["message"]["content"]
    new_content = new_payload["message"]["content"]

    assert old_content != new_content
    assert old_content.startswith(
        '<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">'
    )
    assert "Treat it as user-level preferences." in old_content
    assert old_content.endswith("calculate lattice parameter")
    assert new_content == (
        "<user_instructions>\nUse SI units.\n</user_instructions>"
        "\n\n"
        "<current_instruction>\ncalculate lattice parameter\n</current_instruction>"
    )


@pytest.mark.asyncio
async def test_current_attachment_prompt_shape_delta_is_explicit_before_cutover() -> None:
    """Current baseline differs for attachment-bearing turns; keep the delta visible."""
    old_payload = _phase1_payload(
        user_text="inspect this file",
        instructions=_info("Be precise."),
        attachment_text="[Available attachments]\nfile_1 a.txt /tmp/a.txt",
    )
    new_payload = await _phase2c_payload(
        user_text="inspect this file",
        instructions=_bundle("Be precise."),
        attachments=TurnAttachmentsSource(files=("/tmp/a.txt",)),
    )

    old_content = old_payload["message"]["content"]
    new_content = new_payload["message"]["content"]

    assert old_content != new_content
    assert old_content.startswith(
        '<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">'
    )
    assert old_content.endswith(
        "inspect this file\n\n[Available attachments]\nfile_1 a.txt /tmp/a.txt"
    )
    assert new_content == (
        "<user_instructions>\nBe precise.\n</user_instructions>"
        "\n\n"
        "<current_instruction>\ninspect this file\n\n"
        "[Current attachments]\nfile_1 a.txt /tmp/a.txt\n</current_instruction>"
    )
