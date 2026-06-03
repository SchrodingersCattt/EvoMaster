from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models.byok import BYOKConfigCreate, BYOKConfigUpdate, to_config_out


def _create_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "  Research Proxy  ",
        "base_url": "  https://api.example.com/v1  ",
        "model": "  openai-compatible-model  ",
        "api_key": "sk-1234567890abcdef",
    }
    payload.update(overrides)
    return payload


def test_create_trims_required_strings_and_hides_secret_repr() -> None:
    req = BYOKConfigCreate(**_create_payload())

    assert req.display_name == "Research Proxy"
    assert req.base_url == "https://api.example.com/v1"
    assert req.model == "openai-compatible-model"
    assert "sk-1234567890abcdef" not in repr(req)
    assert "sk-1234567890abcdef" not in str(req)


def test_params_are_whitelisted_and_bounded() -> None:
    req = BYOKConfigCreate(
        **_create_payload(
            params={
                "temperature": 0.2,
                "seed": 42,
                "reasoning_effort": "high",
            }
        )
    )
    assert req.params == {
        "temperature": 0.2,
        "seed": 42,
        "reasoning_effort": "high",
    }

    with pytest.raises(ValidationError):
        BYOKConfigCreate(**_create_payload(params={"api_key": "secret"}))

    with pytest.raises(ValidationError):
        BYOKConfigCreate(**_create_payload(params={"stop": "x" * 9000}))


def test_extra_body_rejects_credentials_core_fields_and_non_objects() -> None:
    BYOKConfigCreate(**_create_payload(extra_body={"metadata": {"team": "lab"}}))

    rejected_values = [
        ["not", "an", "object"],
        {"api_key": "secret"},
        {"nested": {"token": "secret"}},
        {"messages": []},
        {"tools": []},
        {"stream": True},
        {"model": "other-model"},
        {"temperature": 0.1},
        {"max_tokens": 128},
    ]
    for extra_body in rejected_values:
        with pytest.raises(ValidationError):
            BYOKConfigCreate(**_create_payload(extra_body=extra_body))


def test_patch_empty_object_is_allowed_but_empty_api_key_is_rejected() -> None:
    assert BYOKConfigUpdate().model_dump(exclude_unset=True) == {}

    with pytest.raises(ValidationError):
        BYOKConfigUpdate(api_key="")


def test_to_config_out_drops_cipher() -> None:
    row = {
        "id": 12,
        "user_id": "user-1",
        "display_name": "Research Proxy",
        "base_url": "https://api.example.com/v1",
        "model": "model-a",
        "api_key_cipher": "cipher-token",
        "api_key_hint": "sk-...cdef",
        "key_version": "v1",
        "params": {"temperature": 0.2},
        "extra_body": {"metadata": {"team": "lab"}},
        "prompt_cache": {"type": "ephemeral"},
        "supports_streaming": 1,
        "supports_tool_calling": 1,
        "supports_vision": 0,
        "verification_status": "verified",
        "verification_error": None,
        "verified_at": datetime(2026, 6, 3, tzinfo=timezone.utc),
        "is_enabled": 1,
        "version": 3,
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 6, 2, tzinfo=timezone.utc),
    }

    out = to_config_out(row)
    dumped = out.model_dump()

    assert dumped["id"] == 12
    assert dumped["api_key_hint"] == "sk-...cdef"
    assert dumped["supports_streaming"] is True
    assert dumped["supports_tool_calling"] is True
    assert dumped["supports_vision"] is False
    assert "api_key_cipher" not in dumped
    assert "api_key" not in dumped
    assert "user_id" not in dumped
