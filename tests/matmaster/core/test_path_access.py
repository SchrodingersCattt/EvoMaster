from __future__ import annotations

from pathlib import Path

from matmaster.core.path_access import derive_path_access_roots
from matmaster.core.playground import ExecutionEnvironment
from matmaster.types.runtime_ports import BohriumRuntimeSnapshot


def test_path_access_reads_typed_bohrium_snapshot() -> None:
    env = ExecutionEnvironment(
        workdir=Path("/tmp/work"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    ).with_bohrium(
        BohriumRuntimeSnapshot(
            remote_project_root="/share/project",
            remote_workspace_root="/share",
            ssh_attached=True,
            node_id=9,
        )
    )

    roots = derive_path_access_roots(env)

    assert ("/share/project", "runtime") in {(root.root, root.kind) for root in roots}
    assert ("/share/.matmaster", "project_runtime") in {
        (root.root, root.kind) for root in roots
    }
