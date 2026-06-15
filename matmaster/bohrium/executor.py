from __future__ import annotations

import copy
from typing import Any

from .env import build_trace_env
from .types import BohriumCredentials


def build_executor(
    template: dict[str, Any] | None,
    credentials: BohriumCredentials,
) -> dict[str, Any] | None:
    if template is None:
        return None

    executor = copy.deepcopy(template)
    if executor.get("type") == "dispatcher":
        remote_profile = executor.setdefault("machine", {}).setdefault(
            "remote_profile", {}
        )
        remote_profile["access_key"] = credentials.access_key
        remote_profile["project_id"] = credentials.project_id
        remote_profile["real_user_id"] = credentials.user_id or -1
        resources = executor.setdefault("resources", {})
        envs = resources.setdefault("envs", {})
        envs.update(build_trace_env())
        envs["BOHRIUM_PROJECT_ID"] = credentials.project_id
    elif executor.get("type") == "local":
        env = executor.setdefault("env", {})
        env.update(build_trace_env())
        env["BOHRIUM_PROJECT_ID"] = str(credentials.project_id)
        env["BOHRIUM_ACCESS_KEY"] = credentials.access_key
    return executor
