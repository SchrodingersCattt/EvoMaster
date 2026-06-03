"""BYOK 凭证下发客户端：向 matmaster-tools-server 取解密后的 {model, base_url, api_key}。

仅在运行时按 user_id + credential_id 拉取，凭证只在本次 run 内存使用、不落盘、不长缓存。
鉴权用 Nacos byok.service_api_keys 对应的服务 Bearer（MATMASTER_TOOLS_BYOK_BEARER）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from utils.env import MATMASTER_TOOLS_BYOK_BEARER, MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ByokCredential:
    model: str
    base_url: str
    api_key: str
    # 黑盒透传参数（来自 tools-server model_params），原样作为 extra_body 合并进请求体。
    extra_body: dict[str, Any] = field(default_factory=dict)


class ByokCredentialError(Exception):
    """凭证拉取失败（缺鉴权配置 / 不存在 / 服务异常）。"""


def fetch_byok_credential(*, user_id: str, credential_id: str) -> ByokCredential:
    """同步拉取解密凭证。失败抛 ByokCredentialError，由调用方决定如何向用户报错。"""
    if not MATMASTER_TOOLS_BYOK_BEARER:
        raise ByokCredentialError("未配置 MATMASTER_TOOLS_BYOK_BEARER")
    if not (user_id and credential_id):
        raise ByokCredentialError("缺少 user_id 或 credential_id")

    base = MATMASTER_TOOLS_SERVER.rstrip("/")
    url = f"{base}/api/v1/internal/llm-credentials/{credential_id}"
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            resp = client.get(
                url,
                params={"user_id": user_id},
                headers={"Authorization": f"Bearer {MATMASTER_TOOLS_BYOK_BEARER}"},
            )
    except Exception as exc:  # noqa: BLE001
        raise ByokCredentialError(f"凭证服务请求失败: {exc}") from exc

    if resp.status_code >= 400:
        raise ByokCredentialError(f"凭证服务返回 HTTP {resp.status_code}")
    body = resp.json() or {}
    if body.get("code") != 0 or not body.get("data"):
        raise ByokCredentialError(body.get("msg") or "凭证不存在或不可用")
    data = body["data"]
    params = data.get("model_params")
    extra_body = params if isinstance(params, dict) else {}
    return ByokCredential(
        model=data.get("model") or "",
        base_url=data.get("base_url") or "",
        api_key=data.get("api_key") or "",
        extra_body=extra_body,
    )
