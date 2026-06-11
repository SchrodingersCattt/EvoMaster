from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from matmaster.context.compositions import (
    ANCHOR_COMPOSITION,
    COMPACTED_COMPOSITION,
    CONTINUATION_COMPOSITION,
    ContextComposition,
    ContextCompositionInputs,
    SectionSource,
)
from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    UserInstructions,
)
from matmaster.context.sections import ContextSection
from matmaster.context.session import SessionContextBuilder
from matmaster.context.sources.turn_input import TurnInput
from matmaster.context.turn_context import UserTurnContext


class ContextAssemblyIntent(str, Enum):
    ANCHOR_TURN = "anchor_turn"
    CONTINUATION_TURN = "continuation_turn"
    PREFLIGHT_COMPACTION = "preflight_compaction"
    RUNTIME_COMPACTION = "runtime_compaction"

    @property
    def is_anchor_turn(self) -> bool:
        return self == ContextAssemblyIntent.ANCHOR_TURN

    @property
    def is_compaction(self) -> bool:
        return self in {
            ContextAssemblyIntent.PREFLIGHT_COMPACTION,
            ContextAssemblyIntent.RUNTIME_COMPACTION,
        }


@dataclass(frozen=True)
class TurnAssemblyRequest:
    session_id: str
    spawn_id: str | None
    turn_input: TurnInput
    user_instructions: UserInstructions


@dataclass(frozen=True)
class CompactionAssemblyRequest:
    session_id: str
    spawn_id: str | None
    user_instructions: UserInstructions
    compacted_history_summary: str
    turn_input: TurnInput | None = None
    covered_until_event_id: int | None = None
    session_attachments_override: SectionSource | None = None


@dataclass(frozen=True)
class ContextRenderOptions:
    split_turn_attachments: bool = False


@dataclass(frozen=True)
class AssemblyResult:
    user_turn_context: UserTurnContext
    user_instructions_text: str
    user_instructions_hash: str
    used_composition: str
    covered_until_event_id: int | None = None


SessionSectionBuilder = Callable[
    [tuple[SessionEvent, ...], int, bool],
    tuple[ContextSection, ...],
]
SessionContextFactory = Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]


def _no_session_sections(
    events: tuple[SessionEvent, ...],
    until_event_id: int,
    include_attachments: bool,
) -> tuple[ContextSection, ...]:
    """Default builder when no session_context_factory is supplied."""
    return ()


_INTENT_COMPOSITION_MAP: dict[ContextAssemblyIntent, ContextComposition] = {
    ContextAssemblyIntent.ANCHOR_TURN: ANCHOR_COMPOSITION,
    ContextAssemblyIntent.CONTINUATION_TURN: CONTINUATION_COMPOSITION,
    ContextAssemblyIntent.PREFLIGHT_COMPACTION: COMPACTED_COMPOSITION,
    ContextAssemblyIntent.RUNTIME_COMPACTION: COMPACTED_COMPOSITION,
}


def _resolve_covered_until(
    intent: ContextAssemblyIntent,
    request: CompactionAssemblyRequest,
) -> int:
    if intent == ContextAssemblyIntent.RUNTIME_COMPACTION:
        if request.covered_until_event_id is None:
            raise ValueError(
                "RUNTIME_COMPACTION requires explicit covered_until_event_id"
            )
        return request.covered_until_event_id
    if request.covered_until_event_id is not None:
        return request.covered_until_event_id
    if request.turn_input is not None:
        return request.turn_input.pre_turn_history_event_id
    raise ValueError(
        "PREFLIGHT_COMPACTION requires turn_input or explicit " "covered_until_event_id"
    )


