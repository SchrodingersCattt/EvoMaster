from __future__ import annotations

import os
from typing import Any

from .endpoints import get_bohrium_base_url
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
