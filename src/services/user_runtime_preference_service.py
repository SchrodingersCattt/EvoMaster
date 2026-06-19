"""用户运行偏好组合服务。

用户级偏好由根目录 clients 层从 matmaster-tools-server 读取；evo 本地只补充
会话运行态上下文（例如最近 org_id），避免业务入口直接依赖 user_preference 表结构。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pymysql import Error

from clients.user_runtime_preference_client import get_user_level_runtime_preference
from src.dao.chat_sessions_table import ChatSessionsTable, get_chat_sessions_table

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass(frozen=True)
class UserRuntimePreference:
    project_id: int | None = None
    model: str | None = None
    org_id: str | None = None


def _get_latest_org_id(
    user_id: str,
    *,
    table: ChatSessionsTable | None = None,
) -> str | None:
    """从 evo 会话表读取最近 org_id。

    fail-soft：org_id 只是飞书入口发起 run 时的上下文补全，DB 异常不应阻断
    飞书消息处理；Bohrium 显式需要 org/project 时后续运行闸口仍会校验。
    """
    uid = (user_id or "").strip()
    if not uid:
        return None
    table = table or get_chat_sessions_table()
    try:
        return table.get_latest_org_id_by_user(uid)
    except Error as exc:
        logger.warning(
            "get latest org_id failed user_id=%s error=%s",
            uid,
            exc,
            exc_info=True,
        )
        return None


def get_user_runtime_preference(
    user_id: str,
    *,
    table: ChatSessionsTable | None = None,
) -> UserRuntimePreference:
    user_level = get_user_level_runtime_preference(user_id)
    org_id = _get_latest_org_id(user_id, table=table)
    return UserRuntimePreference(
        project_id=user_level.project_id,
        model=user_level.model,
        org_id=org_id,
    )
