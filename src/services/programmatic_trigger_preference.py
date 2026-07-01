"""用户级程序化 trigger 偏好判断。"""

from __future__ import annotations

import logging
from collections.abc import Callable

from clients.matmaster_platform.runtime_preference import (
    UserLevelRuntimePreference,
    get_user_level_runtime_preference,
)

logger = logging.getLogger(__name__)


def get_programmatic_trigger_enabled_state(
    user_id: str,
    *,
    preference_getter: Callable[[str], UserLevelRuntimePreference] | None = None,
) -> bool | None:
    """读取程序化 trigger 偏好三态。

    返回值语义：
    - True：用户显式允许自动继续分析；
    - False：用户未开启或显式关闭；
    - None：偏好不可用，应跳过但不要 ack 历史 pending terminal。
    """
    getter = preference_getter or get_user_level_runtime_preference
    try:
        preference = getter(user_id)
    except Exception:
        logger.warning(
            "programmatic trigger preference lookup failed user_id=%s",
            user_id,
            exc_info=True,
        )
        return None
    if not getattr(preference, "loaded", False):
        return None
    return preference.programmatic_trigger_enabled is True


def is_programmatic_trigger_enabled(user_id: str) -> bool:
    """程序化 trigger 会发起新 run，必须由用户偏好显式允许。"""
    return get_programmatic_trigger_enabled_state(user_id) is True
