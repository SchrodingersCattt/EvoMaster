"""Small helper contracts for Exp assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RootTurnRender:
    rendered_content: str


def resolve_skill_config_dir(raw_dir: str) -> Path:
    """Map legacy ``matmaster_config`` references onto this repo's ``config`` dir."""
    candidate = Path(raw_dir)
    if candidate.exists():
        return candidate
    if raw_dir == "matmaster_config":
        compat = Path("config")
        if compat.exists():
            return compat
    return candidate


def deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged
