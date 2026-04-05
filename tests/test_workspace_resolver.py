from pathlib import Path

from matmaster.integration.workspace_resolver import (
    get_remote_session_workspace_root,
)


def test_remote_session_workspace_root_defaults_to_share_workspace(
    tmp_path: Path,
) -> None:
    resolved = get_remote_session_workspace_root({}, project_root=tmp_path)

    assert resolved == Path('/share')


def test_remote_session_workspace_root_supports_config_override(
    tmp_path: Path,
) -> None:
    resolved = get_remote_session_workspace_root(
        {'mat_master': {'remote_session_workspace_root': './remote-session-root'}},
        project_root=tmp_path,
    )

    assert resolved == (tmp_path / 'remote-session-root').resolve()
