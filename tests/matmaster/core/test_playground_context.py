"""Tests for WorkspaceArchivalConfig and Playground.prepare().

The old frozen god-object ``PlaygroundContext`` was split into honest boundary
types (``ExecutionEnvironment`` / ``AgentRunRequest`` / ``AgentRunContext``).
The contract coverage for those new types now lives in
``tests/matmaster/core/test_run_context.py`` and
``tests/matmaster/types/test_runtime_ports.py``. What remains here is the still
valid behavior that survived the split: ``WorkspaceArchivalConfig`` (unchanged)
and ``Playground.prepare()`` (now returning an ``ExecutionEnvironment``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from matmaster.core.playground import Playground, WorkspaceArchivalConfig
from matmaster.types.run_metadata import RunMetadata


class TestWorkspaceArchivalConfig:
    def test_frozen(self) -> None:
        cfg = WorkspaceArchivalConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = True

    def test_defaults(self) -> None:
        cfg = WorkspaceArchivalConfig()
        assert cfg.enabled is False
        assert cfg.oss_bucket == ""
        assert cfg.oss_prefix == ""
        assert cfg.credential_ref == ""

    def test_custom_values(self) -> None:
        cfg = WorkspaceArchivalConfig(
            enabled=True,
            oss_bucket="my-bucket",
            oss_prefix="runs/",
            credential_ref="env:aliyun-oss",
        )
        assert cfg.enabled is True
        assert cfg.oss_bucket == "my-bucket"
        assert cfg.oss_prefix == "runs/"
        assert cfg.credential_ref == "env:aliyun-oss"

    def test_roundtrip(self) -> None:
        cfg = WorkspaceArchivalConfig(
            enabled=True,
            oss_bucket="bucket",
            oss_prefix="prefix/",
            credential_ref="ref",
        )
        data = cfg.model_dump()
        restored = WorkspaceArchivalConfig.model_validate(data)
        assert restored == cfg


class TestPlaygroundPrepareSessionId:
    def test_prepare_keeps_session_id_out_of_metadata(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        playground = Playground(session_type="local")

        try:
            env = playground.prepare(
                RunMetadata(run_dir=str(run_dir), task_id="task-1"),
                session_id="sess-1",
            )
        finally:
            playground.cleanup()

        assert env.session_id == "sess-1"
        assert env.metadata.run_dir == str(run_dir)
        assert env.metadata.task_id == "task-1"
        assert "session_id" not in RunMetadata.model_fields
