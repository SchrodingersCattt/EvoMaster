from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlparse

from matmaster.context.sections import RUNTIME_ONLY_VIEWS, ContextSection, SectionOrder
from matmaster.types.messages import ImageContentPart


def _clean_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        text for value in values if isinstance(value, str) and (text := value.strip())
    )


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
                views=RUNTIME_ONLY_VIEWS,
            ),
        )


@dataclass(frozen=True)
class TurnAttachmentsSource:
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    image_detail: Literal["low", "high", "auto"] | None = None
    workspace_paths: tuple[str, ...] = ()

    def to_lines(self) -> tuple[str, ...]:
        lines = [
            *(f"file_{i} {_display_name(v)} {v}" for i, v in enumerate(self.files, 1)),
            *(f"workspace_{i} {v}" for i, v in enumerate(self.workspace_paths, 1)),
            *(
                f"image_{i} {_display_name(v)} {v}"
                for i, v in enumerate(self.images, 1)
            ),
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
                views=RUNTIME_ONLY_VIEWS,
            ),
        )

    def images_as_parts(self) -> tuple[ImageContentPart, ...]:
        return tuple(
            ImageContentPart(url=url, detail=self.image_detail) for url in self.images
        )


@dataclass(frozen=True)
class TurnInput:
    instruction: TurnInstructionSource = field(default_factory=TurnInstructionSource)
    attachments: TurnAttachmentsSource = field(default_factory=TurnAttachmentsSource)
    pre_turn_history_event_id: int = 0

    def __post_init__(self) -> None:
        if self.pre_turn_history_event_id < 0:
            raise ValueError("pre_turn_history_event_id must be >= 0")

    @classmethod
    def from_values(
        cls,
        *,
        user_text: str | None = None,
        files: Any = None,
        images: Any = None,
        image_detail: Literal["low", "high", "auto"] | None = None,
        workspace_paths: Any = None,
        pre_turn_history_event_id: int | None = 0,
    ) -> TurnInput:
        return cls(
            instruction=TurnInstructionSource(user_text=(user_text or "").strip()),
            attachments=TurnAttachmentsSource(
                files=_clean_tuple(files),
                images=_clean_tuple(images),
                image_detail=image_detail,
                workspace_paths=_clean_tuple(workspace_paths),
            ),
            pre_turn_history_event_id=int(pre_turn_history_event_id or 0),
        )

    @classmethod
    def from_payload(cls, payload: Any) -> TurnInput | None:
        if not isinstance(payload, dict):
            return None
        raw_boundary = payload.get(
            "pre_turn_history_event_id",
            payload.get("pre_query_scope_event_id", 0),
        )
        try:
            boundary = int(raw_boundary or 0)
        except (TypeError, ValueError):
            boundary = 0
        return cls.from_values(
            user_text=payload.get("user_text"),
            files=payload.get("files"),
            images=payload.get("images"),
            image_detail=payload.get("image_detail"),
            workspace_paths=payload.get("workspace_paths"),
            pre_turn_history_event_id=boundary,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_text": self.user_text,
            "files": list(self.files),
            "images": list(self.images),
            "image_detail": self.attachments.image_detail,
            "workspace_paths": list(self.workspace_paths),
            "pre_turn_history_event_id": self.pre_turn_history_event_id,
        }

    @property
    def user_text(self) -> str:
        return self.instruction.user_text

    @property
    def files(self) -> tuple[str, ...]:
        return self.attachments.files

    @property
    def images(self) -> tuple[str, ...]:
        return self.attachments.images

    @property
    def workspace_paths(self) -> tuple[str, ...]:
        return self.attachments.workspace_paths

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

    def with_deferred_instruction(self) -> TurnInput:
        return dataclasses.replace(
            self,
            instruction=dataclasses.replace(self.instruction, deferred=True),
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
