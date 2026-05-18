from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class ContextView(str, Enum):
    RUNTIME = "runtime"
    CHECKPOINT = "checkpoint"


ALL_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
RUNTIME_ONLY_VIEWS = frozenset({ContextView.RUNTIME})


class SectionOrder(IntEnum):
    USER_INSTRUCTIONS = 10
    COMPACTED_HISTORY = 100
    SESSION_SKILLS = 300
    SESSION_TOOLS = 400
    SESSION_ATTACHMENTS = 500
    SESSION_WORKSPACE = 600
    SESSION_ARTIFACTS = 700
    TURN_INSTRUCTION = 1000
    TURN_ATTACHMENTS = 1100
    SESSION_JOBS = 1200
    TURN_INSTRUCTION_LAST = 1300


@dataclass(frozen=True)
class ContextSection:
    key: str
    tag: str
    content: str
    order: int
    views: frozenset[ContextView]

    def __post_init__(self) -> None:
        if ContextView.CHECKPOINT in self.views and ContextView.RUNTIME not in self.views:
            raise ValueError(
                f"Section {self.key!r}: CHECKPOINT view requires RUNTIME view "
                "(invariant RUNTIME >= CHECKPOINT)"
            )
        if not self.key:
            raise ValueError("ContextSection.key must be non-empty")
        if not self.tag:
            raise ValueError("ContextSection.tag must be non-empty")


def single_section_or_empty(
    *,
    key: str,
    tag: str,
    content: str,
    order: int,
    views: frozenset[ContextView] = ALL_VIEWS,
) -> tuple[ContextSection, ...]:
    """Return a single-element tuple if content has non-blank text, else ()."""
    if not content.strip():
        return ()
    return (
        ContextSection(
            key=key,
            tag=tag,
            content=content,
            order=order,
            views=views,
        ),
    )
