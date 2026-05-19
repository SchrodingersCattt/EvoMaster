from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from matmaster.context.rendering import render_sections
from matmaster.context.sections import ContextSection, ContextView
from matmaster.types.messages import ImageContentPart, UserMessage


@dataclass(frozen=True)
class UserTurnContext:
    sections: tuple[ContextSection, ...]
    images: tuple[ImageContentPart, ...] = ()
    checkpoint_images: tuple[ImageContentPart, ...] = ()

    @classmethod
    def from_sources(
        cls,
        *section_groups: Iterable[ContextSection],
        images: Iterable[ImageContentPart] = (),
        checkpoint_images: Iterable[ImageContentPart] = (),
    ) -> UserTurnContext:
        merged: list[ContextSection] = []
        seen_keys: set[str] = set()
        for group in section_groups:
            for section in group:
                if section.key in seen_keys:
                    raise ValueError(
                        f"Duplicate section key {section.key!r} in UserTurnContext "
                        "sources. Keys must be unique across all sources."
                    )
                seen_keys.add(section.key)
                merged.append(section)
        return cls(
            sections=tuple(merged),
            images=tuple(images),
            checkpoint_images=tuple(checkpoint_images),
        )

    def render(self, view: ContextView) -> str:
        return render_sections(self.sections, view=view)

    def to_message(self, view: ContextView) -> UserMessage:
        return UserMessage(
            content=self.render(view),
            images=list(self._images_for_view(view)),
        )

    def _images_for_view(self, view: ContextView) -> tuple[ImageContentPart, ...]:
        if view == ContextView.CHECKPOINT:
            return self.checkpoint_images
        return (*self.checkpoint_images, *self.images)
