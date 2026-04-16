"""Figure type contracts for chat response image metadata."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FigureManifestEntry(BaseModel):
    """Manifest entry describing a locally generated figure."""

    model_config = ConfigDict(extra="forbid")

    figure_id: str
    path: str
    caption: str
    alt: str | None = None
    importance: Literal["primary", "secondary"] = "secondary"
    placement_hint: Literal["sidebar_only", "appendix_candidate"] = "sidebar_only"


class FigureDescriptor(BaseModel):
    """Public figure metadata emitted to clients."""

    model_config = ConfigDict(extra="forbid")

    figure_id: str
    asset_url: str
    caption: str
    alt: str | None = None
    importance: Literal["primary", "secondary"] = "secondary"
    placement_hint: Literal["sidebar_only", "appendix_candidate"] = "sidebar_only"
    source_tool_call_id: str | None = None


class FigureUploadConfig(BaseModel):
    """Runtime upload contract for turning figure bytes into hosted assets."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    task_id: str
    asset_key_prefix: str
    upload_bytes: Callable[[bytes, str], str]
