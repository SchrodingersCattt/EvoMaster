from __future__ import annotations

import os
from typing import Any

from .endpoints import get_bohrium_base_url, use_sandbox
from .errors import BohriumCredentialError
from .runtime import get_runtime
from .types import BohriumCredentials


def normalize_bohrium_credentials(values: dict[str, Any]) -> BohriumCredentials:
    normalized = dict(values)
    if not str(normalized.get("base_url") or "").strip():
        normalized["base_url"] = get_bohrium_base_url()
    return BohriumCredentials.from_mapping(normalized)


def credentials_from_env() -> BohriumCredentials | None:
    cred = normalize_bohrium_credentials(
        {
            "access_key": os.getenv("BOHRIUM_ACCESS_KEY"),
            "project_id": os.getenv("BOHRIUM_PROJECT_ID"),
            "user_id": os.getenv("BOHRIUM_USER_ID"),
            "user_no": os.getenv("BOHRIUM_USER_NO"),
            "base_url": os.getenv("BOHRIUM_BASE_URL"),
        }
    )
    return cred if cred.access_key else None


def build_bohrium_context(
    *,
    session: Any = None,
    require_project: bool = False,
):
    """Build a BohriumContext from runtime handle or environment."""
    from .types import BohriumContext

    runtime = get_runtime(session) if session is not None else None
    if runtime is not None:
        cred = runtime.credentials()
        source = "runtime"
    else:
        cred = credentials_from_env()
        if cred is None:
            raise BohriumCredentialError(
                "Bohrium credentials unavailable. Provide via session or BOHRIUM_ACCESS_KEY."
            )
        source = "env"

    ctx = BohriumContext.from_credentials(
        cred,
        sandbox=use_sandbox(),
        source=source,
    )
    if require_project and ctx.credentials.project_id <= 0:
        raise BohriumCredentialError(
            "Bohrium project ID unavailable. Provide via session or BOHRIUM_PROJECT_ID."
        )
    if not ctx.credentials.base_url:
        cred_with_url = BohriumCredentials(
            access_key=cred.access_key,
            project_id=cred.project_id,
            user_id=cred.user_id,
            user_no=cred.user_no,
            base_url=get_bohrium_base_url(),
        )
        ctx = BohriumContext(
            credentials=cred_with_url,
            sandbox=ctx.sandbox,
            credential_source=ctx.credential_source,
        )
    return ctx
