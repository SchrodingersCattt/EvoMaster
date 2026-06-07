from unittest.mock import MagicMock, patch

import httpx
import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig, ProviderConfig
from matmaster.context.sources.turn_input import TurnInput
from src.services.image_input_service import (
    IMAGE_INPUT_DOMAIN_BLOCKED,
    IMAGE_INPUT_DUPLICATE_ATTACHMENT,
    IMAGE_INPUT_INVALID_SCHEME,
    IMAGE_INPUT_SIZE_UNKNOWN,
    IMAGE_INPUT_TOO_LARGE,
    IMAGE_INPUT_UNSUPPORTED_MIME,
    VISION_MODEL_NOT_SUPPORTED,
    ImageInputError,
    ImageInputService,
    ImageInputSettings,
)


def _service() -> ImageInputService:
    return ImageInputService(ImageInputSettings())


def _llm_config(profiles: dict[str, LLMProfileConfig], default: str) -> LLMConfig:
    return LLMConfig(
        providers={
            "litellm": ProviderConfig(transport="chat_completions", api_key="sk-test")
        },
        profiles=profiles,
        default=default,
    )


def _response(
    status_code: int,
    *,
    method: str,
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


def test_rejects_duplicate_file_and_image_url() -> None:
    service = _service()

    with pytest.raises(ImageInputError) as exc:
        service.validate_current_images(
            files=["https://oss.example.com/chat/a.png"],
            images=["https://oss.example.com/chat/a.png"],
        )

    assert exc.value.error_code == IMAGE_INPUT_DUPLICATE_ATTACHMENT


def test_rejects_non_https_scheme() -> None:
    with pytest.raises(ImageInputError) as exc:
        _service().validate_current_images(
            files=[],
            images=["http://oss.example.com/chat/a.png"],
        )

    assert exc.value.error_code == IMAGE_INPUT_INVALID_SCHEME


def test_head_success_accepts_png() -> None:
    client = MagicMock()
    client.head.return_value = _response(
        200,
        method="HEAD",
        headers={"content-type": "image/png", "content-length": "100"},
    )

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        images = _service().validate_current_images(
            files=[],
            images=["https://oss.example.com/chat/a.png"],
        )

    assert images[0].url == "https://oss.example.com/chat/a.png"
    assert images[0].mime_type == "image/png"
    assert images[0].size_bytes == 100
    client.get.assert_not_called()


def test_default_limit_rejects_image_over_five_mib() -> None:
    client = MagicMock()
    client.head.return_value = _response(
        200,
        method="HEAD",
        headers={
            "content-type": "image/png",
            "content-length": str(5 * 1024 * 1024 + 1),
        },
    )

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImageInputError) as exc:
            _service().validate_current_images(
                files=[],
                images=["https://oss.example.com/chat/a.png"],
            )

    assert exc.value.error_code == IMAGE_INPUT_TOO_LARGE
    client.get.assert_not_called()


def test_probe_rejects_redirect_final_url_to_private_ip() -> None:
    client = MagicMock()
    client.head.return_value = _response(
        200,
        method="HEAD",
        url="https://127.0.0.1/admin/a.png",
        headers={"content-type": "image/png", "content-length": "100"},
    )

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImageInputError) as exc:
            _service().validate_current_images(
                files=[],
                images=["https://oss.example.com/chat/a.png"],
            )

    assert exc.value.error_code == IMAGE_INPUT_DOMAIN_BLOCKED
    client.get.assert_not_called()


def test_probe_rejects_manual_redirect_before_following_to_private_ip() -> None:
    client = MagicMock()
    client.head.return_value = _response(
        302,
        method="HEAD",
        headers={"location": "https://127.0.0.1/admin/a.png"},
    )
    client.get.return_value = _response(
        206,
        method="GET",
        headers={"content-range": "bytes 0-4095/4096"},
        content=b"\x89PNG\r\n\x1a\npayload",
    )

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImageInputError) as exc:
            _service().validate_current_images(
                files=[],
                images=["https://oss.example.com/chat/a.png"],
            )

    assert exc.value.error_code == IMAGE_INPUT_DOMAIN_BLOCKED
    client.get.assert_not_called()


def test_validate_current_images_disables_httpx_auto_redirects() -> None:
    client = MagicMock()
    client.head.return_value = _response(
        200,
        method="HEAD",
        headers={"content-type": "image/png", "content-length": "100"},
    )

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        _service().validate_current_images(
            files=[],
            images=["https://oss.example.com/chat/a.png"],
        )

    assert client_cls.call_args.kwargs["follow_redirects"] is False


def test_history_image_url_accepts_any_https_external_host() -> None:
    # Host allowlist removed: any well-formed https URL on a public host is accepted.
    assert (
        _service().validate_history_image_url("https://oss.example.com/chat/a.png")
        is True
    )


def test_history_image_url_still_rejects_private_ip() -> None:
    assert (
        _service().validate_history_image_url("https://127.0.0.1/admin/a.png") is False
    )


