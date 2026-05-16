from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlparse

from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.types.messages import ImageContentPart

_RUNTIME = frozenset({ContextView.RUNTIME})


def _display_name(value: str) -> str:
    parsed = urlparse(value)
    return PurePosixPath(parsed.path or value).name or value


@dataclass(frozen=True)
class TurnInstructionSource:
    user_text: str = ""
    deferred: bool = False

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = self.user_text.strip()
        if not text:
            return ()
        order = (
            SectionOrder.TURN_INSTRUCTION_LAST
            if self.deferred
            else SectionOrder.TURN_INSTRUCTION
        )
        return (
            ContextSection(
                key="current_instruction",
                tag="current_instruction",
                content=text,
                order=order,
                views=_RUNTIME,
            ),
        )


@dataclass(frozen=True)
class TurnAttachmentsSource:
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    workspace_paths: tuple[str, ...] = ()

    def to_lines(self) -> tuple[str, ...]:
        lines = [
            *(f"file_{i} {_display_name(v)} {v}" for i, v in enumerate(self.files, 1)),
            *(f"workspace_{i} {v}" for i, v in enumerate(self.workspace_paths, 1)),
            *(f"image_{i} {_display_name(v)} {v}" for i, v in enumerate(self.images, 1)),
        ]
        return tuple(lines)

    def to_sections(self) -> tuple[ContextSection, ...]:
        lines = self.to_lines()
        if not lines:
            return ()
        return (
            ContextSection(
                key="turn_attachments",
                tag="turn_attachments",
                content="\n".join(lines),
                order=SectionOrder.TURN_ATTACHMENTS,
                views=_RUNTIME,
            ),
        )

    def images_as_parts(self) -> tuple[ImageContentPart, ...]:
        return tuple(ImageContentPart(url=url) for url in self.images)


@dataclass(frozen=True)
class TurnInput:
    instruction: TurnInstructionSource = field(default_factory=TurnInstructionSource)
    attachments: TurnAttachmentsSource = field(default_factory=TurnAttachmentsSource)
    pre_turn_history_event_id: int = 0

    def to_sections(
        self,
        *,
        split_attachments: bool = False,
    ) -> tuple[ContextSection, ...]:
        if split_attachments:
            return (
                *self.instruction.to_sections(),
                *self.attachments.to_sections(),
            )

        merged = self._merged_current_instruction_text()
        if not merged.strip():
            return ()
        return TurnInstructionSource(
            user_text=merged,
            deferred=self.instruction.deferred,
        ).to_sections()

    def has_effective_input(self) -> bool:
        return bool(
            self.instruction.user_text.strip()
            or self.attachments.files
            or self.attachments.images
            or self.attachments.workspace_paths
        )

    def _merged_current_instruction_text(self) -> str:
        lines: list[str] = []
        user_text = self.instruction.user_text.strip()
        if user_text:
            lines.append(user_text)
        attachment_lines = self.attachments.to_lines()
        if attachment_lines:
            if lines:
                lines.append("")
            lines.append("[Current attachments]")
            lines.extend(attachment_lines)
        return "\n".join(lines).strip()
