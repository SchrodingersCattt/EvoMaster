from __future__ import annotations

import json
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app import app
from src.dao.user_llm_config_table import get_user_llm_config_table
from src.services.byok_verifier import get_byok_verifier


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


@contextmanager
def _client():
    router = app.router
    saved = router.lifespan_context
    router.lifespan_context = _noop_lifespan
    try:
        yield TestClient(app)
    finally:
        router.lifespan_context = saved


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 12,
        "user_id": "user-1",
        "display_name": "Research Proxy",
        "base_url": "https://api.example.com/v1",
        "model": "model-a",
        "api_key_cipher": "cipher-token",
        "api_key_hint": "sk-...cdef",
        "key_version": "v1",
        "params": {"temperature": 0.2},
        "extra_body": {"metadata": {"team": "lab"}},
        "prompt_cache": {},
        "supports_streaming": True,
        "supports_tool_calling": True,
        "supports_vision": False,
        "verification_status": "unverified",
        "verification_error": None,
        "verified_at": None,
        "is_enabled": True,
        "version": 1,
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 6, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class FakeTable:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {12: _row()}
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.updated: list[tuple[str, int, dict[str, Any]]] = []
        self.deleted: list[tuple[str, int]] = []
        self.next_id = 20

    def create(self, user_id: str, **fields: Any) -> int:
        self.created.append((user_id, fields))
        config_id = self.next_id
        self.next_id += 1
        self.rows[config_id] = _row(id=config_id, user_id=user_id, **fields)
        return config_id

    def get(self, user_id: str, config_id: int) -> dict[str, Any] | None:
        row = self.rows.get(config_id)
        if row and row["user_id"] == user_id:
            return dict(row)
        return None

    def list_by_user(self, user_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values() if row["user_id"] == user_id]

    def update(self, user_id: str, config_id: int, **fields: Any) -> bool:
        self.updated.append((user_id, config_id, fields))
        row = self.rows.get(config_id)
        if not row or row["user_id"] != user_id:
            return False
        row.update(fields)
        row["version"] = int(row["version"]) + 1
        return True

    def delete(self, user_id: str, config_id: int) -> bool:
        self.deleted.append((user_id, config_id))
        row = self.rows.get(config_id)
        if not row or row["user_id"] != user_id:
            return False
        del self.rows[config_id]
        return True


class FakeVerifier:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {
            "status": "verified",
            "supports_streaming": True,
            "supports_tool_calling": True,
            "supports_vision": False,
            "error": None,
        }
        self.calls: list[dict[str, Any]] = []

    async def verify_unsaved(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.result)


class FakePolicy:
    def validate_base_url(self, raw_url: str) -> str:
        return raw_url.strip().rstrip("/")


def _install(
    monkeypatch,
    *,
    table: FakeTable | None = None,
    verifier: FakeVerifier | None = None,
) -> tuple[FakeTable, FakeVerifier]:
    from src.apis import byok_api

    fake_table = table or FakeTable()
    fake_verifier = verifier or FakeVerifier()
    app.dependency_overrides[get_user_llm_config_table] = lambda: fake_table
    app.dependency_overrides[get_byok_verifier] = lambda: fake_verifier
    monkeypatch.setattr(byok_api.secret, "is_byok_enabled", lambda: True)
    monkeypatch.setattr(byok_api.secret, "encrypt", lambda value: f"cipher:{value}")
    monkeypatch.setattr(byok_api.secret, "decrypt", lambda value: f"plain:{value}")
    monkeypatch.setattr(byok_api.secret, "hint", lambda _value: "sk-...cdef")
    monkeypatch.setattr(byok_api.secret, "current_key_version", lambda: "v9")
    monkeypatch.setattr(byok_api, "BYOKEndpointPolicy", FakePolicy)
    return fake_table, fake_verifier


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_user_llm_config_table, None)
    app.dependency_overrides.pop(get_byok_verifier, None)


def _assert_no_key_material(body: dict[str, Any]) -> None:
    text = json.dumps(body, ensure_ascii=False)
    assert '"api_key"' not in text
    assert "api_key_cipher" not in text
    assert "sk-1234567890abcdef" not in text
    assert "cipher:" not in text


