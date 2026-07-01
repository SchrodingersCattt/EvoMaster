"""用户级程序化 trigger 偏好判断。"""

from __future__ import annotations

import logging

from clients.matmaster_platform.runtime_preference import (
    get_user_level_runtime_preference,
)

logger = logging.getLogger(__name__)


def is_programmatic_trigger_enabled(user_id: str) -> bool:
    """程序化 trigger 会发起新 run，必须由用户偏好显式允许。"""
    try:
        preference = get_user_level_runtime_preference(user_id)
    except Exception:
        logger.warning(
            "programmatic trigger preference lookup failed user_id=%s",
            user_id,
            exc_info=True,
        )
        return False
    return preference.programmatic_trigger_enabled is True
