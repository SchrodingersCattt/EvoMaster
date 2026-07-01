"""MatMaster 平台用户运行偏好客户端。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from utils.env import MATMASTER_TOOLS_SERVER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


@dataclass(frozen=True)
class UserLevelRuntimePreference:
    project_id: int | None = None
    model: str | None = None
    bohrium_submit_confirmation_required: bool | None = None
    bohrium_job_max_runtime_seconds: int | None = None
    bohrium_node_sku_id: int | None = None
    programmatic_trigger_enabled: bool | None = None
    loaded: bool = False


def _runtime_preference_url(user_id: str) -> str:
    base = (MATMASTER_TOOLS_SERVER or "").strip().rstrip("/")
    uid = quote((user_id or "").strip(), safe="")
    return f"{base}/api/v1/users/{uid}/runtime-preference"


def _coerce_project_id(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_model(value: Any) -> str | None:
    if value is None:
        return None
    model = str(value).strip()
    return model or None


def _coerce_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def get_user_level_runtime_preference(user_id: str) -> UserLevelRuntimePreference:
    """从平台读取用户级运行偏好。

    fail-soft：调用方可在偏好服务抖动时继续运行，仅回退为不带默认项目/模型。
    """
    uid = (user_id or "").strip()
    if not uid:
        return UserLevelRuntimePreference()
    base = (MATMASTER_TOOLS_SERVER or "").strip()
    if not base:
        logger.debug("MATMASTER_TOOLS_SERVER 未配置，跳过用户运行偏好查询")
        return UserLevelRuntimePreference()

    url = _runtime_preference_url(uid)
    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            response = client.get(url, headers={"X-User-Id": uid})
        if response.status_code >= 400:
            logger.warning(
                "platform runtime preference HTTP %s user_id=%s body=%s",
                response.status_code,
                uid,
                (response.text or "")[:256],
            )
            return UserLevelRuntimePreference()
        body = response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning(
            "platform runtime preference failed user_id=%s error=%s",
            uid,
            exc,
            exc_info=True,
        )
        return UserLevelRuntimePreference()

    if body.get("code") != 0:
        logger.warning(
            "platform runtime preference non-zero user_id=%s code=%s msg=%s",
            uid,
            body.get("code"),
            body.get("msg"),
        )
        return UserLevelRuntimePreference()
    data = body.get("data") or {}
    return UserLevelRuntimePreference(
        project_id=_coerce_project_id(data.get("last_selected_project_id")),
        model=_coerce_model(data.get("last_selected_model")),
        bohrium_submit_confirmation_required=(
            data.get("bohrium_submit_confirmation_required")
            if isinstance(data.get("bohrium_submit_confirmation_required"), bool)
            else None
        ),
        bohrium_job_max_runtime_seconds=_coerce_positive_int(
            data.get("bohrium_job_max_runtime_seconds")
        ),
        bohrium_node_sku_id=_coerce_positive_int(data.get("bohrium_node_sku_id")),
        programmatic_trigger_enabled=(
            data.get("programmatic_trigger_enabled")
            if isinstance(data.get("programmatic_trigger_enabled"), bool)
            else None
        ),
        loaded=True,
    )


__all__ = [
    "UserLevelRuntimePreference",
    "get_user_level_runtime_preference",
]
