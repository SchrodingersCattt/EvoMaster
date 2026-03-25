"""MatMaster 配置里首个 agent 块与 prompt 文件路径解析（生产 run + 本地 Web run 共用）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def get_first_agent_config(config_dict: dict[str, Any]) -> dict[str, Any]:
    """从 ``pg.config.model_dump()`` 中取第一个 agent 的配置 dict（``agents`` 优先，否则 ``agent``）。"""
    agents_block = config_dict.get('agents')
    if isinstance(agents_block, dict) and agents_block:
        agent_config = next(iter(agents_block.values()))
    else:
        agent_config = config_dict.get('agent') or {}
    if not isinstance(agent_config, dict):
        return {}
    return agent_config


def resolve_mat_master_prompt_files(
    config_dir: Path | str,
    agent_config: dict[str, Any],
) -> tuple[str | None, str | None, dict[str, Any]]:
    """解析 ``system_prompt_file`` / ``user_prompt_file``（相对路径相对 ``config_dir``→``playground``）及 ``prompt_format_kwargs``。"""
    system_prompt_file = agent_config.get('system_prompt_file')
    user_prompt_file = agent_config.get('user_prompt_file')
    playground_base = Path(str(config_dir).replace('configs', 'playground'))
    if system_prompt_file:
        p = Path(system_prompt_file)
        if not p.is_absolute():
            system_prompt_file = str((playground_base / p).resolve())
    if user_prompt_file:
        p = Path(user_prompt_file)
        if not p.is_absolute():
            user_prompt_file = str((playground_base / p).resolve())
    raw = agent_config.get('prompt_format_kwargs', {})
    prompt_format_kwargs: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return system_prompt_file, user_prompt_file, prompt_format_kwargs
