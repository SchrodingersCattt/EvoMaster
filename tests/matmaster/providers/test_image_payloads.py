from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from matmaster.providers.image_payloads import (
    ImagePayloadError,
    inline_image_url_as_base64,
)


def _response(
    status_code: int,
    *,
    method: str = "GET",
    url: str = "https://oss.example.com/chat/a.png",
    headers: dict[str, str] | None = None,
    content: bytes = b"",
) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers=headers or {},
        content=content,
        request=httpx.Request(method, url),
    )


def test_inline_image_url_as_base64_downloads_valid_png() -> None:
    client = MagicMock()
    client.get.return_value = _response(
        200,
        headers={"content-length": "13"},
        content=b"\x89PNG\r\n\x1a\nabcde",
    )

    with patch("matmaster.providers.image_payloads.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        media_type, data = inline_image_url_as_base64(
            "https://oss.example.com/chat/a.png"
        )

    assert media_type == "image/png"
    assert data == "iVBORw0KGgphYmNkZQ=="
    assert client_cls.call_args.kwargs["follow_redirects"] is False


def test_inline_image_url_rejects_private_redirect() -> None:
    client = MagicMock()
    client.get.return_value = _response(
        302,
        headers={"location": "https://127.0.0.1/admin/a.png"},
    )

    with patch("matmaster.providers.image_payloads.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImagePayloadError, match="private"):
            inline_image_url_as_base64("https://oss.example.com/chat/a.png")


def test_inline_image_url_rejects_http_scheme() -> None:
    with pytest.raises(ImagePayloadError, match="HTTPS"):
        inline_image_url_as_base64("http://oss.example.com/chat/a.png")


def test_inline_image_url_rejects_unknown_size() -> None:
    client = MagicMock()
    client.get.return_value = _response(
        200,
        headers={"content-length": ""},
        content=b"\x89PNG\r\n\x1a\nabcde",
    )

    with patch("matmaster.providers.image_payloads.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImagePayloadError, match="size"):
            inline_image_url_as_base64("https://oss.example.com/chat/a.png")


def test_inline_image_url_rejects_too_large_image() -> None:
    client = MagicMock()
    client.get.return_value = _response(
        200,
        headers={"content-length": str(3_750_001)},
        content=b"\x89PNG\r\n\x1a\nabcde",
    )

    with patch("matmaster.providers.image_payloads.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImagePayloadError, match="large"):
            inline_image_url_as_base64("https://oss.example.com/chat/a.png")


def test_inline_image_url_rejects_unsupported_mime() -> None:
    client = MagicMock()
    client.get.return_value = _response(
        200,
        headers={"content-length": "11"},
        content=b"hello world",
    )

    with patch("matmaster.providers.image_payloads.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImagePayloadError, match="format"):
            inline_image_url_as_base64("https://oss.example.com/chat/a.txt")
