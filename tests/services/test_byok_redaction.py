from __future__ import annotations

from src.services.byok_redaction import (
    redact_mapping,
    redact_text,
    sanitize_provider_error,
)


def test_redact_mapping_removes_secret_fields_recursively() -> None:
    payload = {
        "api_key": "sk-top-level",
        "api_key_cipher": "cipher-token",
        "nested": {
            "Authorization": "Bearer sk-nested",
            "secret": "secret-value",
            "safe": "visible",
            "items": [
                {"token": "access-token"},
                {"access_token": "access-token-2"},
                {"refresh_token": "refresh-token"},
                "plain",
            ],
        },
    }

    redacted = redact_mapping(payload)

    assert redacted["api_key"] == "<redacted>"
    assert redacted["api_key_cipher"] == "<redacted>"
    assert redacted["nested"]["Authorization"] == "<redacted>"
    assert redacted["nested"]["secret"] == "<redacted>"
    assert redacted["nested"]["items"][0]["token"] == "<redacted>"
    assert redacted["nested"]["items"][1]["access_token"] == "<redacted>"
    assert redacted["nested"]["items"][2]["refresh_token"] == "<redacted>"
    assert redacted["nested"]["safe"] == "visible"
    assert redacted["nested"]["items"][3] == "plain"


def test_redact_text_masks_common_secret_patterns() -> None:
    text = (
        "api_key=sk-1234567890abcdef Authorization: Bearer sk-live-token "
        "token=abc123 secret: hidden"
    )

    redacted = redact_text(text)

    assert "sk-1234567890abcdef" not in redacted
    assert "sk-live-token" not in redacted
    assert "abc123" not in redacted
    assert "hidden" not in redacted
    assert "api_key=<redacted>" in redacted
    assert "Authorization: <redacted>" in redacted
    assert "token=<redacted>" in redacted
    assert "secret: <redacted>" in redacted


def test_sanitize_provider_error_truncates_and_redacts() -> None:
    error = RuntimeError(
        "provider failed with api_key=sk-1234567890abcdef "
        "Authorization: Bearer sk-live-token and a very long body"
    )

    sanitized = sanitize_provider_error(error, max_chars=80)

    assert "sk-1234567890abcdef" not in sanitized
    assert "sk-live-token" not in sanitized
    assert sanitized.endswith("\u2026")
    assert len(sanitized) <= 80
