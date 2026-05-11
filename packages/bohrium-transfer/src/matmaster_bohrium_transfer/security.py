from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

SECRET_KEYS = {"token", "access_key", "accessKey", "authorization", "Authorization"}
TOKEN_QUERY_RE = re.compile(r"(?i)(token|access_key|accessKey)=([^&\s]+)")
BEARER_RE = re.compile(r"(?i)(Bearer\s+)[^&\s]+")
PATH_TOKEN_RE = re.compile(r"(?<=/)[A-Za-z0-9_\-=]{24,}(?=/|$)")


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if str(key) in SECRET_KEYS else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        text = BEARER_RE.sub(r"\1<redacted>", value)
        text = TOKEN_QUERY_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
        return PATH_TOKEN_RE.sub("<redacted>", text)
    return value


def redact_secrets(value: Any) -> str:
    sanitized = _sanitize(value)
    if isinstance(sanitized, str):
        return sanitized
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)


def token_fingerprint(token: str, transfer_id: str) -> str:
    material = f"{transfer_id}:{token}".encode()
    return hashlib.sha256(material).hexdigest()


def secure_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
    except Exception:
        target.unlink(missing_ok=True)
        raise
