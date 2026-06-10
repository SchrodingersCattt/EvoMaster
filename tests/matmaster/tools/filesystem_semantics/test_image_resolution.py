"""Tests for matmaster/tools/filesystem_semantics/image_resolution.py."""

import struct
import zlib

import pytest

from matmaster.tools.filesystem_semantics.image_resolution import (
    MAX_IMAGE_BYTES,
    ImagePayload,
    ImageValidationError,
    build_image_payload,
    png_dimensions,
    sniff_image_media_type,
)


def _png_chunk(typ: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + typ
        + data
        + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x00\x00\x00" * width
    idat = zlib.compress(row * height)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def make_png_header_only(width: int, height: int) -> bytes:
    """合成仅含签名+IHDR 的字节，用于超大尺寸校验（无需真实像素数据）。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)


def test_sniff_media_types() -> None:
    assert sniff_image_media_type(make_png(2, 2)) == "image/png"
    assert sniff_image_media_type(b"\xff\xd8\xff\xe0" + b"\x00" * 16) == "image/jpeg"
    assert sniff_image_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_image_media_type(b"GIF89a" + b"\x00" * 10) is None
    assert sniff_image_media_type(b"plain text") is None


def test_png_dimensions() -> None:
    assert png_dimensions(make_png(3, 5)) == (3, 5)
    assert png_dimensions(b"\x89PNG\r\n\x1a\n short") is None


def test_build_payload_success() -> None:
    raw = make_png(2, 2)
    payload = build_image_payload(raw)
    assert isinstance(payload, ImagePayload)
    assert payload.media_type == "image/png"
    assert payload.data_uri.startswith("data:image/png;base64,")
    assert payload.raw_size == len(raw)
    assert (payload.width, payload.height) == (2, 2)


def test_build_payload_jpeg_has_no_dimensions() -> None:
    payload = build_image_payload(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    assert payload.media_type == "image/jpeg"
    assert payload.width is None and payload.height is None


def test_build_payload_rejects_oversize_bytes() -> None:
    raw = b"\xff\xd8\xff" + b"\x00" * (MAX_IMAGE_BYTES + 1)
    with pytest.raises(ImageValidationError, match="3 MiB"):
        build_image_payload(raw)


def test_build_payload_rejects_oversize_png_dimensions() -> None:
    with pytest.raises(ImageValidationError, match="8000px"):
        build_image_payload(make_png_header_only(9000, 100))


def test_build_payload_rejects_non_image() -> None:
    with pytest.raises(ValueError):
        build_image_payload(b"plain text")
