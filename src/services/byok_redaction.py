from __future__ import annotations

import re
from typing import Any

SECRET_FIELD_NAMES = {
    "api_key",
    "api_key_cipher",
    "authorization",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}

_REDACTED = "<redacted>"
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|api_key_cipher|authorization|secret|token|"
    r"access_token|refresh_token)\b(\s*[:=]\s*)(?:Bearer\s+)?[^\s,;}\]]+"
)
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b")


def redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in SECRET_FIELD_NAMES:
                out[key] = _REDACTED
            else:
                out[key] = redact_mapping(item)
        return out
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item) for item in value)
    return value


def redact_text(text: object) -> str:
    raw = str(text)

    def _replace_key_value(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}{_REDACTED}"

    redacted = _KEY_VALUE_RE.sub(_replace_key_value, raw)
    return _OPENAI_KEY_RE.sub(_REDACTED, redacted)


def sanitize_provider_error(error: object, *, max_chars: int = 512) -> str:
    if isinstance(error, (dict, list, tuple)):
        text = redact_text(redact_mapping(error))
    else:
        text = redact_text(error)
    if max_chars > 0 and len(text) > max_chars:
        if max_chars == 1:
            return "…"
        return f"{text[: max_chars - 1]}…"
    return text
