"""Image payload resolution for ReadTool: magic sniffing, PNG dimension check, data URI.

Format set is the Anthropic and qwen-VL intersection (PNG/JPEG/WEBP, no GIF);
limits follow the design spec (3 MiB raw bytes, 8000px PNG edge).
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass

MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_PNG_EDGE_PX = 8000

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class ImageValidationError(ValueError):
    """Image fails size/dimension constraints; message is model-facing."""


@dataclass(frozen=True)
class ImagePayload:
    media_type: str
    data_uri: str
    raw_size: int
    width: int | None
    height: int | None


def sniff_image_media_type(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def png_dimensions(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 24 or raw[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", raw[16:24])
    return width, height


def build_image_payload(raw: bytes) -> ImagePayload:
    media_type = sniff_image_media_type(raw)
    if media_type is None:
        raise ValueError("not a supported image; sniff before calling")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageValidationError(
            f"image is {len(raw) / (1024 * 1024):.1f} MiB, exceeds the 3 MiB limit; "
            "compress it first (e.g. via Bash) and re-Read"
        )
    width: int | None = None
    height: int | None = None
    if media_type == "image/png":
        dims = png_dimensions(raw)
        if dims is None:
            raise ImageValidationError("corrupt PNG header (IHDR not found)")
        width, height = dims
        if max(width, height) > MAX_PNG_EDGE_PX:
            raise ImageValidationError(
                f"image is {width}x{height}px, exceeds the {MAX_PNG_EDGE_PX}px edge limit; "
                "downscale it first (e.g. via Bash) and re-Read"
            )
    encoded = base64.standard_b64encode(raw).decode("ascii")
    return ImagePayload(
        media_type=media_type,
        data_uri=f"data:{media_type};base64,{encoded}",
        raw_size=len(raw),
        width=width,
        height=height,
    )
