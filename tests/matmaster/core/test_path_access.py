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


def test_planned_skill_roots_readable_while_deferred_session_is_cold() -> None:
    """冷态 deferred session 无 live 远端根，但规划的节点侧技能根要可读。"""
    from types import SimpleNamespace

    session = SimpleNamespace(
        remote_project_root=None,
        remote_skill_roots=[],
        remote_user_skills_root=None,
        planned_skill_root_map=(
            ("/app/matmaster/plugins", "/personal/.matmaster/plugins"),
            ("/app/matmaster/skills", "/personal/.matmaster/skills"),
        ),
    )
    env = ExecutionEnvironment(
        workdir=Path("/tmp/work"),
        session_type="bohrium-deferred",
        cache_area=Path("/tmp/cache"),
    ).model_copy(update={"session": session})

    kinds = {(root.root, root.kind) for root in derive_path_access_roots(env)}

    assert ("/personal/.matmaster/plugins", "skill") in kinds
    assert ("/personal/.matmaster/skills", "skill") in kinds
