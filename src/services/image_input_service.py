from __future__ import annotations

import ipaddress
import logging
import os
import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlparse

import httpx

from matmaster.config.llm import LLMConfig, LLMProfileConfig
from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.messages import ImageContentPart, Message, UserMessage

IMAGE_INPUT_TOO_MANY = "IMAGE_INPUT_TOO_MANY"
IMAGE_INPUT_URL_TOO_LONG = "IMAGE_INPUT_URL_TOO_LONG"
IMAGE_INPUT_INVALID_SCHEME = "IMAGE_INPUT_INVALID_SCHEME"
# Host allowlist removed; constant kept because the private/loopback IP block
# (the remaining SSRF guard) still raises it.
IMAGE_INPUT_DOMAIN_BLOCKED = "IMAGE_INPUT_DOMAIN_BLOCKED"
IMAGE_INPUT_DUPLICATE_ATTACHMENT = "IMAGE_INPUT_DUPLICATE_ATTACHMENT"
IMAGE_INPUT_UNREACHABLE = "IMAGE_INPUT_UNREACHABLE"
IMAGE_INPUT_UNSUPPORTED_MIME = "IMAGE_INPUT_UNSUPPORTED_MIME"
IMAGE_INPUT_SIZE_UNKNOWN = "IMAGE_INPUT_SIZE_UNKNOWN"
IMAGE_INPUT_TOO_LARGE = "IMAGE_INPUT_TOO_LARGE"
VISION_MODEL_NOT_SUPPORTED = "VISION_MODEL_NOT_SUPPORTED"

logger = logging.getLogger(__name__)
ImageDetail = Literal["low", "high", "auto"]

_ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
_RANGE_HEADER = {"Range": "bytes=0-4095"}
_CONTENT_RANGE_SIZE_RE = re.compile(r"/(\d+|\*)\s*$")
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class ImageInputError(Exception):
    def __init__(
        self,
        error_code: str,
        message: str,
        http_status: int = 422,
    ) -> None:
        super().__init__(error_code, message, http_status)
        self.error_code = error_code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class ImageInputSettings:
    """Image input runtime knobs.

    The only URL gate is HTTPS plus the private/loopback IP block (see
    `_is_ip_address_blocked`). Dev environments may opt in to HTTP for specific
    hosts via `IMAGE_INPUT_ALLOW_INSECURE_HOSTS`; this is force-cleared in
    production.
    """

    allow_insecure_hosts: frozenset[str] = frozenset()
    max_images: int = 5
    max_url_length: int = 4096
    max_bytes: int = 5 * 1024 * 1024
    per_image_timeout_seconds: float = 3.0
    max_redirects: int = 3


@dataclass(frozen=True)
class ValidatedImageInput:
    url: str
    mime_type: str
    size_bytes: int


