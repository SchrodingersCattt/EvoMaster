from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from matmaster.context.ports import WorkspaceJobs
from matmaster.context.sections import ContextSection
from matmaster.context.sources.compacted_history import CompactedHistorySource
from matmaster.context.sources.turn_input import TurnInput, TurnInstructionSource
from matmaster.context.sources.user_instructions import UserInstructionsSource
from matmaster.context.sources.workspace_jobs import WorkspaceJobsSource
from matmaster.context.turn_context import UserTurnContext


class SectionSource(Protocol):
    def to_sections(self) -> tuple[ContextSection, ...]:
        raise NotImplementedError


@dataclass(frozen=True)
class ContextCompositionInputs:
    user_instructions_text: str = ""
    compacted_history_summary: str = ""
    turn_input: TurnInput | None = None
    session_sections: tuple[ContextSection, ...] = ()
    workspace_jobs: WorkspaceJobs = field(default_factory=WorkspaceJobs.empty)
    session_attachments_override: SectionSource | None = None
    defer_turn_instruction: bool = False
    split_turn_attachments: bool = False


CompositionStep = Callable[[ContextCompositionInputs], tuple[ContextSection, ...]]


@dataclass(frozen=True)
class ContextComposition:
    name: str
    steps: tuple[CompositionStep, ...]

    def apply(self, inputs: ContextCompositionInputs) -> UserTurnContext:
        section_groups = tuple(step(inputs) for step in self.steps)
        images = ()
        if inputs.turn_input is not None:
            images = inputs.turn_input.attachments.images_as_parts()
        return UserTurnContext.from_sources(*section_groups, images=images)


def _step_user_instructions(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    return UserInstructionsSource(text=inputs.user_instructions_text).to_sections()


def _step_compacted_history(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    return CompactedHistorySource(
        summary=inputs.compacted_history_summary
    ).to_sections()


def _step_session_sections(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    return inputs.session_sections


def _step_session_attachments_override(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    if inputs.session_attachments_override is None:
        return ()
    return inputs.session_attachments_override.to_sections()


def _step_turn_input(inputs: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    if inputs.turn_input is None:
        return ()
    turn_input = inputs.turn_input
    delivery_text = WorkspaceJobsSource.delivery_instruction_text(inputs.workspace_jobs)
    if delivery_text:
        turn_input = dataclasses.replace(
            turn_input,
            instruction=TurnInstructionSource(
                user_text=delivery_text,
                deferred=turn_input.instruction.deferred,
                tag=turn_input.instruction.tag,
            ),
        )
    if inputs.defer_turn_instruction:
        turn_input = turn_input.with_deferred_instruction()
    return turn_input.to_sections(
        split_attachments=inputs.split_turn_attachments,
    )


def _step_workspace_jobs(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    if inputs.workspace_jobs.mode == "session_workspace_delivery":
        return ()
    return WorkspaceJobsSource.from_jobs(inputs.workspace_jobs).to_sections()


ANCHOR_COMPOSITION = ContextComposition(
    name="anchor",
    steps=(
        _step_user_instructions,
        _step_session_sections,
        _step_turn_input,
        _step_workspace_jobs,
    ),
)

CONTINUATION_COMPOSITION = ContextComposition(
    name="continuation",
    steps=(
        _step_turn_input,
        _step_workspace_jobs,
    ),
)

COMPACTED_COMPOSITION = ContextComposition(
    name="compacted",
    steps=(
        _step_user_instructions,
        _step_compacted_history,
        _step_session_attachments_override,
        _step_session_sections,
        _step_turn_input,
        _step_workspace_jobs,
    ),
)