def test_create_returns_safe_payload_and_encrypts(monkeypatch) -> None:
    table, _verifier = _install(monkeypatch)
    try:
        with _client() as client:
            response = client.post(
                "/api/v1/llm-configs",
                headers={"X-User-Id": "user-1"},
                json={
                    "display_name": "  Research Proxy  ",
                    "base_url": "https://api.example.com/v1/",
                    "model": "model-a",
                    "api_key": "sk-1234567890abcdef",
                },
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    body = response.json()
    _assert_no_key_material(body)
    assert body["data"]["id"] == 20
    created_user, created_fields = table.created[0]
    assert created_user == "user-1"
    assert created_fields["api_key_cipher"] == "cipher:sk-1234567890abcdef"
    assert created_fields["api_key_hint"] == "sk-...cdef"
    assert created_fields["key_version"] == "v9"


def test_list_returns_current_user_configs(monkeypatch) -> None:
    table = FakeTable()
    table.rows[13] = _row(id=13, user_id="other-user")
    _install(monkeypatch, table=table)
    try:
        with _client() as client:
            response = client.get(
                "/api/v1/llm-configs",
                headers={"X-User-Id": "user-1"},
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["id"] for item in data] == [12]
    _assert_no_key_material(response.json())


def test_byok_api_response_never_contains_cipher_or_plaintext(monkeypatch) -> None:
    table = FakeTable()
    table.rows[12] = _row(
        api_key_cipher="cipher:sk-1234567890abcdef",
        verification_error="provider error api_key=<redacted>",
    )
    _install(monkeypatch, table=table)
    try:
        with _client() as client:
            response = client.get(
                "/api/v1/llm-configs/12",
                headers={"X-User-Id": "user-1"},
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    _assert_no_key_material(response.json())


def test_patch_omitted_fields_are_not_updated(monkeypatch) -> None:
    table, _verifier = _install(monkeypatch)
    try:
        with _client() as client:
            response = client.patch(
                "/api/v1/llm-configs/12",
                headers={"X-User-Id": "user-1"},
                json={"display_name": "New Name"},
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    _user, _config_id, fields = table.updated[0]
    assert fields == {"display_name": "New Name"}
    _assert_no_key_material(response.json())


def test_patch_api_key_replaces_cipher_hint_and_key_version(monkeypatch) -> None:
    table, _verifier = _install(monkeypatch)
    try:
        with _client() as client:
            response = client.patch(
                "/api/v1/llm-configs/12",
                headers={"X-User-Id": "user-1"},
                json={"api_key": "sk-1234567890abcdef"},
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    fields = table.updated[0][2]
    assert fields == {
        "api_key_cipher": "cipher:sk-1234567890abcdef",
        "api_key_hint": "sk-...cdef",
        "key_version": "v9",
        "verification_status": "unverified",
        "verification_error": None,
        "verified_at": None,
    }
    _assert_no_key_material(response.json())


def test_delete_scopes_current_user(monkeypatch) -> None:
    table, _verifier = _install(monkeypatch)
    try:
        with _client() as client:
            response = client.delete(
                "/api/v1/llm-configs/12",
                headers={"X-User-Id": "user-1"},
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert table.deleted == [("user-1", 12)]


def test_unsaved_test_does_not_persist(monkeypatch) -> None:
    table, verifier = _install(monkeypatch)
    try:
        with _client() as client:
            response = client.post(
                "/api/v1/llm-configs/test",
                headers={"X-User-Id": "user-1"},
                json={
                    "base_url": "https://api.example.com/v1",
                    "model": "model-a",
                    "api_key": "sk-1234567890abcdef",
                    "supports_vision": True,
                },
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert table.created == []
    assert table.updated == []
    assert verifier.calls[0]["api_key"] == "sk-1234567890abcdef"
    _assert_no_key_material(response.json())


def test_saved_test_updates_verification_fields(monkeypatch) -> None:
    table, verifier = _install(monkeypatch)
    try:
        with _client() as client:
            response = client.post(
                "/api/v1/llm-configs/12/test",
                headers={"X-User-Id": "user-1"},
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    assert verifier.calls[0]["api_key"] == "plain:cipher-token"
    fields = table.updated[0][2]
    assert fields["verification_status"] == "verified"
    assert fields["verification_error"] is None
    assert fields["supports_streaming"] is True
    assert fields["supports_tool_calling"] is True
    assert fields["supports_vision"] is False
    assert fields["verified_at"] is not None
    _assert_no_key_material(response.json())


def test_saved_test_sanitizes_provider_error(monkeypatch) -> None:
    table = FakeTable()
    verifier = FakeVerifier(
        {
            "status": "failed",
            "supports_streaming": False,
            "supports_tool_calling": False,
            "supports_vision": False,
            "error": "provider error api_key=sk-1234567890abcdef",
        }
    )
    _install(monkeypatch, table=table, verifier=verifier)
    try:
        with _client() as client:
            response = client.post(
                "/api/v1/llm-configs/12/test",
                headers={"X-User-Id": "user-1"},
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200, response.text
    fields = table.updated[0][2]
    assert fields["verification_status"] == "failed"
    assert "sk-1234567890abcdef" not in fields["verification_error"]
    assert "api_key=<redacted>" in fields["verification_error"]
    _assert_no_key_material(response.json())
