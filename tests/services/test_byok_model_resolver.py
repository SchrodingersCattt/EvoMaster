from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.services.byok_endpoint_policy import BYOKEndpointPolicyError
from src.services.byok_model_resolver import BYOKModelResolver, BYOKResolveError

_DEFAULT_ROW = object()


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 12,
        "user_id": "user-1",
        "display_name": "Research Proxy",
        "base_url": "https://api.example.com/v1",
        "model": "model-a",
        "api_key_cipher": "cipher-token",
        "api_key_hint": "sk-...cdef",
        "key_version": "v1",
        "params": {
            "temperature": 0.2,
            "max_tokens": 512,
            "seed": 42,
        },
        "extra_body": {"metadata": {"team": "lab"}},
        "prompt_cache": {},
        "supports_streaming": True,
        "supports_tool_calling": True,
        "supports_vision": True,
        "verification_status": "verified",
        "verification_error": None,
        "verified_at": None,
        "is_enabled": True,
        "version": 3,
        "created_at": None,
        "updated_at": None,
    }
    row.update(overrides)
    return row


@dataclass
class FakeTable:
    row: dict[str, object] | None
    get_calls: list[tuple[str, int]] = field(default_factory=list)
    get_for_run_calls: list[tuple[str, int]] = field(default_factory=list)

    def get(self, user_id: str, config_id: int) -> dict[str, object] | None:
        self.get_calls.append((user_id, config_id))
        return self.row

    def get_for_run(self, user_id: str, config_id: int) -> dict[str, object] | None:
        self.get_for_run_calls.append((user_id, config_id))
        return self.row


@dataclass
class FakePolicy:
    fail: bool = False
    calls: list[str] = field(default_factory=list)

    def validate_base_url(self, raw_url: str) -> str:
        self.calls.append(raw_url)
        if self.fail:
            raise BYOKEndpointPolicyError("unsafe endpoint")
        return raw_url.rstrip("/")


@dataclass
class FakeSecret:
    calls: list[str] = field(default_factory=list)

    def decrypt(self, token: str) -> str:
        self.calls.append(token)
        return "sk-decrypted"


def _resolver(
    *,
    row: dict[str, object] | None | object = _DEFAULT_ROW,
    policy: FakePolicy | None = None,
    secret: FakeSecret | None = None,
) -> tuple[BYOKModelResolver, FakeTable, FakePolicy, FakeSecret]:
    table = FakeTable(_row() if row is _DEFAULT_ROW else row)
    endpoint_policy = policy or FakePolicy()
    secret_module = secret or FakeSecret()
    return (
        BYOKModelResolver(
            table=table,
            endpoint_policy=endpoint_policy,
            secret_module=secret_module,
        ),
        table,
        endpoint_policy,
        secret_module,
    )


def test_preflight_returns_reference_without_secret() -> None:
    resolver, table, policy, secret = _resolver()

    resolved = resolver.resolve_for_preflight(
        user_id="user-1",
        config_id=12,
        mode="direct",
        has_images=False,
    )

    assert table.get_calls == [("user-1", 12)]
    assert policy.calls == ["https://api.example.com/v1"]
    assert secret.calls == []
    assert resolved.ref.config_id == 12
    assert resolved.ref.version == 3
    assert resolved.ref.model == "model-a"
    assert resolved.config.api_key_hint == "sk-...cdef"


def test_worker_resolve_decrypts_and_builds_profile() -> None:
    resolver, table, policy, secret = _resolver()

    resolved = resolver.resolve_for_worker_run(
        user_id="user-1",
        config_id=12,
        expected_version=3,
        mode="direct",
        has_images=True,
    )

    assert table.get_for_run_calls == [("user-1", 12)]
    assert policy.calls == ["https://api.example.com/v1"]
    assert secret.calls == ["cipher-token"]
    assert resolved.config_id == 12
    assert resolved.version == 3
    assert resolved.profile.provider == "openai"
    assert resolved.profile.model == "model-a"
    assert resolved.profile.api_key == "sk-decrypted"
    assert resolved.profile.base_url == "https://api.example.com/v1"
    assert resolved.profile.temperature == 0.2
    assert resolved.profile.max_tokens == 512
    assert resolved.profile.passthrough_params == {"seed": 42}
    assert resolved.profile.passthrough_extra_body == {"metadata": {"team": "lab"}}
    assert resolved.profile.timeout == 600
    assert resolved.profile.stream_timeout == 120
    assert resolved.profile.stream_idle_timeout == 60
    assert resolved.profile.max_retries == 2
    assert resolved.profile.retry_delay == 1.0
    assert resolved.profile.vision_detail == "high"


def test_fail_fast_when_missing_disabled_unverified_or_version_mismatch() -> None:
    cases = [
        (None, 3, 404, "byok_not_found"),
        (_row(is_enabled=False), 3, 400, "byok_disabled"),
        (_row(verification_status="failed"), 3, 400, "byok_unverified"),
        (_row(version=4), 3, 409, "byok_version_mismatch"),
    ]

    for row, expected_version, http_status, error_code in cases:
        resolver, _table, _policy, secret = _resolver(row=row)
        with pytest.raises(BYOKResolveError) as exc_info:
            resolver.resolve_for_worker_run(
                user_id="user-1",
                config_id=12,
                expected_version=expected_version,
                mode="direct",
                has_images=False,
            )
        assert exc_info.value.http_status == http_status
        assert exc_info.value.error_code == error_code
        assert secret.calls == []


def test_direct_and_planner_require_streaming_and_tool_calling() -> None:
    for mode in ("direct", "planner"):
        resolver, _table, _policy, _secret = _resolver(
            row=_row(supports_streaming=False)
        )
        with pytest.raises(BYOKResolveError) as exc_info:
            resolver.resolve_for_preflight(
                user_id="user-1",
                config_id=12,
                mode=mode,
                has_images=False,
            )
        assert exc_info.value.error_code == "byok_capability_missing"

        resolver, _table, _policy, _secret = _resolver(
            row=_row(supports_tool_calling=False)
        )
        with pytest.raises(BYOKResolveError) as exc_info:
            resolver.resolve_for_preflight(
                user_id="user-1",
                config_id=12,
                mode=mode,
                has_images=False,
            )
        assert exc_info.value.error_code == "byok_capability_missing"


def test_images_require_vision_support() -> None:
    resolver, _table, _policy, _secret = _resolver(row=_row(supports_vision=False))

    with pytest.raises(BYOKResolveError) as exc_info:
        resolver.resolve_for_preflight(
            user_id="user-1",
            config_id=12,
            mode="direct",
            has_images=True,
        )

    assert exc_info.value.error_code == "byok_vision_required"


def test_endpoint_policy_failure_is_mapped() -> None:
    resolver, _table, _policy, secret = _resolver(policy=FakePolicy(fail=True))

    with pytest.raises(BYOKResolveError) as exc_info:
        resolver.resolve_for_worker_run(
            user_id="user-1",
            config_id=12,
            expected_version=3,
            mode="direct",
            has_images=False,
        )

    assert exc_info.value.error_code == "byok_endpoint_not_allowed"
    assert exc_info.value.http_status == 400
    assert "unsafe endpoint" in exc_info.value.message
    assert secret.calls == []
