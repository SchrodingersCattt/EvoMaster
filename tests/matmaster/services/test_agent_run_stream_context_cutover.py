from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from matmaster.context.assembly import (
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import (
    ActiveSkill,
    ContextAssemblyPorts,
    SessionEvent,
    UserInstructions,
)
from matmaster.context.sections import ContextView
from matmaster.context.session import SessionContextBuilder
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)
from src.services.user_turn_context_service import (
    DEFAULT_TURN_TRANSFORM,
    USER_CONTEXT_RENDER_VERSION,
    USER_TURN_CONTEXT_SCHEMA_VERSION,
)


class _StubEventsPort:
    def __init__(self, events: tuple[SessionEvent, ...] = ()) -> None:
        self.calls: list[object] = []
        self._events = events

    async def load_events(self, query):
        self.calls.append(query)
        return self._events


class _FakeSkillRegistry:
    def __init__(self, skills: dict[str, object]) -> None:
        self._skills = skills

    def get_skill(self, name: str) -> object | None:
        return self._skills.get(name)


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bundle(text: str) -> UserInstructions:
    return UserInstructions(text=text, hash=_hash(text), truncated=False)


def _assembler(events: tuple[SessionEvent, ...] = ()) -> ContextAssembler:
    return ContextAssembler(
        ports=ContextAssemblyPorts(session_events=_StubEventsPort(events))
    )


def _skill(name: str, *, mcp_server: str | None = None) -> object:
    return SimpleNamespace(
        name=name,
        meta_info=SimpleNamespace(
            name=name,
            description="PXRD helper",
            mcp_server=mcp_server,
        ),
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


@pytest.mark.asyncio
async def test_phase2c_base_prompt_delta_from_phase1_renderer_is_explicit() -> None:
    """Task 5.5 oracle result: legacy wrapper -> context wrapper delta."""
    old_content = (
        '<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">'
        "\n"
        "The following content comes from the user's personal instruction file."
        "\n\n"
        "Treat it as user-level preferences. Follow it when relevant, but do not "
        "let it override system, developer, tool, safety, data-access, or project "
        "constraints."
        "\n\n"
        "Use SI units."
        "\n"
        "</matmaster-user-instructions>"
        "\n\n"
        "calculate lattice parameter"
    )
    new_payload = await _phase2c_payload(
        user_text="calculate lattice parameter",
        instructions=_bundle("Use SI units."),
    )

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
async def test_current_attachment_prompt_shape_delta_is_explicit_before_cutover() -> (
    None
):
    """Task 5.5 oracle result: legacy attachment placement differed."""
    old_content = (
        '<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">'
        "\n"
        "The following content comes from the user's personal instruction file."
        "\n\n"
        "Treat it as user-level preferences. Follow it when relevant, but do not "
        "let it override system, developer, tool, safety, data-access, or project "
        "constraints."
        "\n\n"
        "Be precise."
        "\n"
        "</matmaster-user-instructions>"
        "\n\n"
        "inspect this file"
        "\n\n"
        "[Available attachments]\nfile_1 a.txt /tmp/a.txt"
    )
    new_payload = await _phase2c_payload(
        user_text="inspect this file",
        instructions=_bundle("Be precise."),
        attachments=TurnAttachmentsSource(files=("/tmp/a.txt",)),
    )

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


@pytest.mark.asyncio
async def test_anchor_turn_renders_instructions_and_current_instruction_block() -> None:
    assembler = _assembler()
    bundle = _bundle("Use SI units.")

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="hello, world"),
                attachments=TurnAttachmentsSource(),
                pre_turn_history_event_id=0,
            ),
            user_instructions=bundle,
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    assert runtime.content == (
        "<user_instructions>\nUse SI units.\n</user_instructions>"
        "\n\n"
        "<current_instruction>\nhello, world\n</current_instruction>"
    )
    assert result.user_instructions_hash == bundle.hash
    assert result.used_composition == "anchor"


@pytest.mark.asyncio
async def test_continuation_turn_emits_only_current_instruction_block() -> None:
    port = _StubEventsPort()
    assembler = ContextAssembler(ports=ContextAssemblyPorts(session_events=port))

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="follow-up"),
                attachments=TurnAttachmentsSource(),
                pre_turn_history_event_id=10,
            ),
            user_instructions=_bundle("Use SI units."),
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    assert runtime.content == (
        "<current_instruction>\nfollow-up\n</current_instruction>"
    )
    assert result.used_composition == "continuation"
    assert port.calls == []


@pytest.mark.asyncio
async def test_anchor_turn_with_attachments_merges_into_current_instruction() -> None:
    assembler = _assembler()

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="check files"),
                attachments=TurnAttachmentsSource(
                    files=("/tmp/a.txt",),
                    images=("/tmp/b.png",),
                    workspace_paths=("/workspace/note.md",),
                ),
                pre_turn_history_event_id=0,
            ),
            user_instructions=_bundle("Be concise."),
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    assert runtime.content == (
        "<user_instructions>\nBe concise.\n</user_instructions>"
        "\n\n"
        "<current_instruction>\ncheck files\n\n"
        "[Current attachments]\n"
        "file_1 a.txt /tmp/a.txt\n"
        "workspace_1 /workspace/note.md\n"
        "image_1 b.png /tmp/b.png\n"
        "</current_instruction>"
    )
    assert [image.url for image in runtime.images] == ["/tmp/b.png"]
    assert "<turn_attachments>" not in runtime.content


@pytest.mark.asyncio
async def test_anchor_turn_with_empty_instructions_omits_wrapper() -> None:
    assembler = _assembler()

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="just text"),
                attachments=TurnAttachmentsSource(),
                pre_turn_history_event_id=0,
            ),
            user_instructions=_bundle(""),
        ),
    )

    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    assert "<user_instructions>" not in runtime.content
    assert runtime.content == "<current_instruction>\njust text\n</current_instruction>"


