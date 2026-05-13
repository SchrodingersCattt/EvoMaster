"""Runtime path access root derivation."""

from __future__ import annotations

import posixpath
from typing import Any

from matmaster.types.context import PlaygroundContext
from matmaster.types.topology import PathAccessRoot


def derive_path_access_roots(ctx: PlaygroundContext) -> tuple[PathAccessRoot, ...]:
    """Derive extra read/search roots exposed by the runtime.

    The session workspace remains the primary writable root. Extra roots are for
    runtime-owned locations outside that workspace, such as the remote skill
    mirror exposed through SkillTool and project-level ``.matmaster`` state
    under a Bohrium shared workspace.
    """
    roots: list[PathAccessRoot] = []
    workspace_root = posixpath.normpath(str(ctx.execution_workdir))
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

    session = getattr(ctx, "session", None)
    _add(getattr(session, "remote_project_root", None), "runtime")
    remote_skill_roots = getattr(session, "remote_skill_roots", None)
    if isinstance(remote_skill_roots, (list, tuple, set)):
        for root in remote_skill_roots:
            _add(root, "skill")
    _add(getattr(session, "remote_user_skills_root", None), "skill")

    run_meta = getattr(ctx, "run_meta", {}) or {}
    bohrium = run_meta.get("bohrium") if isinstance(run_meta, dict) else None
    if isinstance(bohrium, dict):
        _add(bohrium.get("remote_project_root"), "runtime")
        remote_workspace_root = bohrium.get("remote_workspace_root")
        if isinstance(remote_workspace_root, str) and remote_workspace_root.strip():
            _add(
                posixpath.join(remote_workspace_root, ".matmaster"),
                "project_runtime",
            )

    return tuple(roots)
