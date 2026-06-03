from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from src.base.base_res import BaseResponse
from src.dao.user_llm_config_table import (
    UserLLMConfigTable,
    get_user_llm_config_table,
)
from src.models.byok import BYOKConfigCreate, BYOKConfigOut, BYOKConfigUpdate
from src.models.byok import to_config_out
from src.services.byok_endpoint_policy import BYOKEndpointPolicy
from src.services.byok_redaction import sanitize_provider_error
from src.services.byok_verifier import BYOKVerifier, get_byok_verifier
from src.services.user_service import UserService
from src.utils import secret
from src.utils.exceptions import BaseErrorResponse, NotFoundErrorResponse


router = APIRouter(tags=["BYOK LLM Configs"])


class BYOKConfigTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str
    api_key: SecretStr = Field(repr=False)
    supports_vision: bool = False

    @field_validator("base_url", "model")
    @classmethod
    def _trim_required(cls, value: str) -> str:
        trimmed = (value or "").strip()
        if not trimmed:
            raise ValueError("value must not be empty.")
        return trimmed

    @field_validator("api_key", mode="before")
    @classmethod
    def _trim_api_key(cls, value: object) -> object:
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("api_key must not be empty.")
            return trimmed
        return value


def _require_enabled() -> None:
    if not secret.is_byok_enabled():
        raise BaseErrorResponse(
            http_status=503,
            code=503,
            msg="BYOK LLM 配置未启用",
            data={"error_code": "byok_disabled"},
        )


def _not_found() -> NotFoundErrorResponse:
    return NotFoundErrorResponse(
        msg="自定义模型配置不存在",
        data={"error_code": "byok_not_found"},
    )


def _safe_config(row: dict[str, Any]) -> BYOKConfigOut:
    return to_config_out(row)


def _verification_fields(result: dict[str, Any]) -> dict[str, Any]:
    status = str(result.get("status") or "failed")
    if status != "verified":
        status = "failed"
    error = result.get("error")
    sanitized_error = sanitize_provider_error(error) if error else None
    return {
        "verification_status": status,
        "verification_error": sanitized_error,
        "verified_at": (
            datetime.now(timezone.utc).replace(tzinfo=None)
            if status == "verified"
            else None
        ),
        "supports_streaming": bool(result.get("supports_streaming")),
        "supports_tool_calling": bool(result.get("supports_tool_calling")),
        "supports_vision": bool(result.get("supports_vision")),
    }


@router.post("", response_model=BaseResponse[BYOKConfigOut])
def create_config(
    req: BYOKConfigCreate,
    user_id: str = Depends(UserService.require_user_id),
    table: UserLLMConfigTable = Depends(get_user_llm_config_table),
) -> BaseResponse[BYOKConfigOut]:
    _require_enabled()
    api_key = req.api_key.get_secret_value()
    base_url = BYOKEndpointPolicy().validate_base_url(req.base_url)
    config_id = table.create(
        user_id,
        display_name=req.display_name,
        base_url=base_url,
        model=req.model,
        api_key_cipher=secret.encrypt(api_key),
        api_key_hint=secret.hint(api_key),
        key_version=secret.current_key_version(),
        params=req.params or {},
        extra_body=req.extra_body or {},
        prompt_cache=req.prompt_cache or {},
        supports_streaming=req.supports_streaming,
        supports_tool_calling=req.supports_tool_calling,
        supports_vision=req.supports_vision,
        verification_status="unverified",
        verification_error=None,
        verified_at=None,
        is_enabled=True if req.is_enabled is None else req.is_enabled,
    )
    row = table.get(user_id, config_id)
    if row is None:
        raise _not_found()
    return BaseResponse(data=_safe_config(row))


@router.get("", response_model=BaseResponse[list[BYOKConfigOut]])
def list_configs(
    user_id: str = Depends(UserService.require_user_id),
    table: UserLLMConfigTable = Depends(get_user_llm_config_table),
) -> BaseResponse[list[BYOKConfigOut]]:
    _require_enabled()
    return BaseResponse(data=[_safe_config(row) for row in table.list_by_user(user_id)])


