"""Ask-human 配置与 ``ConfirmationManager`` 挂接（生产 run + 本地 Web run 共用）。"""

from __future__ import annotations

from typing import Any, Callable


def get_ask_human_config_dict(config_dict: dict[str, Any]) -> dict[str, Any]:
    """从 ``pg.config.model_dump()`` 取 ``mat_master.ask_human`` 块。"""
    mat_master_block = (
        config_dict.get('mat_master') if isinstance(config_dict, dict) else None
    )
    ah_cfg = (
        mat_master_block.get('ask_human') if isinstance(mat_master_block, dict) else {}
    ) or {}
    return ah_cfg if isinstance(ah_cfg, dict) else {}


def attach_ask_human_on_agent(
    agent: Any,
    reply_queue: Any,
    event_callback: Callable[..., None],
    ah_cfg: dict[str, Any],
) -> None:
    """当 ``reply_queue`` 非空时设置 ``_ask_human_queue`` / ``_ask_human_config`` / ``_confirm_manager``。"""
    if reply_queue is None:
        return
    agent._ask_human_queue = reply_queue
    try:
        from playground.mat_master.service.confirm import ConfirmationManager

        agent._ask_human_config = ah_cfg
        agent._confirm_manager = ConfirmationManager(
            emitter=event_callback,
            reply_queue=reply_queue,
            default_timeout_sec=ah_cfg.get('timeout_seconds', 20),
        )
    except Exception:
        pass
