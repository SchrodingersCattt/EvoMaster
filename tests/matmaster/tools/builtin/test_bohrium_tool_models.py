from __future__ import annotations

import pytest

from matmaster.bohrium.types import BohriumCredentials
from matmaster.tools.builtin.bohrium_tool.errors import BohriumCredentialError
from matmaster.tools.builtin.bohrium_tool.models import BohriumContext


def test_bohrium_context_builds_from_bohrium_credentials() -> None:
    cred = BohriumCredentials(
        access_key="ak",
        project_id=42,
        user_id=7,
        user_no="U001",
        base_url="https://openapi.test.dp.tech",
    )

    ctx = BohriumContext.from_credentials(cred, sandbox=False)

    assert ctx.access_key == "ak"
    assert ctx.project_id == 42
    assert ctx.base_url == "https://openapi.test.dp.tech"
    assert ctx.credential_source == "runtime"
    assert ctx.sandbox is False
    assert ctx.user_id == 7
    assert ctx.user_no == "U001"


def test_context_from_credentials_rejects_missing_access_key() -> None:
    cred = BohriumCredentials(
        access_key="",
        project_id=42,
        user_id=None,
        user_no="",
        base_url="https://openapi.test.dp.tech",
    )

    with pytest.raises(BohriumCredentialError, match="BOHRIUM_ACCESS_KEY"):
        BohriumContext.from_credentials(cred, sandbox=False)
