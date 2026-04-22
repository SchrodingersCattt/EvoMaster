from __future__ import annotations

from matmaster_bohrium_transfer.security import redact_secrets
from matmaster_bohrium_transfer.version import (
    CAPABILITIES,
    PACKAGE_NAME,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    version_payload,
)


def test_version_payload_exposes_protocol_and_capabilities() -> None:
    payload = version_payload()

    assert payload["ok"] is True
    assert payload["package"] == PACKAGE_NAME
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert "zip_stored" in payload["capabilities"]
    assert "redacted_errors" in payload["capabilities"]
    assert sorted(payload["capabilities"]) == sorted(CAPABILITIES)


def test_redact_secrets_available_before_remote_runner_phase() -> None:
    redacted = redact_secrets(
        {
            "Authorization": "Bearer secret-token",
            "url": "https://store/api/download/a?token=secret-token",
        }
    )

    assert "secret-token" not in redacted
    assert "<redacted>" in redacted
