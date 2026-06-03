from __future__ import annotations

from functools import lru_cache
from typing import Any

from matmaster.config.llm import LLMProfileConfig

from src.dao.user_llm_config_table import (
    UserLLMConfigTable,
    get_user_llm_config_table,
)
from src.models.byok import (
    BYOKResolvedPreflight,
    BYOKResolvedWorkerRun,
    BYOKRunReference,
    to_config_out,
)
from src.services.byok_endpoint_policy import (
    BYOKEndpointPolicy,
    BYOKEndpointPolicyError,
)
from src.utils import secret


class BYOKResolveError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int = 400,
        error_code: str = "byok_invalid",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.error_code = error_code


class BYOKModelResolver:
    def __init__(
        self,
        *,
        table: UserLLMConfigTable | None = None,
        endpoint_policy: BYOKEndpointPolicy | None = None,
        secret_module: Any = secret,
    ) -> None:
        self._table = table or get_user_llm_config_table()
        self._endpoint_policy = endpoint_policy or BYOKEndpointPolicy()
        self._secret = secret_module

    def resolve_for_preflight(
        self,
        *,
        user_id: str,
        config_id: int,
        mode: str,
        has_images: bool,
    ) -> BYOKResolvedPreflight:
        row = self._table.get(user_id, config_id)
        normalized = self._validate_row(
            row,
            mode=mode,
            has_images=has_images,
            expected_version=None,
        )
        config = to_config_out(normalized)
        ref = BYOKRunReference(
            config_id=int(normalized["id"]),
            version=int(normalized["version"]),
            display_name=str(normalized.get("display_name") or ""),
            model=str(normalized.get("model") or ""),
        )
        return BYOKResolvedPreflight(config=config, ref=ref)

    def resolve_for_worker_run(
        self,
        *,
        user_id: str,
        config_id: int,
        expected_version: int,
        mode: str,
        has_images: bool,
    ) -> BYOKResolvedWorkerRun:
        row = self._table.get_for_run(user_id, config_id)
        normalized = self._validate_row(
            row,
            mode=mode,
            has_images=has_images,
            expected_version=expected_version,
        )
        try:
            api_key = self._secret.decrypt(str(normalized["api_key_cipher"]))
        except Exception as exc:
            raise BYOKResolveError(
                "自定义模型密钥解密失败，请重新保存该配置。",
                error_code="byok_secret_error",
            ) from exc
        profile = self._build_profile(normalized, api_key=api_key)
        return BYOKResolvedWorkerRun(
            config_id=int(normalized["id"]),
            version=int(normalized["version"]),
            model=str(normalized["model"]),
            display_name=str(normalized.get("display_name") or ""),
            profile=profile,
        )

    def _validate_row(
        self,
        row: dict[str, Any] | None,
        *,
        mode: str,
        has_images: bool,
        expected_version: int | None,
    ) -> dict[str, Any]:
        if row is None:
            raise BYOKResolveError(
                "自定义模型配置不存在。",
                http_status=404,
                error_code="byok_not_found",
            )
        if not bool(row.get("is_enabled", True)):
            raise BYOKResolveError(
                "自定义模型配置已禁用。",
                error_code="byok_disabled",
            )
        if str(row.get("verification_status") or "") != "verified":
            raise BYOKResolveError(
                "自定义模型配置尚未验证通过。",
                error_code="byok_unverified",
            )
        version = int(row.get("version") or 0)
        if expected_version is not None and version != int(expected_version):
            raise BYOKResolveError(
                "自定义模型配置已更新，请重新发送本轮请求。",
                http_status=409,
                error_code="byok_version_mismatch",
            )
        if mode in {"direct", "planner"} and (
            not bool(row.get("supports_streaming"))
            or not bool(row.get("supports_tool_calling"))
        ):
            raise BYOKResolveError(
                "自定义模型必须支持流式输出和 tool calling。",
                error_code="byok_capability_missing",
            )
        if has_images and not bool(row.get("supports_vision")):
            raise BYOKResolveError(
                "该自定义模型不支持图片输入，请切换模型。",
                error_code="byok_vision_required",
            )

        try:
            base_url = self._endpoint_policy.validate_base_url(str(row["base_url"]))
        except BYOKEndpointPolicyError as exc:
            raise BYOKResolveError(
                f"自定义模型 endpoint 不安全或不可用：{exc}",
                error_code="byok_endpoint_not_allowed",
            ) from exc

        normalized = dict(row)
        normalized["base_url"] = base_url
        return normalized

    @staticmethod
    def _build_profile(row: dict[str, Any], *, api_key: str) -> LLMProfileConfig:
        params = dict(row.get("params") or {})
        temperature = params.pop("temperature", None)
        max_tokens = params.pop("max_tokens", None)
        kwargs: dict[str, Any] = {
            "provider": "openai",
            "model": str(row["model"]),
            "api_key": api_key,
            "base_url": str(row["base_url"]),
            "supports_vision": bool(row.get("supports_vision")),
            "timeout": 600,
            "stream_timeout": 120,
            "stream_idle_timeout": 60,
            "max_retries": 2,
            "retry_delay": 1.0,
            "vision_detail": "high",
            "passthrough_params": params or None,
            "passthrough_extra_body": dict(row.get("extra_body") or {}) or None,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return LLMProfileConfig(**kwargs)


@lru_cache(maxsize=1)
def get_byok_model_resolver() -> BYOKModelResolver:
    return BYOKModelResolver()
