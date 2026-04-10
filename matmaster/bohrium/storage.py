from __future__ import annotations

from .types import BohriumCredentials


def build_storage(credentials: BohriumCredentials) -> dict[str, object]:
    return {
        "type": "https",
        "plugin": {
            "type": "bohrium",
            "access_key": credentials.access_key,
            "project_id": credentials.project_id,
            "app_key": "agent",
        },
    }
