"""LLM profile validation helpers for chat enqueue paths."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from matmaster.config.loader import load_llm_config

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LLM_CONFIG_PATH = _PROJECT_ROOT / "config" / "llm_config.yaml"


class InvalidModelProfileError(ValueError):
    def __init__(self, profile_key: str, available_profiles: tuple[str, ...]) -> None:
        super().__init__(profile_key, available_profiles)
        self.profile_key = profile_key
        self.available_profiles = available_profiles


@lru_cache(maxsize=1)
def _available_llm_profiles() -> tuple[str, ...]:
    return tuple(load_llm_config(_LLM_CONFIG_PATH).profiles)


def validate_platform_model_profile(model: str | None) -> str | None:
    profile_key = (model or '').strip() or None
    if profile_key is None:
        return None
    available = _available_llm_profiles()
    if profile_key not in available:
        raise InvalidModelProfileError(profile_key, available)
    return profile_key


def resolve_trigger_model_profile(
    explicit_model: str | None,
    inherited_model: str | None,
    session_id: str,
    logger: logging.Logger,
) -> tuple[str | None, bool]:
    """Validate trigger model; invalid inherited values fall back to default."""
    if explicit_model is not None:
        try:
            return validate_platform_model_profile(explicit_model), False
        except InvalidModelProfileError as exc:
            logger.warning(
                "trigger explicit unknown llm profile rejected "
                "session_id=%s profile=%s available=%s",
                session_id,
                exc.profile_key,
                list(exc.available_profiles),
            )
            return None, True

    try:
        return validate_platform_model_profile(inherited_model), False
    except InvalidModelProfileError as exc:
        logger.warning(
            "trigger inherited unknown llm profile; falling back to default "
            "session_id=%s profile=%s available=%s",
            session_id,
            exc.profile_key,
            list(exc.available_profiles),
        )
        return None, False
