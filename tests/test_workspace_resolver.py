from pathlib import Path

from playground.mat_master.core.workspace_resolver import (
    get_remote_session_workspace_root,
    resolve_workspace_path,
)


def test_workspace_resolver_defaults_to_task_workspace(tmp_path: Path) -> None:
    run_dir = tmp_path / 'runs' / 'mat_master_web'
    resolution = resolve_workspace_path(
        run_dir,
        task_id='task_1',
        session_id='session_1',
        config_dict={},
        project_root=tmp_path,
    )

    assert resolution.mode == 'task'
    assert resolution.source == 'task'
    assert resolution.path == run_dir.resolve() / 'workspaces' / 'task_1'


def test_workspace_resolver_falls_back_to_default_workspace(tmp_path: Path) -> None:
    run_dir = tmp_path / 'runs' / 'mat_master_web'
    resolution = resolve_workspace_path(
        run_dir,
        task_id=None,
        session_id='session_1',
        config_dict={},
    )

    assert resolution.mode == 'task'
    assert resolution.source == 'default'
    assert resolution.path == run_dir.resolve() / 'workspace'


def test_remote_session_workspace_root_defaults_to_share_workspace(
    tmp_path: Path,
) -> None:
    resolved = get_remote_session_workspace_root({}, project_root=tmp_path)

    assert resolved == Path('/share/workspace')


def test_remote_session_workspace_root_supports_config_override(
    tmp_path: Path,
) -> None:
    resolved = get_remote_session_workspace_root(
        {'mat_master': {'remote_session_workspace_root': './remote-session-root'}},
        project_root=tmp_path,
    )

    assert resolved == (tmp_path / 'remote-session-root').resolve()
