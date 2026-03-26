"""Config loader -- YAML config loading utility.

Loads experiment config from a YAML file path or passes through a dict.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any


def load_config(source: dict[str, Any] | str | Path) -> dict[str, Any]:
    """Load experiment config from a file path or dict.

    Args:
        source: A dict (pass-through), a Path, or a string path to a YAML file.
            String paths are expanded (~ -> home dir) and resolved to absolute.

    Returns:
        Config dict.

    Raises:
        FileNotFoundError: If the file path does not exist.
    """
    if isinstance(source, dict):
        return source

    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    import yaml
    with open(path) as f:
        return yaml.safe_load(f)
