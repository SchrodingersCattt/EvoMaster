from __future__ import annotations

import pytest

from matmaster.context.sections import ContextSection, ContextView, SectionOrder


def test_context_section_accepts_runtime_and_checkpoint_views() -> None:
    section = ContextSection(
        key="user-instructions",
        tag="user-instructions",
        content="Use SI units.",
        order=SectionOrder.USER_INSTRUCTIONS,
        views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
    )

    assert section.key == "user-instructions"
    assert section.order == SectionOrder.USER_INSTRUCTIONS


def test_context_section_rejects_checkpoint_without_runtime() -> None:
    with pytest.raises(ValueError, match="CHECKPOINT view requires RUNTIME"):
        ContextSection(
            key="broken",
            tag="broken",
            content="content",
            order=1,
            views=frozenset({ContextView.CHECKPOINT}),
        )


@pytest.mark.parametrize(
    ("key", "tag", "message"),
    [
        ("", "valid", "ContextSection.key must be non-empty"),
        ("valid", "", "ContextSection.tag must be non-empty"),
        ("valid", "bad_tag", "ContextSection.tag must use hyphen separators"),
    ],
)
def test_context_section_rejects_empty_key_or_tag(
    key: str,
    tag: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ContextSection(
            key=key,
            tag=tag,
            content="content",
            order=1,
            views=frozenset({ContextView.RUNTIME}),
        )
