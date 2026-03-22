"""Bohrium 鉴权与 MCP calculation 用 storage/executor 配置。

供 MCP calculation path adaptor 使用，统一从环境变量（.env）读取 BOHRIUM_*，
生成 HTTPS storage 与注入 executor 的鉴权信息。与 _tmp/MatMaster 的 private_callback 对齐。
"""

from __future__ import annotations

import copy
import os
from typing import Any

from src.utils.constant import BOHRIUM_OPENAPI_HOST


def build_bohrium_skill_remote_env(session: Any) -> dict[str, str]:
    """从 ``session._bohrium_credentials`` 生成远程 skill 脚本所需的 ``BOHRIUM_*`` 环境变量。

    与 :class:`~evomaster.agent.tools.mcp.mcp.MCPTool`、`monitor_job` 使用同一凭证来源
    （由 ``apply_run_credentials_to_session`` 或 WebSocket 入口写入 session）。

    ``BOHRIUM_BASE_URL`` 取自 ``src.utils.constant.BOHRIUM_OPENAPI_HOST``（与节点服务、
    OpenAPI 调用一致：含按环境的默认 host，且可被 ``BOHRIUM_BASE_URL`` 环境变量覆盖）。

    Returns:
        非空时包含 ``BOHRIUM_ACCESS_KEY``、``BOHRIUM_PROJECT_ID``、``BOHRIUM_BASE_URL``；
        凭证缺失或无效时返回空 dict。
    """
    creds = getattr(session, '_bohrium_credentials', None)
    if not isinstance(creds, dict):
        return {}
    access_key = (creds.get('access_key') or '').strip()
    if not access_key:
        return {}
    pid = creds.get('project_id')
    if pid is None:
        return {}
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return {}
    return {
        'BOHRIUM_ACCESS_KEY': access_key,
        'BOHRIUM_PROJECT_ID': str(pid_int),
        'BOHRIUM_BASE_URL': BOHRIUM_OPENAPI_HOST,
    }


def get_bohrium_credentials(
    access_key: str | None = None,
    project_id: int | str | None = None,
    user_id: int | str | None = None,
) -> dict[str, Any]:
    """从环境变量读取 Bohrium 鉴权（.env 或 os.environ），或使用提供的参数。

    Args:
        access_key: 可选的 access_key，如果提供则优先使用
        project_id: 可选的 project_id，如果提供则优先使用
        user_id: 可选的 user_id，如果提供则优先使用

    Returns:
        包含 access_key, project_id, user_id 的字典
    """
    # 如果提供了参数，优先使用；否则从环境变量读取
    if access_key is None:
        access_key = os.getenv('BOHRIUM_ACCESS_KEY', '').strip()
    else:
        access_key = str(access_key).strip()

    if project_id is None:
        try:
            project_id = int(os.getenv('BOHRIUM_PROJECT_ID', '-1'))
        except (TypeError, ValueError):
            project_id = -1
    else:
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            project_id = -1

    if user_id is None:
        try:
            user_id = int(os.getenv('BOHRIUM_USER_ID', '-1'))
        except (TypeError, ValueError):
            user_id = -1
    else:
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            user_id = -1

    return {
        'access_key': access_key,
        'project_id': project_id,
        'user_id': user_id,
    }


def get_bohrium_storage_config(
    access_key: str | None = None,
    project_id: int | str | None = None,
    user_id: int | str | None = None,
) -> dict[str, Any]:
    """MCP calculation 用 HTTPS storage（type https + Bohrium plugin）。

    Args:
        access_key: 可选的 access_key，如果提供则优先使用
        project_id: 可选的 project_id，如果提供则优先使用
        user_id: 可选的 user_id（此函数不使用，但为了一致性保留）

    Returns:
        storage 配置字典
    """
    cred = get_bohrium_credentials(
        access_key=access_key, project_id=project_id, user_id=user_id
    )
    return {
        'type': 'https',
        'plugin': {
            'type': 'bohrium',
            'access_key': cred['access_key'],
            'project_id': cred['project_id'],
            'app_key': 'agent',
        },
    }


def inject_bohrium_executor(
    executor_template: dict[str, Any],
    access_key: str | None = None,
    project_id: int | str | None = None,
    user_id: int | str | None = None,
) -> dict[str, Any]:
    """深拷贝 executor 模板并注入 BOHRIUM_* 鉴权（与 MatMaster private_callback 一致）。

    Args:
        executor_template: executor 模板字典
        access_key: 可选的 access_key，如果提供则优先使用
        project_id: 可选的 project_id，如果提供则优先使用
        user_id: 可选的 user_id，如果提供则优先使用

    Returns:
        注入鉴权后的 executor 字典
    """
    executor = copy.deepcopy(executor_template)
    cred = get_bohrium_credentials(
        access_key=access_key, project_id=project_id, user_id=user_id
    )
    if executor.get('type') == 'dispatcher':
        rp = executor.setdefault('machine', {}).setdefault('remote_profile', {})
        rp['access_key'] = cred['access_key']
        rp['project_id'] = cred['project_id']
        rp['real_user_id'] = cred['user_id']
        resources = executor.setdefault('resources', {})
        envs = resources.setdefault('envs', {})
        envs['BOHRIUM_PROJECT_ID'] = cred['project_id']
    elif executor.get('type') == 'local':
        # bohr-agent-sdk LocalExecutor 使用 executor.env 作为运行时环境变量；仅传 ak 与 project_id
        env = executor.setdefault('env', {})
        env['BOHRIUM_PROJECT_ID'] = str(cred['project_id'])
        if cred.get('access_key'):
            env['BOHRIUM_ACCESS_KEY'] = str(cred['access_key'])
    return executor
