from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlparse

from matmaster.context.sections import RUNTIME_ONLY_VIEWS, ContextSection, SectionOrder
from matmaster.types.messages import ImageContentPart

TurnInstructionTag = Literal["current-instruction", "system-reminder"]


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


def _clean_number_list(value: Any) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[float] = []
    for item in value[:3]:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            return ()
    return tuple(out)


def _format_coord(values: tuple[float, ...]) -> str:
    return "[" + ", ".join(f"{v:.6g}" for v in values) + "]"


def _clean_structure_selections(values: Any) -> tuple[dict[str, Any], ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        return ()

    selections: list[dict[str, Any]] = []
    for raw_selection in values:
        if not isinstance(raw_selection, dict):
            continue
        raw_atoms = raw_selection.get("atoms")
        if not isinstance(raw_atoms, (list, tuple)):
            continue
        atoms: list[dict[str, Any]] = []
        for raw_atom in raw_atoms:
            if not isinstance(raw_atom, dict):
                continue
            atom: dict[str, Any] = {}
            order = raw_atom.get("order")
            if isinstance(order, (str, int)):
                atom["order"] = order
            element = raw_atom.get("element")
            if isinstance(element, str) and element.strip():
                atom["element"] = element.strip()
            cart_coord = _clean_number_list(raw_atom.get("cart_coord"))
            if cart_coord:
                atom["cart_coord"] = list(cart_coord)
            frac_coord = _clean_number_list(raw_atom.get("frac_coord"))
            if frac_coord:
                atom["frac_coord"] = list(frac_coord)
            if atom:
                atoms.append(atom)
        if not atoms:
            continue

        selection: dict[str, Any] = {"atoms": atoms}
        for key in ("id", "source_path", "source_format"):
            value = raw_selection.get(key)
            if isinstance(value, str) and value.strip():
                selection[key] = value.strip()
        selections.append(selection)
    return tuple(selections)


@dataclass(frozen=True)
class TurnInstructionSource:
    user_text: str = ""
    deferred: bool = False
    tag: TurnInstructionTag = "current-instruction"

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
                key=self.tag,
                tag=self.tag,
                content=text,
                order=order,
                views=RUNTIME_ONLY_VIEWS,
            ),
        )


@dataclass(frozen=True)
class TurnStructureSelectionsSource:
    selections: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> list[dict[str, Any]]:
        return [
            {
                **{k: v for k, v in selection.items() if k != "atoms"},
                "atoms": [dict(atom) for atom in selection.get("atoms", [])],
            }
            for selection in self.selections
        ]

    def to_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        for index, selection in enumerate(self.selections, 1):
            atoms = selection.get("atoms", [])
            label = selection.get("source_path") or "structure"
            lines.append(f"selection_{index}: {label}")
            if selection.get("source_path"):
                lines.append(f"source_path: {selection['source_path']}")
            if selection.get("source_format"):
                lines.append(f"source_format: {selection['source_format']}")
            lines.append(f"atom_count: {len(atoms)}")
            lines.append("atoms:")
            for atom in atoms:
                parts: list[str] = []
                if atom.get("order") is not None:
                    parts.append(f"order={atom['order']}")
                if atom.get("element"):
                    parts.append(f"element={atom['element']}")
                cart_coord = _clean_number_list(atom.get("cart_coord"))
                if cart_coord:
                    parts.append(f"cart_coord_angstrom={_format_coord(cart_coord)}")
                frac_coord = _clean_number_list(atom.get("frac_coord"))
                if frac_coord:
                    parts.append(f"frac_coord={_format_coord(frac_coord)}")
                if parts:
                    lines.append("- " + " ".join(parts))
        return tuple(lines)

    def to_sections(self) -> tuple[ContextSection, ...]:
        lines = self.to_lines()
        if not lines:
            return ()
        return (
            ContextSection(
                key="structure-selections",
                tag="structure-selections",
                content="\n".join(lines),
                order=SectionOrder.TURN_STRUCTURE_SELECTIONS,
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
                key="turn-attachments",
                tag="turn-attachments",
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
    structure_selections: TurnStructureSelectionsSource = field(default_factory=TurnStructureSelectionsSource)
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
        structure_selections: Any = None,
        pre_turn_history_event_id: int | None = 0,
        instruction_tag: TurnInstructionTag = "current-instruction",
    ) -> TurnInput:
        return cls(
            instruction=TurnInstructionSource(
                user_text=(user_text or "").strip(),
                tag=instruction_tag,
            ),
            structure_selections=TurnStructureSelectionsSource(
                selections=_clean_structure_selections(structure_selections),
            ),
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
        raw_boundary = payload.get("pre_turn_history_event_id", 0)
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
            structure_selections=payload.get("structure_selections"),
            pre_turn_history_event_id=boundary,
            instruction_tag=payload.get("instruction_tag", "current-instruction"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_text": self.user_text,
            "instruction_tag": self.instruction.tag,
            "files": list(self.files),
            "images": list(self.images),
            "image_detail": self.attachments.image_detail,
            "workspace_paths": list(self.workspace_paths),
            "structure_selections": self.structure_selections.to_payload(),
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
                *self.structure_selections.to_sections(),
                *self.attachments.to_sections(),
            )

        merged = self._merged_current_instruction_text()
        instruction_sections = TurnInstructionSource(
            user_text=merged,
            deferred=self.instruction.deferred,
            tag=self.instruction.tag,
        ).to_sections()
        return (*instruction_sections, *self.structure_selections.to_sections())

    def has_effective_input(self) -> bool:
        return bool(
            self.instruction.user_text.strip()
            or self.structure_selections.selections
            or self.attachments.files
            or self.attachments.images
            or self.attachments.workspace_paths
        )

    def with_deferred_instruction(self) -> TurnInput:
        return dataclasses.replace(
            self,
            instruction=dataclasses.replace(self.instruction, deferred=True),
        )

    def instruction_only(self) -> TurnInput:
        return dataclasses.replace(
            self,
            structure_selections=TurnStructureSelectionsSource(),
            attachments=TurnAttachmentsSource(),
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
