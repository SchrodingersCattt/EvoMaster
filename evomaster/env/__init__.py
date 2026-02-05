"""EvoMaster 环境管理

提供本地（Local）与 Docker 两种执行环境，供 Session 层调用。
"""

from evomaster.env.local import LocalEnv, LocalEnvConfig
from evomaster.env.docker import (
    DockerEnv,
    DockerEnvConfig,
    PS1_PATTERN,
    BashMetadata,
)

__all__ = [
    "LocalEnv",
    "LocalEnvConfig",
    "DockerEnv",
    "DockerEnvConfig",
    "PS1_PATTERN",
    "BashMetadata",
]
