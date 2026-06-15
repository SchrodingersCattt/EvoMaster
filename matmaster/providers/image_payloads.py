"""Provider-side helpers for inlining remote image URLs."""

from __future__ import annotations

import base64
import ipaddress
from urllib.parse import urljoin, urlparse

import httpx

_ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
_MAX_URL_LENGTH = 4096
_MAX_RAW_BYTES_FOR_BASE64 = 3_750_000
_MAX_REDIRECTS = 3
_TIMEOUT_SECONDS = 5.0
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class ImagePayloadError(ValueError):
    """Raised when a remote image cannot be safely inlined for a provider."""


def _is_ip_address_blocked(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _validate_url(url: str) -> None:
    if len(url) > _MAX_URL_LENGTH:
        raise ImagePayloadError("image URL is too long")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise ImagePayloadError("image URL must be HTTPS")
    if _is_ip_address_blocked(host):
        raise ImagePayloadError("image URL must not point to private addresses")


def _mime_from_magic(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _get_with_validated_redirects(client: httpx.Client, url: str) -> httpx.Response:
    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        _validate_url(current_url)
        try:
            response = client.get(current_url)
        except (httpx.HTTPError, OSError) as exc:
            raise ImagePayloadError("image URL is not reachable") from exc

        final_url = str(response.url)
        _validate_url(final_url)
        if response.status_code not in _REDIRECT_STATUS_CODES:
            return response

        location = response.headers.get("location")
        if not location:
            return response
        current_url = urljoin(final_url, location)

    raise ImagePayloadError("image URL redirects too many times")


def inline_image_url_as_base64(url: str) -> tuple[str, str]:
    """Download a remote image URL and return ``(media_type, base64_data)``.

    This is intentionally provider-side: canonical history keeps compact URLs,
    while Bedrock-style Anthropic endpoints receive their required base64 source.
    """

    _validate_url(url)
    with httpx.Client(
        timeout=httpx.Timeout(_TIMEOUT_SECONDS, connect=_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        response = _get_with_validated_redirects(client, url)

    if not response.is_success:
        raise ImagePayloadError("image URL is not reachable")

    size = _content_length(response)
    if size is None:
        raise ImagePayloadError("image size is unknown")
    if size > _MAX_RAW_BYTES_FOR_BASE64:
        raise ImagePayloadError("image is too large for Anthropic Bedrock base64")

    payload = response.content
    if len(payload) > _MAX_RAW_BYTES_FOR_BASE64:
        raise ImagePayloadError("image is too large for Anthropic Bedrock base64")
    if len(payload) != size:
        raise ImagePayloadError("image size does not match content-length")

    media_type = _mime_from_magic(payload)
    if media_type not in _ALLOWED_MIME_TYPES:
        raise ImagePayloadError("image format is not supported")

    data = base64.b64encode(payload).decode("ascii")
    return media_type, data