def _split_env_csv(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _is_production_env() -> bool:
    return (os.environ.get("SERVICE_ENV", "test") or "").strip().lower() in {
        "prod",
        "production",
    }


def _settings_from_env() -> ImageInputSettings:
    allow_insecure_hosts = frozenset(
        host.lower() for host in _split_env_csv("IMAGE_INPUT_ALLOW_INSECURE_HOSTS")
    )
    if _is_production_env():
        allow_insecure_hosts = frozenset()
    return ImageInputSettings(allow_insecure_hosts=allow_insecure_hosts)


def _header_mime(headers: httpx.Headers) -> str | None:
    content_type = headers.get("content-type", "")
    mime_type = content_type.split(";", 1)[0].strip().lower()
    return mime_type or None


def _header_size(headers: httpx.Headers, *, trust_content_length: bool) -> int | None:
    content_range = headers.get("content-range", "")
    match = _CONTENT_RANGE_SIZE_RE.search(content_range)
    if match and match.group(1) != "*":
        return int(match.group(1))
    if trust_content_length:
        content_length = headers.get("content-length")
        if content_length:
            try:
                return int(content_length)
            except ValueError:
                return None
    return None


def _mime_from_magic(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _is_ip_address_blocked(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


class ImageInputService:
    def __init__(self, settings: ImageInputSettings | None = None) -> None:
        self.settings = settings or _settings_from_env()

    def validate_current_images(
        self,
        *,
        files: list[str] | None,
        images: list[str] | None,
    ) -> list[ValidatedImageInput]:
        deduped_images = self._dedupe_images(images or [])
        if not deduped_images:
            return []
        if len(deduped_images) > self.settings.max_images:
            raise ImageInputError(
                IMAGE_INPUT_TOO_MANY,
                f"图片数量不能超过 {self.settings.max_images} 张",
            )

        file_urls = set(files or [])
        if file_urls.intersection(deduped_images):
            raise ImageInputError(
                IMAGE_INPUT_DUPLICATE_ATTACHMENT,
                "同一 URL 不能同时作为普通附件和图片输入",
            )

        for url in deduped_images:
            self._validate_url(url)

        with httpx.Client(
            timeout=httpx.Timeout(
                self.settings.per_image_timeout_seconds,
                connect=self.settings.per_image_timeout_seconds,
            ),
            follow_redirects=False,
        ) as client:
            return [self._probe_image(client, url) for url in deduped_images]

    def validate_history_image_url(self, url: str) -> bool:
        try:
            self._validate_url(url)
        except ImageInputError:
            return False
        return True

    def ensure_vision_supported(
        self,
        *,
        llm_config: LLMConfig,
        model_override: str | None,
        default_profile_key: str | None,
    ) -> LLMProfileConfig:
        resolved = llm_config.resolve(
            model_override=model_override,
            default_key=default_profile_key,
        )
        profile = resolved.profile
        if not profile.supports_vision:
            raise ImageInputError(
                VISION_MODEL_NOT_SUPPORTED,
                "当前模型不支持图片输入，请切换到支持图片的模型后重试。",
            )
        return profile

    @staticmethod
    def select_current_images(
        turn_input: TurnInput | None,
        top_level_images: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Turn-input images take precedence over top-level images."""
        turn_input_images = turn_input.images if turn_input is not None else ()
        return turn_input_images or top_level_images

    def resolve_image_detail(
        self,
        *,
        llm_config: LLMConfig,
        images: tuple[str, ...],
        model_override: str | None,
        default_profile_key: str | None,
    ) -> ImageDetail | None:
        if not images:
            return None
        profile = self.ensure_vision_supported(
            llm_config=llm_config,
            model_override=model_override,
            default_profile_key=default_profile_key,
        )
        return profile.vision_detail

    def enrich_turn_input_images(
        self,
        *,
        turn_input: TurnInput | None,
        user_prompt: str,
        top_level_images: tuple[str, ...],
        image_detail: ImageDetail | None,
    ) -> TurnInput:
        turn_input_images = turn_input.images if turn_input is not None else ()
        current_images = self.select_current_images(turn_input, top_level_images)
        if (
            turn_input_images
            and top_level_images
            and turn_input_images != top_level_images
        ):
            logger.warning("run_agent image inputs differ; using TurnInput images")

        if turn_input is None:
            return TurnInput.from_values(
                user_text=user_prompt,
                files=(),
                images=current_images,
                image_detail=image_detail if current_images else None,
                workspace_paths=(),
                pre_turn_history_event_id=0,
            )

        if not current_images:
            return turn_input

        return TurnInput.from_values(
            user_text=turn_input.user_text,
            files=turn_input.files,
            images=current_images,
            image_detail=(
                image_detail
                if image_detail is not None
                else turn_input.attachments.image_detail
            ),
            workspace_paths=turn_input.workspace_paths,
            pre_turn_history_event_id=turn_input.pre_turn_history_event_id,
        )

    def _dedupe_images(self, images: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for image in images:
            if image in seen:
                continue
            seen.add(image)
            deduped.append(image)
        return deduped

    def _validate_url(self, url: str) -> None:
        if len(url) > self.settings.max_url_length:
            raise ImageInputError(IMAGE_INPUT_URL_TOO_LONG, "图片 URL 过长")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not parsed.scheme or not host:
            raise ImageInputError(IMAGE_INPUT_INVALID_SCHEME, "图片 URL 不合法")
        if parsed.scheme == "http":
            if host not in self.settings.allow_insecure_hosts:
                raise ImageInputError(
                    IMAGE_INPUT_INVALID_SCHEME,
                    "图片 URL 必须使用 HTTPS",
                )
        elif parsed.scheme != "https":
            raise ImageInputError(
                IMAGE_INPUT_INVALID_SCHEME,
                "图片 URL 必须使用 HTTPS",
            )
        if (
            _is_ip_address_blocked(host)
            and host not in self.settings.allow_insecure_hosts
        ):
            raise ImageInputError(
                IMAGE_INPUT_DOMAIN_BLOCKED,
                "图片 URL 不允许指向内网地址",
            )

    def _send_probe_request(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        kwargs: dict[str, Any] = {}
        if headers is not None:
            kwargs["headers"] = headers
        if method == "HEAD":
            return client.head(url, **kwargs)
        return client.get(url, **kwargs)

    def _request_with_validated_redirects(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        current_url = url
        for _ in range(self.settings.max_redirects + 1):
            self._validate_url(current_url)
            response = self._send_probe_request(
                client,
                method,
                current_url,
                headers=headers,
            )
            final_url = str(response.url)
            self._validate_url(final_url)
            if response.status_code not in _REDIRECT_STATUS_CODES:
                return response

            location = response.headers.get("location")
            if not location:
                return response
            current_url = urljoin(final_url, location)

        raise ImageInputError(
            IMAGE_INPUT_UNREACHABLE,
            "图片 URL 重定向次数过多",
        )

    def _probe_image(self, client: httpx.Client, url: str) -> ValidatedImageInput:
        try:
            head_response = self._request_with_validated_redirects(
                client,
                "HEAD",
                url,
            )
        except (httpx.HTTPError, OSError):
            head_response = None
        if head_response is not None and head_response.is_success:
            mime_type = _header_mime(head_response.headers)
            size = _header_size(head_response.headers, trust_content_length=True)
            if mime_type in _ALLOWED_MIME_TYPES and size is not None:
                return self._validated(url, mime_type, size)

        try:
            range_response = self._request_with_validated_redirects(
                client,
                "GET",
                url,
                headers=_RANGE_HEADER,
            )
        except (httpx.HTTPError, OSError) as exc:
            raise ImageInputError(
                IMAGE_INPUT_UNREACHABLE,
                "图片 URL 不可访问",
            ) from exc
        if not range_response.is_success:
            raise ImageInputError(
                IMAGE_INPUT_UNREACHABLE,
                "图片 URL 不可访问",
            )

        mime_type = _mime_from_magic(range_response.content)
        if mime_type not in _ALLOWED_MIME_TYPES:
            raise ImageInputError(
                IMAGE_INPUT_UNSUPPORTED_MIME,
                "图片格式仅支持 PNG、JPEG、WebP",
            )
        size = _header_size(range_response.headers, trust_content_length=False)
        if size is None:
            raise ImageInputError(
                IMAGE_INPUT_SIZE_UNKNOWN,
                "无法确认图片大小",
            )
        return self._validated(url, mime_type, size)

    def _validated(
        self,
        url: str,
        mime_type: str,
        size: int,
    ) -> ValidatedImageInput:
        if mime_type not in _ALLOWED_MIME_TYPES:
            raise ImageInputError(
                IMAGE_INPUT_UNSUPPORTED_MIME,
                "图片格式仅支持 PNG、JPEG、WebP",
            )
        if size > self.settings.max_bytes:
            raise ImageInputError(
                IMAGE_INPUT_TOO_LARGE,
                f"图片大小不能超过 {self.settings.max_bytes} 字节",
            )
        return ValidatedImageInput(url=url, mime_type=mime_type, size_bytes=size)


@lru_cache
def get_image_input_service() -> ImageInputService:
    return ImageInputService()


def _history_int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _image_name(url: str) -> str:
    path = unquote(urlparse(url).path)
    return posixpath.basename(path) or "image"


def _append_image_placeholders(
    content: str | None,
    images: list[ImageContentPart],
    *,
    reason: str,
) -> str:
    text = content or ""
    placeholders = [f"[历史图片{reason}: {_image_name(image.url)}]" for image in images]
    if not placeholders:
        return text
    return "\n".join([item for item in [text, *placeholders] if item])


def trim_history_images(
    messages: list[Message],
    *,
    last_k_turns: int | None = None,
    max_images: int | None = None,
    image_service: ImageInputService | None = None,
) -> list[Message]:
    allowed_turns = (
        _history_int_env(
            "IMAGE_INPUT_HISTORY_LAST_K_TURNS",
            3,
        )
        if last_k_turns is None
        else last_k_turns
    )
    allowed_images = (
        _history_int_env(
            "IMAGE_INPUT_HISTORY_MAX_IMAGES",
            10,
        )
        if max_images is None
        else max_images
    )
    service = image_service or get_image_input_service()

    remaining_turns = max(allowed_turns, 0)
    remaining_images = max(allowed_images, 0)
    output = list(messages)

    for idx in range(len(output) - 1, -1, -1):
        message = output[idx]
        if not isinstance(message, UserMessage) or not message.images:
            continue

        original_images = list(message.images)
        keep_images: list[ImageContentPart] = []
        pruned_images: list[ImageContentPart] = []

        if remaining_turns > 0 and remaining_images > 0:
            remaining_turns -= 1
            for image in original_images:
                if remaining_images <= 0:
                    pruned_images.append(image)
                    continue
                if service.validate_history_image_url(image.url):
                    keep_images.append(image)
                    remaining_images -= 1
                else:
                    pruned_images.append(image)
        else:
            pruned_images = original_images

        if pruned_images:
            output[idx] = message.model_copy(
                update={
                    "content": _append_image_placeholders(
                        message.content,
                        pruned_images,
                        reason="已裁剪",
                    ),
                    "images": keep_images,
                }
            )
        elif len(keep_images) != len(original_images):
            output[idx] = message.model_copy(update={"images": keep_images})

    return output


_NON_VISION_PLACEHOLDER = "[历史图片已移除：当前模型不支持图片输入]"


def strip_all_history_images(messages: list[Message]) -> list[Message]:
    """Strip all history images when the target model has no vision capability."""
    out = list(messages)
    for idx, message in enumerate(out):
        images = getattr(message, "images", None)
        if not images:
            continue
        text = message.content or ""
        placeholders = "\n".join(_NON_VISION_PLACEHOLDER for _ in images)
        out[idx] = message.model_copy(
            update={
                "content": "\n".join(item for item in (text, placeholders) if item),
                "images": [],
            }
        )
    return out
