from __future__ import annotations

import pytest

from matmaster.integration.runtime_bridge.models import ResolvedCredential
from matmaster.tools.builtin.bohrium_tool.errors import BohriumCredentialError
from matmaster.tools.builtin.bohrium_tool.models import BohriumContext


def test_context_from_resolved_credential_captures_source_and_sandbox() -> None:
    cred = ResolvedCredential(
        service="bohrium",
        source="session",
        values={
            "access_key": "ak-123",
            "project_id": 42,
            "base_url": "https://openapi.test.dp.tech",
            "user_id": 7,
            "user_no": "U001",
        },
    )

    ctx = BohriumContext.from_resolved_credential(cred, sandbox=True)

    assert ctx.access_key == "ak-123"
    assert ctx.project_id == 42
    assert ctx.base_url == "https://openapi.test.dp.tech"
    assert ctx.credential_source == "session"
    assert ctx.sandbox is True
    assert ctx.user_id == 7
    assert ctx.user_no == "U001"


def test_context_from_resolved_credential_rejects_missing_access_key() -> None:
    cred = ResolvedCredential(service="bohrium", source="none", values={})

    with pytest.raises(BohriumCredentialError, match="BOHRIUM_ACCESS_KEY"):
        BohriumContext.from_resolved_credential(cred, sandbox=False)
