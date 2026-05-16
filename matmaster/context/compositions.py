from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextSection
from matmaster.context.sources.compacted_history import CompactedHistorySource
from matmaster.context.sources.session_jobs import SessionJobsSource
from matmaster.context.sources.turn_input import TurnInput
from matmaster.context.sources.user_instructions import UserInstructionsSource
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
    session_jobs: SessionJobs = field(default_factory=SessionJobs.empty)
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
    return CompactedHistorySource(summary=inputs.compacted_history_summary).to_sections()


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
    if inputs.defer_turn_instruction:
        turn_input = dataclasses.replace(
            turn_input,
            instruction=dataclasses.replace(
                turn_input.instruction,
                deferred=True,
            ),
        )
    return turn_input.to_sections(
        split_attachments=inputs.split_turn_attachments,
    )


def _step_session_jobs(inputs: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    return SessionJobsSource.from_jobs(inputs.session_jobs).to_sections()


ANCHOR_COMPOSITION = ContextComposition(
    name="anchor",
    steps=(
        _step_user_instructions,
        _step_session_sections,
        _step_turn_input,
        _step_session_jobs,
    ),
)

CONTINUATION_COMPOSITION = ContextComposition(
    name="continuation",
    steps=(
        _step_turn_input,
        _step_session_jobs,
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
        _step_session_jobs,
    ),
)
