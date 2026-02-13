"""Environment-aware MCP config resolution.

Reads ``SERVICE_ENV`` (default ``'prod'``) and, when it equals a non-prod
value (e.g. ``'test'`` or ``'uat'``), swaps the MCP config file path to the
corresponding ``*.{env}.json`` variant so that all calculation MCP servers
connect to the correct environment.

Usage (in playground ``_setup_mcp_tools``)::

    from evomaster.adaptors.calculation.env_config import resolve_mcp_config_path
    config_path = resolve_mcp_config_path(config_path)
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Environment variable used to determine the current deployment environment.
_ENV_VAR = "SERVICE_ENV"
_DEFAULT_ENV = "prod"

# Non-prod environments that have their own MCP config files.
_NON_PROD_ENVS = {"test", "uat"}


def get_current_env() -> str:
    """Return the current environment name from ``SERVICE_ENV`` (default ``'prod'``)."""
    return os.getenv(_ENV_VAR, _DEFAULT_ENV)


def resolve_mcp_config_path(config_path: Path) -> Path:
    """Return the environment-specific MCP config path if applicable.

    When ``SERVICE_ENV`` is a non-prod environment (``'test'`` or ``'uat'``),
    this function looks for a sibling file with ``.{env}.json`` suffix
    (e.g. ``mcp_config.test.json`` or ``mcp_config.uat.json``).
    If that file exists it is returned; otherwise the original *config_path*
    is returned unchanged.

    Args:
        config_path: Resolved (absolute) path to the default MCP config JSON.

    Returns:
        The env-specific config path when in a non-prod environment and the
        file exists, otherwise the original *config_path*.
    """
    current_env = get_current_env()
    if current_env not in _NON_PROD_ENVS:
        return config_path

    # Build env config path: mcp_config.json -> mcp_config.{env}.json
    stem = config_path.stem          # e.g. "mcp_config"
    suffix = config_path.suffix      # e.g. ".json"
    env_path = config_path.with_name(f"{stem}.{current_env}{suffix}")

    if env_path.exists():
        logger.info(
            "SERVICE_ENV=%s → switching MCP config: %s -> %s",
            current_env,
            config_path.name,
            env_path.name,
        )
        return env_path

    logger.warning(
        "SERVICE_ENV=%s but env config not found: %s; falling back to %s",
        current_env,
        env_path,
        config_path,
    )
    return config_path
