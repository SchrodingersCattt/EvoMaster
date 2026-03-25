from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from matmaster.types.context import WorkspaceArchivalConfig
from src.services.agent_run_service import _build_workspace_upload_fn


def test_build_workspace_upload_fn_uses_session_level_prefix() -> None:
    archival_config = WorkspaceArchivalConfig(
        enabled=True,
        oss_prefix="matmaster_evo/chat_workspace/",
    )

    upload_fn = _build_workspace_upload_fn(archival_config)

    assert upload_fn is not None
    workspace_path = Path("/tmp/task-1")
    with patch("src.dao.oss_io.upload_dir_to_oss") as mock_upload:
        upload_fn("sess-1", "task-1", workspace_path)

    mock_upload.assert_called_once_with(
        workspace_path,
        "matmaster_evo/chat_workspace/sess-1",
    )
