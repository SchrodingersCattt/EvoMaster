from __future__ import annotations

from matmaster.context.compositions import (
    ANCHOR_COMPOSITION,
    COMPACTED_COMPOSITION,
    CONTINUATION_COMPOSITION,
    ContextCompositionInputs,
)
from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)


def _session_section() -> ContextSection:
    return ContextSection(
        key="session_tools",
        tag="session_tools",
        content="Bash, Read",
        order=SectionOrder.SESSION_TOOLS,
        views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
    )


class OverrideSource:
    def to_sections(self) -> tuple[ContextSection, ...]:
        return (
            ContextSection(
                key="session_attachments",
                tag="session_attachments",
                content="file_1 old.cif https://example.com/old.cif",
                order=SectionOrder.SESSION_ATTACHMENTS,
                views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
            ),
        )


def _job(job_id: str, status: str = "finished", job_name: str | None = None) -> dict:
    row = {"job_id": job_id, "status": status}
    if job_name is not None:
        row["job_name"] = job_name
    return row


def test_composition_inputs_defaults_are_empty() -> None:
    inputs = ContextCompositionInputs()

    assert inputs.user_instructions_text == ""
    assert inputs.compacted_history_summary == ""
    assert inputs.turn_input is None
    assert inputs.session_sections == ()
    assert inputs.workspace_jobs == WorkspaceJobs.empty()
    assert inputs.session_attachments_override is None
    assert inputs.defer_turn_instruction is False


def test_anchor_composition_includes_instructions_session_turn_and_jobs() -> None:
    context = ANCHOR_COMPOSITION.apply(
        ContextCompositionInputs(
            user_instructions_text="Use SI units.",
            turn_input=TurnInput(
                attachments=TurnAttachmentsSource(images=("https://example.com/a.png",))
            ),
            session_sections=(_session_section(),),
            workspace_jobs=WorkspaceJobs(active_jobs=({"id": "job-1"},)),
        )
    )

    assert [section.key for section in context.sections] == [
        "user_instructions",
        "session_tools",
        "current_instruction",
        "workspace_jobs",
    ]
    assert context.images[0].url == "https://example.com/a.png"


def test_anchor_delivery_turn_embeds_job_template_in_current_instruction() -> None:
    context = ANCHOR_COMPOSITION.apply(
        ContextCompositionInputs(
            user_instructions_text="Use SI units.",
            turn_input=TurnInput(
                instruction=TurnInstructionSource(
                    user_text="本会话出现失败的 Bohrium 作业，仍有作业在运行。"
                )
            ),
            workspace_jobs=WorkspaceJobs(
                mode="session_workspace_delivery",
                pending_terminal_jobs=(
                    _job("f1", "failed", "relax-fail"),
                    _job("t1", "finished", "relax-ok"),
                ),
                active_jobs=(_job("a1", "running", "relax-running"),),
            ),
        )
    )

    runtime = context.render(ContextView.RUNTIME)

    assert "<workspace_jobs>" not in runtime
    assert "<delivery_directive>" not in runtime
    assert "relax-running" not in runtime
    assert "本会话出现失败的 Bohrium 作业" not in runtime
    assert (
        "<current_instruction>\n"
        "以下作业失败：\n"
        "job_id, job_name\n"
        "f1, relax-fail\n"
        "\n"
        "以下作业成功结束：\n"
        "job_id, job_name\n"
        "t1, relax-ok\n"
        "</current_instruction>" in runtime
    )


def test_anchor_observation_jobs_do_not_render_delivery_directive() -> None:
    context = ANCHOR_COMPOSITION.apply(
        ContextCompositionInputs(
            user_instructions_text="Use SI units.",
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Check workspace jobs.")
            ),
            workspace_jobs=WorkspaceJobs(
                mode="workspace_observation",
                pending_terminal_jobs=(_job("t1", "failed"),),
            ),
        )
    )

    runtime = context.render(ContextView.RUNTIME)

    assert "<workspace_jobs>" in runtime
    assert "<delivery_directive>" not in runtime


def test_continuation_composition_excludes_user_instructions_and_session_sections() -> (
    None
):
    context = CONTINUATION_COMPOSITION.apply(
        ContextCompositionInputs(
            user_instructions_text="Use SI units.",
            turn_input=TurnInput(attachments=TurnAttachmentsSource(files=("a.cif",))),
            session_sections=(_session_section(),),
            workspace_jobs=WorkspaceJobs(active_jobs=({"id": "job-1"},)),
        )
    )

    assert [section.key for section in context.sections] == [
        "current_instruction",
        "workspace_jobs",
    ]


def test_compacted_composition_includes_compacted_history_and_override() -> None:
    context = COMPACTED_COMPOSITION.apply(
        ContextCompositionInputs(
            user_instructions_text="Use SI units.",
            compacted_history_summary="Earlier turns mention FeO.",
            turn_input=TurnInput(),
            session_sections=(_session_section(),),
            workspace_jobs=WorkspaceJobs.empty(),
            session_attachments_override=OverrideSource(),
        )
    )

    assert [section.key for section in context.sections] == [
        "user_instructions",
        "compacted_history",
        "session_attachments",
        "session_tools",
    ]


def test_defer_turn_instruction_moves_instruction_to_last_order() -> None:
    context = COMPACTED_COMPOSITION.apply(
        ContextCompositionInputs(
            compacted_history_summary="Earlier turns mention FeO.",
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Continue the analysis.")
            ),
            session_attachments_override=None,
            defer_turn_instruction=True,
        )
    )

    turn_section = [
        section for section in context.sections if section.key == "current_instruction"
    ][0]
    assert turn_section.order == SectionOrder.TURN_INSTRUCTION_LAST


def test_compaction_inputs_can_split_turn_attachments() -> None:
    result = COMPACTED_COMPOSITION.apply(
        ContextCompositionInputs(
            compacted_history_summary="Earlier summary.",
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Use current file."),
                attachments=TurnAttachmentsSource(files=("https://oss/a.cif",)),
                pre_turn_history_event_id=5,
            ),
            split_turn_attachments=True,
        )
    )

    runtime = result.render(ContextView.RUNTIME)
    checkpoint = result.render(ContextView.CHECKPOINT)
    assert "<current_instruction>" in runtime
    assert "<turn_attachments>" in runtime
    assert "<turn_attachments>" not in checkpoint
