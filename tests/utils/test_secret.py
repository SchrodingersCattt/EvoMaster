from __future__ import annotations

import importlib
import os

import pytest
from cryptography.fernet import Fernet


def _reload_secret(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> object:
    for key in tuple(os.environ):
        if key.startswith("MATMASTER_BYOK_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import src.utils.secret as secret

    return importlib.reload(secret)


def _enabled_env() -> dict[str, str]:
    return {
        "MATMASTER_BYOK_ENABLED": "true",
        "MATMASTER_BYOK_FERNET_KEY": Fernet.generate_key().decode(),
    }


def test_encrypt_decrypt_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _reload_secret(monkeypatch, _enabled_env())

    token = secret.encrypt("sk-test-secret")

    assert token != "sk-test-secret"
    assert secret.decrypt(token) == "sk-test-secret"


def test_hint_does_not_contain_full_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = _reload_secret(monkeypatch, _enabled_env())

    value = "sk-1234567890abcdef"
    key_hint = secret.hint(value)

    assert key_hint == "sk-...cdef"
    assert value not in key_hint
    assert "1234567890" not in key_hint


def test_enabled_requires_valid_fernet_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = _reload_secret(
        monkeypatch,
        {
            "MATMASTER_BYOK_ENABLED": "1",
            "MATMASTER_BYOK_FERNET_KEY": "not-a-fernet-key",
        },
    )

    assert secret.is_byok_enabled() is True
    with pytest.raises(secret.BYOKSecretError):
        secret.encrypt("sk-test-secret")


def test_disabled_secret_service_rejects_encrypt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = _reload_secret(
        monkeypatch,
        {
            "MATMASTER_BYOK_ENABLED": "false",
            "MATMASTER_BYOK_FERNET_KEY": Fernet.generate_key().decode(),
        },
    )

    assert secret.is_byok_enabled() is False
    with pytest.raises(secret.BYOKSecretError):
        secret.encrypt("sk-test-secret")


def test_key_version_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _enabled_env()
    env["MATMASTER_BYOK_FERNET_KEY_VERSION"] = "rotated-v2"
    secret = _reload_secret(monkeypatch, env)

    assert secret.current_key_version() == "rotated-v2"
