from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.tools.builtin.bohrium_tool.errors import BohriumPathError
from matmaster.tools.builtin.bohrium_tool.paths import (
    resolve_download_target,
    resolve_input_source,
)
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import FakeRemoteSession


def test_resolve_input_source_collapses_relative_local_path(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()

    source = resolve_input_source(
        raw_path="inputs",
        workdir=tmp_path,
        session=None,
    )

    assert source.kind == "local_dir"
    assert source.resolved_path == str(input_dir)


def test_resolve_input_source_rejects_missing_remote_session() -> None:
    with pytest.raises(BohriumPathError, match="remote session"):
        resolve_input_source(
            raw_path="/share/job-inputs",
            workdir=Path("/tmp"),
            session=None,
        )


def test_resolve_download_target_uses_staged_upload_for_remote_share(
    tmp_path: Path,
) -> None:
    session = FakeRemoteSession(existing_paths={"/share/results"}, is_open=True)

    target = resolve_download_target(
        raw_path="/share/results",
        workdir=tmp_path,
        session=session,
    )

    assert target.kind == "remote_share_dir"
    assert target.resolved_path == "/share/results"
    assert target.publish_mode == "staged_upload"
    assert target.staging_dir != Path("/share/results")
