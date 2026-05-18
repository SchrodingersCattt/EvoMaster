from __future__ import annotations

import logging
from collections.abc import Iterable

from matmaster.context.sections import ContextSection, ContextView

logger = logging.getLogger(__name__)


def _escape_close_tag(content: str, tag: str) -> str:
    close = f"</{tag}>"
    if close not in content:
        return content
    logger.warning(
        "rendering._escape_close_tag triggered: tag=%r content contains close "
        "form, escaping to avoid breaking section boundary",
        tag,
    )
    return content.replace(close, f"</ {tag}>")


def wrap_tag(tag: str, content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    text = _escape_close_tag(text, tag)
    return f"<{tag}>\n{text}\n</{tag}>"


def render_sections(
    sections: Iterable[ContextSection],
    *,
    view: ContextView,
    separator: str = "\n\n",
) -> str:
    visible = [section for section in sections if view in section.views]
    visible = [section for section in visible if section.content.strip()]
    visible.sort(key=lambda section: section.order)
    return separator.join(wrap_tag(section.tag, section.content) for section in visible)