def test_head_failure_falls_back_to_range_get_magic_bytes() -> None:
    client = MagicMock()
    client.head.return_value = _response(405, method="HEAD")
    client.get.return_value = _response(
        206,
        method="GET",
        headers={"content-range": "bytes 0-4095/4096"},
        content=b"RIFF\x10\x00\x00\x00WEBPVP8 ",
    )

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        images = _service().validate_current_images(
            files=[],
            images=["https://oss.example.com/chat/a.webp"],
        )

    assert images[0].mime_type == "image/webp"
    assert images[0].size_bytes == 4096
    client.get.assert_called_once()
    assert client.get.call_args.kwargs["headers"] == {"Range": "bytes=0-4095"}


def test_range_get_without_size_rejects_current_image() -> None:
    client = MagicMock()
    client.head.return_value = _response(403, method="HEAD")
    client.get.return_value = _response(
        200,
        method="GET",
        content=b"\x89PNG\r\n\x1a\npayload",
    )

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImageInputError) as exc:
            _service().validate_current_images(
                files=[],
                images=["https://oss.example.com/chat/a.png"],
            )

    assert exc.value.error_code == IMAGE_INPUT_SIZE_UNKNOWN


def test_range_get_rejects_unsupported_magic_bytes() -> None:
    client = MagicMock()
    client.head.return_value = _response(403, method="HEAD")
    client.get.return_value = _response(
        206,
        method="GET",
        headers={"content-range": "bytes 0-4095/128"},
        content=b"not-an-image",
    )

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        with pytest.raises(ImageInputError) as exc:
            _service().validate_current_images(
                files=[],
                images=["https://oss.example.com/chat/a.bin"],
            )

    assert exc.value.error_code == IMAGE_INPUT_UNSUPPORTED_MIME


def test_ensure_vision_supported_rejects_text_only_profile() -> None:
    config = _llm_config(
        profiles={
            "plain": LLMProfileConfig(
                provider="litellm",
                model="plain",
                context_limit=200_000,
            )
        },
        default="plain",
    )

    with pytest.raises(ImageInputError) as exc:
        _service().ensure_vision_supported(
            llm_config=config,
            model_override=None,
            default_profile_key=None,
        )

    assert exc.value.error_code == VISION_MODEL_NOT_SUPPORTED


def test_ensure_vision_supported_returns_profile_for_vision_profile() -> None:
    config = _llm_config(
        profiles={
            "vision": LLMProfileConfig(
                provider="litellm",
                model="vision",
                context_limit=200_000,
                supports_vision=True,
                vision_detail="high",
            )
        },
        default="vision",
    )

    profile = _service().ensure_vision_supported(
        llm_config=config,
        model_override=None,
        default_profile_key=None,
    )

    assert profile.supports_vision is True


def test_resolve_image_detail_returns_none_without_images() -> None:
    config = _llm_config(
        profiles={
            "plain": LLMProfileConfig(
                provider="litellm",
                model="plain",
                context_limit=200_000,
            )
        },
        default="plain",
    )

    result = _service().resolve_image_detail(
        llm_config=config,
        images=(),
        model_override=None,
        default_profile_key=None,
    )

    assert result is None


def test_resolve_image_detail_returns_profile_detail_for_images() -> None:
    config = _llm_config(
        profiles={
            "vision": LLMProfileConfig(
                provider="litellm",
                model="vision",
                context_limit=200_000,
                supports_vision=True,
                vision_detail="high",
            )
        },
        default="vision",
    )

    result = _service().resolve_image_detail(
        llm_config=config,
        images=("https://oss.example.com/chat/a.png",),
        model_override=None,
        default_profile_key=None,
    )

    assert result == "high"


def test_enrich_turn_input_images_builds_turn_input_when_missing() -> None:
    enriched = _service().enrich_turn_input_images(
        turn_input=None,
        user_prompt="inspect image",
        top_level_images=("https://oss.example.com/chat/a.png",),
        image_detail="low",
    )

    assert enriched.user_text == "inspect image"
    assert enriched.images == ("https://oss.example.com/chat/a.png",)
    assert enriched.attachments.image_detail == "low"


def test_enrich_turn_input_images_preserves_existing_turn_input_images() -> None:
    turn_input = TurnInput.from_values(
        user_text="from turn input",
        files=("https://oss.example.com/chat/a.cif",),
        images=("https://oss.example.com/chat/existing.png",),
        image_detail="auto",
        workspace_paths=("/workspace/note.md",),
        pre_turn_history_event_id=22,
    )

    enriched = _service().enrich_turn_input_images(
        turn_input=turn_input,
        user_prompt="ignored",
        top_level_images=("https://oss.example.com/chat/top.png",),
        image_detail="high",
    )

    assert enriched.user_text == "from turn input"
    assert enriched.files == ("https://oss.example.com/chat/a.cif",)
    assert enriched.images == ("https://oss.example.com/chat/existing.png",)
    assert enriched.workspace_paths == ("/workspace/note.md",)
    assert enriched.pre_turn_history_event_id == 22
    assert enriched.attachments.image_detail == "high"


def test_enrich_turn_input_images_preserves_existing_detail_when_no_new_detail() -> (
    None
):
    turn_input = TurnInput.from_values(
        user_text="from turn input",
        images=("https://oss.example.com/chat/existing.png",),
        image_detail="auto",
    )

    enriched = _service().enrich_turn_input_images(
        turn_input=turn_input,
        user_prompt="ignored",
        top_level_images=(),
        image_detail=None,
    )

    assert enriched.images == ("https://oss.example.com/chat/existing.png",)
    assert enriched.attachments.image_detail == "auto"
