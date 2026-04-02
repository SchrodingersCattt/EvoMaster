"""Typed config accessors for matmaster.

Two independent entry points, each accepting a YAML path or a pre-loaded
dict.  These are the matmaster-specific counterpart to
``evomaster.config.ConfigManager`` -- both can coexist during migration.

Usage::

    # From YAML (independent entry)
    llm = load_llm_config("config/llm_config.yaml")

    # From ConfigManager dict (dual entry)
    llm = LLMConfig.model_validate(playground.config.llm)
"""

from __future__ import annotations

import logging
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


_logger = logging.getLogger(__name__)


def _load_base_system_prompt(exps_dir: Path) -> str:
    """Load system_prompt from _base.toml. Returns empty string if missing."""
    import tomllib

    base_path = exps_dir / "_base.toml"
    if not base_path.exists():
        _logger.warning(
            "_base.toml not found at %s, system_prompt will be empty", base_path
        )
        return ""
    with open(base_path, "rb") as f:
        base_raw = tomllib.load(f)
    return base_raw.get("system_prompt", "")


def load_base_system_prompt(*, exps_dir: Path | None = None) -> str:
    """Public helper: load system_prompt from _base.toml.

    Used by devshell and other entry points that hand-build ExpConfig
    without going through load_exp_config().
    """
    if exps_dir is None:
        exps_dir = Path(__file__).resolve().parent.parent / "exps"
    return _load_base_system_prompt(exps_dir)


def list_available_exps(
    *,
    exps_dir: Path | None = None,
) -> list[tuple[str, str]]:
    """Return (name, description) pairs for all non-reserved exp definitions.

    Scans ``matmaster/exps/*.toml``, skipping underscore-prefixed files.
    Used by SpawnTool to populate the exp_name enum and description.
    """
    import tomllib

    if exps_dir is None:
        exps_dir = Path(__file__).resolve().parent.parent / "exps"

    result: list[tuple[str, str]] = []
    for p in sorted(exps_dir.glob("*.toml")):
        if p.stem.startswith("_"):
            continue
        try:
            with open(p, "rb") as f:
                raw = tomllib.load(f)
            name = raw.get("name", p.stem)
            desc = raw.get("description", "")
            result.append((name, desc))
        except Exception:
            _logger.warning("Failed to read exp definition: %s", p, exc_info=True)
    return result


def load_exp_config(
    name: str,
    *,
    exps_dir: Path | None = None,
) -> ExpConfig:
    """Load ``matmaster/exps/{name}.toml`` into ``ExpConfig``.

    Automatically merges system_prompt from ``_base.toml`` (if present).
    Exp-level system_prompt overrides the base value.

    Raises:
        FileNotFoundError: If no toml file matches *name*.
        ValueError: If *name* starts with underscore (reserved).
    """
    import tomllib

    if exps_dir is None:
        exps_dir = Path(__file__).resolve().parent.parent / "exps"

    if name.startswith("_"):
        raise ValueError(
            f"Exp name '{name}' is reserved (underscore prefix). "
            f"Use load_base_system_prompt() to access _base.toml."
        )

    toml_path = exps_dir / f"{name}.toml"
    if not toml_path.exists():
        available = sorted(
            p.stem for p in exps_dir.glob("*.toml") if not p.stem.startswith("_")
        )
        raise FileNotFoundError(
            f"Exp definition not found: {toml_path}, " f"available: {available}"
        )

    with open(toml_path, "rb") as f:
        raw = tomllib.load(f)

    # Load base system_prompt (exp can override)
    base_system_prompt = _load_base_system_prompt(exps_dir)

    # Preserve prompt fields verbatim (avoid ${...} misexpansion)
    system_prompt = raw.pop("system_prompt", base_system_prompt)
    dev_instr = raw.pop("developer_instructions", "")
    raw = _expand_env_vars(raw)
    raw["system_prompt"] = system_prompt
    raw["developer_instructions"] = dev_instr
    return ExpConfig.model_validate(raw)
