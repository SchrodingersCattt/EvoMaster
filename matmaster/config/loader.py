"""Typed config accessors for matmaster.

Two independent entry points, each accepting a YAML path or a pre-loaded
dict.  These are the matmaster-specific counterpart to
``evomaster.config.ConfigManager`` -- both can coexist during migration.

Usage::

    # From YAML (independent entry)
    llm = load_llm_config("matmaster_config/config.yaml")

    # From ConfigManager dict (dual entry)
    llm = LLMConfig.model_validate(playground.config.llm)
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .exp import ExpConfig
from .llm import LLMConfig

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ``${VAR}`` patterns in strings."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def _load_raw(source: dict[str, Any] | str | Path) -> dict[str, Any]:
    """Load YAML file or pass through dict, with env-var expansion."""
    if isinstance(source, dict):
        return _expand_env_vars(source)

    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    import yaml

    with open(path) as f:
        data = yaml.safe_load(f)

    return _expand_env_vars(data)


def load_llm_config(source: dict[str, Any] | str | Path) -> LLMConfig:
    """Load the ``llm`` section from YAML file or dict into ``LLMConfig``.

    Args:
        source: YAML file path (str or Path) or a pre-loaded config dict.
            When a dict is passed, it should be the full config (with an
            ``llm`` key) or the ``llm`` section itself.

    Returns:
        Validated ``LLMConfig`` with typed profile access.
    """
    raw = _load_raw(source)
    llm_section = raw.get("llm", raw)
    return LLMConfig.model_validate(llm_section)


def load_exp_config(
    name: str,
    *,
    exps_dir: Path | None = None,
) -> ExpConfig:
    """Load ``matmaster/exps/{name}.toml`` into ``ExpConfig``.

    Args:
        name: Exp definition name (matches toml filename without extension).
        exps_dir: Override directory to search for toml files.
            Defaults to ``matmaster/exps/`` relative to this package.

    Returns:
        Validated ``ExpConfig``.

    Raises:
        FileNotFoundError: If no toml file matches *name*.
    """
    import tomllib

    if exps_dir is None:
        exps_dir = Path(__file__).resolve().parent.parent / "exps"

    toml_path = exps_dir / f"{name}.toml"
    if not toml_path.exists():
        available = sorted(p.stem for p in exps_dir.glob("*.toml"))
        raise FileNotFoundError(
            f"Exp definition not found: {toml_path}, "
            f"available: {available}"
        )

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    # Preserve developer_instructions verbatim (avoid ${...} misexpansion)
    dev_instr = raw.pop("developer_instructions", "")
    raw = _expand_env_vars(raw)
    raw["developer_instructions"] = dev_instr
    return ExpConfig.model_validate(raw)