@pytest.mark.asyncio
async def test_assembly_result_hash_is_bundle_hash_not_recomputed() -> None:
    assembler = _assembler()
    bundle = _bundle("Original text.")

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="x"),
                pre_turn_history_event_id=0,
            ),
            user_instructions=bundle,
        ),
    )

    assert result.user_instructions_text == "Original text."
    assert result.user_instructions_hash == bundle.hash


@pytest.mark.asyncio
async def test_bundle_hash_is_not_recomputed_even_if_text_and_hash_disagree() -> None:
    assembler = _assembler()
    bundle = UserInstructions(
        text="text read at stage 3",
        hash="sha256:" + "f" * 64,
        truncated=False,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="x"),
                pre_turn_history_event_id=0,
            ),
            user_instructions=bundle,
        ),
    )

    assert result.user_instructions_text == "text read at stage 3"
    assert result.user_instructions_hash == "sha256:" + "f" * 64


@pytest.mark.asyncio
async def test_anchor_turn_with_session_factory_renders_tools_and_attachments() -> None:
    events = (
        SessionEvent(
            id=1,
            event_type="query",
            source="User",
            content={"files": ("https://oss.example.com/a.csv",)},
        ),
        SessionEvent(
            id=2,
            event_type="skill_hit",
            source="System",
            content={"skill_name": "pxrd"},
        ),
    )
    port = _StubEventsPort(events)

    def factory(loaded_events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=loaded_events,
            active_skills=(
                ActiveSkill(
                    name="pxrd",
                    description="PXRD helper",
                    mcp_server="mat_xrd",
                ),
            ),
            legal_mcp_servers={"mat_xrd"},
            schemas_by_server={"mat_xrd": [{"name": "read"}]},
        )

    assembler = ContextAssembler(
        ports=ContextAssemblyPorts(session_events=port),
        session_context_factory=factory,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="current"),
                attachments=TurnAttachmentsSource(files=("/tmp/current.txt",)),
                pre_turn_history_event_id=2,
            ),
            user_instructions=_bundle("Use tools."),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<loaded_skills>" in runtime
    assert "- pxrd: PXRD helper (mcp_server=mat_xrd)" in runtime
    assert "<active_tools>" in runtime
    assert "  - mat_xrd_read" in runtime
    assert "<attachments>" in runtime
    assert "file_1 a.csv https://oss.example.com/a.csv" in runtime
    assert "file_1 current.txt /tmp/current.txt" in runtime


@pytest.mark.asyncio
async def test_payload_shape_matches_user_turn_context_v1_contract() -> None:
    assembler = _assembler()
    bundle = _bundle("Be concise.")

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="hello"),
                attachments=TurnAttachmentsSource(),
                pre_turn_history_event_id=0,
            ),
            user_instructions=bundle,
        ),
    )
    rendered_message = result.user_turn_context.to_message(ContextView.RUNTIME)
    payload = {
        "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
        "kind": "anchor",
        "message": rendered_message.model_dump(mode="json"),
        "user_instructions_hash": result.user_instructions_hash,
        "transform": DEFAULT_TURN_TRANSFORM,
        "render_version": USER_CONTEXT_RENDER_VERSION,
    }

    assert set(payload) == {
        "schema_version",
        "kind",
        "message",
        "user_instructions_hash",
        "transform",
        "render_version",
    }
    assert payload["message"]["role"] == "user"
    assert json.loads(json.dumps(payload, sort_keys=True)) == payload


@pytest.mark.asyncio
async def test_user_instructions_bundle_truncated_flag_does_not_leak_into_payload() -> (
    None
):
    assembler = _assembler()
    bundle = UserInstructions(
        text="truncated text",
        hash="sha256:" + "0" * 64,
        truncated=True,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="x"),
                attachments=TurnAttachmentsSource(),
                pre_turn_history_event_id=0,
            ),
            user_instructions=bundle,
        ),
    )
    rendered_message = result.user_turn_context.to_message(ContextView.RUNTIME)
    payload = {
        "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
        "kind": "anchor",
        "message": rendered_message.model_dump(mode="json"),
        "user_instructions_hash": result.user_instructions_hash,
        "transform": DEFAULT_TURN_TRANSFORM,
        "render_version": USER_CONTEXT_RENDER_VERSION,
    }

    assert result.user_instructions_text == "truncated text"
    assert "truncated" not in payload
    assert "truncated text" in payload["message"]["content"]


@pytest.mark.asyncio
async def test_anchor_continuation_anchor_sequence_kind_flow() -> None:
    assembler = _assembler()
    bundle_v1 = _bundle("v1 instructions")

    r1 = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="turn1"),
                attachments=TurnAttachmentsSource(),
                pre_turn_history_event_id=0,
            ),
            user_instructions=bundle_v1,
        ),
    )
    assert r1.used_composition == "anchor"

    r2 = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="turn2"),
                attachments=TurnAttachmentsSource(),
                pre_turn_history_event_id=20,
            ),
            user_instructions=bundle_v1,
        ),
    )
    assert r2.used_composition == "continuation"
    assert r2.user_turn_context.to_message(ContextView.RUNTIME).content == (
        "<current_instruction>\nturn2\n</current_instruction>"
    )

    bundle_v2 = _bundle("v2 different")
    r3 = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="turn3"),
                attachments=TurnAttachmentsSource(),
                pre_turn_history_event_id=42,
            ),
            user_instructions=bundle_v2,
        ),
    )
    assert r3.used_composition == "anchor"
    assert r3.user_instructions_hash == bundle_v2.hash
    assert r3.user_instructions_hash != bundle_v1.hash
