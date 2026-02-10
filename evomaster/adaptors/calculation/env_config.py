"""Environment-aware MCP config resolution.

Reads ``SERVICE_ENV`` (default ``'prod'``) and, when it equals ``'test'``,
swaps the MCP config file path to the ``*.test.json`` variant so that all
calculation MCP servers connect to the test environment.

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


def get_current_env() -> str:
    """Return the current environment name from ``SERVICE_ENV`` (default ``'prod'``)."""
    return os.getenv(_ENV_VAR, _DEFAULT_ENV)


def resolve_mcp_config_path(config_path: Path) -> Path:
    """Return the environment-specific MCP config path if applicable.

    When ``SERVICE_ENV`` is ``'test'``, this function looks for a
    sibling file with ``.test.json`` suffix (e.g. ``mcp_config.test.json``).
    If that file exists it is returned; otherwise the original *config_path*
    is returned unchanged.

    Args:
        config_path: Resolved (absolute) path to the default MCP config JSON.

    Returns:
        The test config path when in test environment and the file exists,
        otherwise the original *config_path*.
    """
    current_env = get_current_env()
    if current_env != "test":
        return config_path

    # Build test config path: mcp_config.json -> mcp_config.test.json
    stem = config_path.stem          # e.g. "mcp_config"
    suffix = config_path.suffix      # e.g. ".json"
    test_path = config_path.with_name(f"{stem}.test{suffix}")

    if test_path.exists():
        logger.info(
            "SERVICE_ENV=%s → switching MCP config: %s -> %s",
            current_env,
            config_path.name,
            test_path.name,
        )
        return test_path

    logger.warning(
        "SERVICE_ENV=%s but test config not found: %s; falling back to %s",
        current_env,
        test_path,
        config_path,
    )
    return config_path
