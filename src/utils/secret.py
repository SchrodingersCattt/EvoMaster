from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


class BYOKSecretError(RuntimeError):
    pass


def is_byok_enabled() -> bool:
    value = os.environ.get("MATMASTER_BYOK_ENABLED", "")
    return value.strip().lower() in _TRUTHY_VALUES


def current_key_version() -> str:
    return os.environ.get("MATMASTER_BYOK_FERNET_KEY_VERSION", "v1").strip() or "v1"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if not is_byok_enabled():
        raise BYOKSecretError("BYOK secret service is disabled.")
    raw_key = os.environ.get("MATMASTER_BYOK_FERNET_KEY", "").strip()
    if not raw_key:
        raise BYOKSecretError("MATMASTER_BYOK_FERNET_KEY is required.")
    try:
        return Fernet(raw_key.encode())
    except (TypeError, ValueError) as exc:
        raise BYOKSecretError("MATMASTER_BYOK_FERNET_KEY is invalid.") from exc


def encrypt(plaintext: str) -> str:
    if not plaintext:
        raise BYOKSecretError("Plaintext secret is required.")
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        raise BYOKSecretError("Encrypted secret token is required.")
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise BYOKSecretError("Encrypted secret token is invalid.") from exc


def hint(plaintext: str) -> str:
    value = (plaintext or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return f"...{value[-2:]}"
    prefix = value[:3]
    suffix = value[-4:]
    return f"{prefix}...{suffix}"