class ContextAssembler:
    def __init__(
        self,
        ports: ContextAssemblyPorts,
        *,
        session_context_factory: SessionContextFactory | None = None,
        render_options: ContextRenderOptions | None = None,
        _session_section_builder_for_tests: SessionSectionBuilder | None = None,
    ) -> None:
        self._ports = ports
        self._render_options = render_options or ContextRenderOptions()
        self._session_context_factory = session_context_factory
        # Production wiring must use session_context_factory;
        # _session_section_builder_for_tests is a unit-test-only seam.
        if _session_section_builder_for_tests is not None:
            self._session_section_builder = _session_section_builder_for_tests
        elif session_context_factory is not None:
            self._session_section_builder = self._build_via_factory
        else:
            self._session_section_builder = _no_session_sections

    def _build_via_factory(
        self,
        events: tuple[SessionEvent, ...],
        until_event_id: int,
        include_attachments: bool,
    ) -> tuple[ContextSection, ...]:
        assert self._session_context_factory is not None
        # Defense in depth: even if a port returned events past the boundary,
        # the factory and its skill resolver must only see in-scope events.
        if until_event_id is not None:
            scoped_events = tuple(
                event for event in events if event.id <= until_event_id
            )
        else:
            scoped_events = events
        builder = self._session_context_factory(scoped_events)
        return builder.build_sections(
            until_event_id=until_event_id,
            include_attachments=include_attachments,
        )

    async def assemble_turn(
        self,
        intent: ContextAssemblyIntent,
        request: TurnAssemblyRequest,
    ) -> AssemblyResult:
        if intent not in {
            ContextAssemblyIntent.ANCHOR_TURN,
            ContextAssemblyIntent.CONTINUATION_TURN,
        }:
            raise ValueError(f"assemble_turn does not accept intent {intent!r}")

        composition = _INTENT_COMPOSITION_MAP[intent]
        session_sections: tuple[ContextSection, ...] = ()

        if intent == ContextAssemblyIntent.ANCHOR_TURN:
            history_boundary = request.turn_input.pre_turn_history_event_id
            events, jobs = await asyncio.gather(
                self._ports.session_events.load_events(
                    SessionEventQuery(
                        session_id=request.session_id,
                        spawn_id=request.spawn_id,
                        until_event_id=history_boundary,
                        order="asc",
                    )
                ),
                self._load_jobs_or_empty(request.session_id),
            )
            session_sections = self._session_section_builder(
                events,
                history_boundary,
                True,
            )
        else:
            jobs = await self._load_jobs_or_empty(request.session_id)

        user_turn_context = composition.apply(
            ContextCompositionInputs(
                user_instructions_text=request.user_instructions.text,
                turn_input=request.turn_input,
                session_sections=session_sections,
                session_jobs=jobs,
                split_turn_attachments=self._render_options.split_turn_attachments,
            )
        )
        return AssemblyResult(
            user_turn_context=user_turn_context,
            user_instructions_text=request.user_instructions.text,
            user_instructions_hash=request.user_instructions.hash,
            used_composition=composition.name,
        )

    async def assemble_compaction(
        self,
        intent: ContextAssemblyIntent,
        request: CompactionAssemblyRequest,
    ) -> AssemblyResult:
        if not intent.is_compaction:
            raise ValueError(f"assemble_compaction does not accept intent {intent!r}")

        covered_until = _resolve_covered_until(intent, request)

        events, jobs = await asyncio.gather(
            self._ports.session_events.load_events(
                SessionEventQuery(
                    session_id=request.session_id,
                    spawn_id=request.spawn_id,
                    until_event_id=covered_until,
                    order="asc",
                )
            ),
            self._load_jobs_or_empty(request.session_id),
        )
        session_sections = self._session_section_builder(
            events,
            covered_until,
            request.session_attachments_override is None,
        )
        composition = _INTENT_COMPOSITION_MAP[intent]
        user_turn_context = composition.apply(
            ContextCompositionInputs(
                user_instructions_text=request.user_instructions.text,
                compacted_history_summary=request.compacted_history_summary,
                turn_input=request.turn_input,
                session_sections=session_sections,
                session_jobs=jobs,
                session_attachments_override=request.session_attachments_override,
                defer_turn_instruction=True,
                split_turn_attachments=self._render_options.split_turn_attachments,
            )
        )
        return AssemblyResult(
            user_turn_context=user_turn_context,
            user_instructions_text=request.user_instructions.text,
            user_instructions_hash=request.user_instructions.hash,
            used_composition=composition.name,
            covered_until_event_id=covered_until,
        )

    async def _load_jobs_or_empty(self, session_id: str) -> SessionJobs:
        if self._ports.session_jobs is None:
            return SessionJobs.empty()
        return await self._ports.session_jobs.load_session_jobs(
            SessionJobsQuery(session_id=session_id)
        )
