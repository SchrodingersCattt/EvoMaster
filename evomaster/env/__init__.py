"""EvoMaster Env 模块

Env 是 EvoMaster 的环境组件，负责：
- 可执行沙盒（Docker）
- 集群调度（k8s、ray、skypilot）
- 资源管理
- Bohrium 鉴权（MCP calculation storage/executor，见 .bohrium）
"""

from __future__ import annotations

from .base import BaseEnv, EnvConfig
from .bohrium import (
    get_bohrium_credentials,
    get_bohrium_storage_config,
    inject_bohrium_executor,
)
from .docker import DockerEnv, DockerEnvConfig
from .local import LocalEnv, LocalEnvConfig
from .ssh import SSHEnv, SSHEnvConfig


# 解决 Pydantic 循环依赖问题：重建 EnvConfig 模型
# 确保 SessionConfig 子类已完全定义
def _rebuild_env_configs():
    """延迟重建 EnvConfig 模型以解决循环依赖"""
    try:
        pass

        DockerEnvConfig.model_rebuild()
        LocalEnvConfig.model_rebuild()
        SSHEnvConfig.model_rebuild()
    except Exception:
        pass


# 延迟执行重建，确保所有模块都已加载
_rebuild_env_configs()

__all__ = [
    'BaseEnv',
    'EnvConfig',
    'LocalEnv',
    'LocalEnvConfig',
    'DockerEnv',
    'DockerEnvConfig',
    'SSHEnv',
    'SSHEnvConfig',
    'get_bohrium_credentials',
    'get_bohrium_storage_config',
    'inject_bohrium_executor',
]
