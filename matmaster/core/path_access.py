"""Runtime path access root derivation."""

from __future__ import annotations

import posixpath
from typing import Any

from matmaster.core.playground import ExecutionEnvironment
from matmaster.types.topology import PathAccessRoot


def derive_path_access_roots(
    env: ExecutionEnvironment,
) -> tuple[PathAccessRoot, ...]:
    """Derive extra read/search roots exposed by the runtime.

    The session workspace remains the primary writable root. Extra roots are for
    runtime-owned locations outside that workspace, such as the remote skill
    mirror exposed through SkillTool and project-level ``.matmaster`` state
    under a Bohrium shared workspace.
    """
    roots: list[PathAccessRoot] = []
    workspace_root = posixpath.normpath(str(env.execution_workdir))
    seen = {workspace_root}
    read_search = frozenset({"read", "search"})

    def _add(raw_root: Any, kind: str) -> None:
        if not isinstance(raw_root, str):
            return
        stripped = raw_root.strip()
        if not stripped:
            return
        normalized = posixpath.normpath(stripped)
        if normalized == "." or normalized in seen:
            return
        roots.append(
            PathAccessRoot(
                root=normalized,
                kind=kind,
                permissions=read_search,
            )
        )
        seen.add(normalized)

    session = getattr(env, "session", None)
    _add(getattr(session, "remote_project_root", None), "runtime")
    remote_skill_roots = getattr(session, "remote_skill_roots", None)
    if isinstance(remote_skill_roots, (list, tuple, set)):
        for root in remote_skill_roots:
            _add(root, "skill")
    _add(getattr(session, "remote_user_skills_root", None), "skill")
    # Deferred Bohrium sessions expose no live remote roots while cold, but
    # SkillTool already renders paths under the planned Node-side roots.
    planned_map = getattr(session, "planned_skill_root_map", None)
    if isinstance(planned_map, (list, tuple)):
        for pair in planned_map:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                _add(pair[1], "skill")

    snapshot = env.bohrium.snapshot
    if snapshot is not None:
        _add(snapshot.remote_project_root, "runtime")
        if snapshot.remote_workspace_root:
            _add(
                posixpath.join(snapshot.remote_workspace_root, ".matmaster"),
                "project_runtime",
            )

    return tuple(roots)
