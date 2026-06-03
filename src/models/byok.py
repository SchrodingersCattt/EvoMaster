from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)

_ALLOWED_PARAM_KEYS = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "reasoning_effort",
        "seed",
        "stop",
    }
)
_SECRET_EXTRA_BODY_KEYS = frozenset(
    {
        "api_key",
        "api-key",
        "api_key_cipher",
        "authorization",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }
)
_CORE_PROVIDER_KEYS = frozenset(
    {
        "messages",
        "tools",
        "stream",
        "model",
        "temperature",
        "max_tokens",
    }
)


def _trim_required(value: str, *, field_name: str) -> str:
    trimmed = (value or "").strip()
    if not trimmed:
        raise ValueError(f"{field_name} is required.")
    return trimmed


def _json_size(value: Any) -> int:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be JSON serializable.") from exc
    return len(encoded.encode())


def _validate_json_size(value: Any, *, limit: int, field_name: str) -> Any:
    if value is None:
        return None
    if _json_size(value) > limit:
        raise ValueError(f"{field_name} exceeds {limit} bytes.")
    return value


def _reject_keys_recursive(value: Any, blocked: frozenset[str], *, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in blocked:
                raise ValueError(f"extra_body must not contain {label} key {key}.")
            _reject_keys_recursive(item, blocked, label=label)
    elif isinstance(value, list):
        for item in value:
            _reject_keys_recursive(item, blocked, label=label)


def _coerce_json_mapping(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("JSON value must be an object.")
        return parsed
    if isinstance(value, dict):
        return value
    raise ValueError("JSON value must be an object.")


class BYOKCapabilities(BaseModel):
    supports_streaming: bool = True
    supports_tool_calling: bool = True
    supports_vision: bool = False


class _BYOKConfigInputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    base_url: str | None = None
    model: str | None = None
    params: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None
    prompt_cache: dict[str, Any] | None = None
    supports_streaming: bool | None = None
    supports_tool_calling: bool | None = None
    supports_vision: bool | None = None
    is_enabled: bool | None = None

    @field_validator("display_name", "base_url", "model", mode="before")
    @classmethod
    def _trim_optional_string(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("value must not be empty.")
        return trimmed

    @field_validator("params")
    @classmethod
    def _validate_params(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        unknown = set(value) - _ALLOWED_PARAM_KEYS
        if unknown:
            raise ValueError(f"params contains unsupported keys: {sorted(unknown)}")
        return _validate_json_size(value, limit=8 * 1024, field_name="params")

    @field_validator("extra_body", mode="before")
    @classmethod
    def _validate_extra_body_type(cls, value: object) -> object:
        if value is None or isinstance(value, dict):
            return value
        raise ValueError("extra_body must be a JSON object.")

    @field_validator("extra_body")
    @classmethod
    def _validate_extra_body(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        _reject_keys_recursive(value, _SECRET_EXTRA_BODY_KEYS, label="credential")
        _reject_keys_recursive(value, _CORE_PROVIDER_KEYS, label="provider core")
        return _validate_json_size(
            value,
            limit=32 * 1024,
            field_name="extra_body",
        )

    @field_validator("prompt_cache", mode="before")
    @classmethod
    def _validate_prompt_cache_type(cls, value: object) -> object:
        if value is None or isinstance(value, dict):
            return value
        raise ValueError("prompt_cache must be a JSON object.")

    @field_validator("prompt_cache")
    @classmethod
    def _validate_prompt_cache(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return _validate_json_size(
            value,
            limit=4 * 1024,
            field_name="prompt_cache",
        )


class BYOKConfigCreate(_BYOKConfigInputBase):
    display_name: str
    base_url: str
    model: str
    api_key: SecretStr = Field(repr=False)
    supports_streaming: bool = True
    supports_tool_calling: bool = True
    supports_vision: bool = False

    @field_validator("display_name", "base_url", "model")
    @classmethod
    def _validate_required_strings(cls, value: str, info: Any) -> str:
        return _trim_required(value, field_name=info.field_name)

    @field_validator("api_key", mode="before")
    @classmethod
    def _validate_api_key(cls, value: object) -> object:
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("api_key is required.")
            return trimmed
        return value


class BYOKConfigUpdate(_BYOKConfigInputBase):
    api_key: SecretStr | None = Field(default=None, repr=False)

    @field_validator("api_key", mode="before")
    @classmethod
    def _validate_optional_api_key(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("api_key must not be empty.")
            return trimmed
        return value


class BYOKConfigOut(BaseModel):
    id: int
    display_name: str
    base_url: str
    model: str
    api_key_hint: str
    key_version: str
    params: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    prompt_cache: dict[str, Any] = Field(default_factory=dict)
    supports_streaming: bool = True
    supports_tool_calling: bool = True
    supports_vision: bool = False
    verification_status: str = "unverified"
    verification_error: str | None = None
    verified_at: datetime | None = None
    is_enabled: bool = True
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BYOKRunReference(BaseModel):
    config_id: int
    version: int
    display_name: str | None = None
    model: str | None = None

    def to_job_payload(self) -> dict[str, int]:
        return {"config_id": self.config_id, "version": self.version}


class BYOKResolvedPreflight(BaseModel):
    config: BYOKConfigOut
    ref: BYOKRunReference


class BYOKResolvedWorkerRun(BaseModel):
    config_id: int
    version: int
    model: str
    display_name: str | None = None
    profile: Any


def to_config_out(row: dict[str, Any]) -> BYOKConfigOut:
    return BYOKConfigOut(
        id=int(row["id"]),
        display_name=str(row["display_name"]),
        base_url=str(row["base_url"]),
        model=str(row["model"]),
        api_key_hint=str(row["api_key_hint"]),
        key_version=str(row.get("key_version") or "v1"),
        params=_coerce_json_mapping(row.get("params")),
        extra_body=_coerce_json_mapping(row.get("extra_body")),
        prompt_cache=_coerce_json_mapping(row.get("prompt_cache")),
        supports_streaming=bool(row.get("supports_streaming")),
        supports_tool_calling=bool(row.get("supports_tool_calling")),
        supports_vision=bool(row.get("supports_vision")),
        verification_status=str(row.get("verification_status") or "unverified"),
        verification_error=row.get("verification_error"),
        verified_at=row.get("verified_at"),
        is_enabled=bool(row.get("is_enabled", True)),
        version=int(row["version"]),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )
