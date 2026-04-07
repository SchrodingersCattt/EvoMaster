"""Environment-aware MCP config resolution."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_VAR = 'SERVICE_ENV'
_DEFAULT_ENV = 'prod'
_NON_PROD_ENVS = {'test', 'uat'}


def get_current_env() -> str:
    return os.getenv(_ENV_VAR, _DEFAULT_ENV)


def resolve_mcp_config_path(config_path: Path) -> Path:
    current_env = get_current_env()
    if current_env not in _NON_PROD_ENVS:
        return config_path

    stem = config_path.stem
    suffix = config_path.suffix
    env_path = config_path.with_name(f"{stem}.{current_env}{suffix}")

    if env_path.exists():
        logger.info(
            'SERVICE_ENV=%s -> switching MCP config: %s -> %s',
            current_env,
            config_path.name,
            env_path.name,
        )
        return env_path

    logger.warning(
        'SERVICE_ENV=%s but env config not found: %s; falling back to %s',
        current_env,
        env_path,
        config_path,
    )
    return config_path