@router.post("/test", response_model=BaseResponse[dict[str, Any]])
async def test_unsaved_config(
    req: BYOKConfigTestRequest,
    user_id: str = Depends(UserService.require_user_id),
    verifier: BYOKVerifier = Depends(get_byok_verifier),
) -> BaseResponse[dict[str, Any]]:
    _require_enabled()
    base_url = BYOKEndpointPolicy().validate_base_url(req.base_url)
    result = await verifier.verify_unsaved(
        base_url=base_url,
        model=req.model,
        api_key=req.api_key.get_secret_value(),
        supports_vision=req.supports_vision,
    )
    safe_result = {
        **_verification_fields(result),
        "user_id": user_id,
    }
    safe_result.pop("verified_at", None)
    return BaseResponse(data=safe_result)


@router.get("/{config_id}", response_model=BaseResponse[BYOKConfigOut])
def get_config(
    config_id: int,
    user_id: str = Depends(UserService.require_user_id),
    table: UserLLMConfigTable = Depends(get_user_llm_config_table),
) -> BaseResponse[BYOKConfigOut]:
    _require_enabled()
    row = table.get(user_id, config_id)
    if row is None:
        raise _not_found()
    return BaseResponse(data=_safe_config(row))


@router.patch("/{config_id}", response_model=BaseResponse[BYOKConfigOut])
def update_config(
    config_id: int,
    req: BYOKConfigUpdate,
    user_id: str = Depends(UserService.require_user_id),
    table: UserLLMConfigTable = Depends(get_user_llm_config_table),
) -> BaseResponse[BYOKConfigOut]:
    _require_enabled()
    fields = req.model_dump(exclude_unset=True)
    if "base_url" in fields:
        fields["base_url"] = BYOKEndpointPolicy().validate_base_url(fields["base_url"])
    if "api_key" in req.model_fields_set:
        api_key = req.api_key.get_secret_value() if req.api_key is not None else ""
        fields.pop("api_key", None)
        fields.update(
            {
                "api_key_cipher": secret.encrypt(api_key),
                "api_key_hint": secret.hint(api_key),
                "key_version": secret.current_key_version(),
                "verification_status": "unverified",
                "verification_error": None,
                "verified_at": None,
            }
        )

    if not table.update(user_id, config_id, **fields):
        raise _not_found()
    row = table.get(user_id, config_id)
    if row is None:
        raise _not_found()
    return BaseResponse(data=_safe_config(row))


@router.delete("/{config_id}", response_model=BaseResponse[None])
def delete_config(
    config_id: int,
    user_id: str = Depends(UserService.require_user_id),
    table: UserLLMConfigTable = Depends(get_user_llm_config_table),
) -> BaseResponse[None]:
    _require_enabled()
    if not table.delete(user_id, config_id):
        raise _not_found()
    return BaseResponse(data=None)


@router.post("/{config_id}/test", response_model=BaseResponse[BYOKConfigOut])
async def test_saved_config(
    config_id: int,
    user_id: str = Depends(UserService.require_user_id),
    table: UserLLMConfigTable = Depends(get_user_llm_config_table),
    verifier: BYOKVerifier = Depends(get_byok_verifier),
) -> BaseResponse[BYOKConfigOut]:
    _require_enabled()
    row = table.get(user_id, config_id)
    if row is None:
        raise _not_found()
    result = await verifier.verify_unsaved(
        base_url=str(row["base_url"]),
        model=str(row["model"]),
        api_key=secret.decrypt(str(row["api_key_cipher"])),
        supports_vision=bool(row.get("supports_vision")),
    )
    table.update(user_id, config_id, **_verification_fields(result))
    updated = table.get(user_id, config_id)
    if updated is None:
        raise _not_found()
    return BaseResponse(data=_safe_config(updated))
