"""Tests for PlaygroundContext frozen model and WorkspaceArchivalConfig."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from matmaster.core.playground import (
    Playground,
    PlaygroundContext,
    WorkspaceArchivalConfig,
)
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.runtime_ports import BohriumRuntimeSnapshot
from matmaster.types.session import Session


def test_interaction_bridge_is_accessible_but_excluded_from_dump() -> None:
    class _Bridge:
        def __repr__(self) -> str:
            return "<bridge-with-callbacks>"

    bridge = _Bridge()
    ctx = PlaygroundContext(
        workdir=Path("/tmp/work"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
        interaction_bridge=bridge,
    )

    assert ctx.interaction_bridge is bridge
    assert "interaction_bridge" not in ctx.model_dump()
    assert "interaction_bridge" not in ctx.model_dump(mode="json")


def test_playground_context_metadata_is_typed_runmetadata() -> None:
    ctx = PlaygroundContext(
        workdir=Path("/tmp/work"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )

    assert isinstance(ctx.metadata, RunMetadata)

    updated = ctx.with_updates(metadata={"task_id": "task-1"})

    assert updated is not ctx
    assert updated.metadata.task_id == "task-1"
    assert ctx.metadata.task_id == ""


def test_with_updates_metadata_rejects_unknown_fields() -> None:
    ctx = PlaygroundContext(
        workdir=Path("/tmp/work"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )

    with pytest.raises(ValueError, match="Unknown RunMetadata field"):
        ctx.with_updates(metadata={"ghost_field": "x"})


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


class TestPlaygroundContext:
    def test_instantiation_with_required_fields(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            session_id="sess-1",
            cache_area=Path("/tmp/cache"),
        )
        assert ctx.workdir == Path("/tmp/work")
        assert ctx.session_type == "docker"
        assert ctx.session_id == "sess-1"
        assert ctx.cache_area == Path("/tmp/cache")

    def test_playground_context_carries_explicit_session_id(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            session_id="sess-explicit",
            cache_area=Path("/tmp/cache"),
        )

        assert ctx.session_id == "sess-explicit"

    def test_execution_workdir_defaults_to_local_workdir(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )
        assert ctx.execution_workdir == str(ctx.workdir)

    def test_frozen_rejects_assignment(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
        )
        with pytest.raises(ValidationError):
            ctx.workdir = Path("/other")

    def test_default_factory_fields(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
        )
        assert ctx.env_vars == {}
        assert ctx.metadata == RunMetadata()
        assert ctx.archival is None

    def test_no_mcp_manager_field(self) -> None:
        """PlaygroundContext must not have mcp_manager field."""
        assert "mcp_manager" not in PlaygroundContext.model_fields

    def test_no_skill_registry_field(self) -> None:
        """PlaygroundContext must not have skill_registry field."""
        assert "skill_registry" not in PlaygroundContext.model_fields

    def test_playground_context_has_no_run_meta_dict_bag(self) -> None:
        assert "run_meta" not in PlaygroundContext.model_fields

    def test_archival_defaults_to_none(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )
        assert ctx.archival is None

    def test_archival_with_config(self) -> None:
        archival = WorkspaceArchivalConfig(
            enabled=True,
            oss_bucket="bucket",
            oss_prefix="prefix/",
            credential_ref="env:oss",
        )
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            archival=archival,
        )
        assert ctx.archival is not None
        assert ctx.archival.enabled is True
        assert ctx.archival.oss_bucket == "bucket"

    def test_model_dump_roundtrip(self) -> None:
        archival = WorkspaceArchivalConfig(
            enabled=True, oss_bucket="b", oss_prefix="p/", credential_ref="r"
        )
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            env_vars={"KEY": "val"},
            metadata=RunMetadata(task_id="t1"),
            archival=archival,
        )
        data = ctx.model_dump()
        assert isinstance(data, dict)
        assert "workdir" in data
        assert "session_type" in data
        assert "env_vars" in data
        assert "archival" in data
        assert "execution_workdir" in data

        restored = PlaygroundContext.model_validate(data)
        assert restored.workdir == ctx.workdir
        assert restored.session_type == ctx.session_type
        assert restored.env_vars == ctx.env_vars
        assert restored.metadata == ctx.metadata
        assert restored.execution_workdir == ctx.execution_workdir
        assert restored.archival is not None
        assert restored.archival.enabled is True
        assert restored.archival.oss_bucket == "b"

    def test_model_dump_roundtrip_no_archival(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            env_vars={"KEY": "val"},
            metadata=RunMetadata(task_id="t1"),
        )
        data = ctx.model_dump()
        restored = PlaygroundContext.model_validate(data)
        assert restored.workdir == ctx.workdir
        assert restored.execution_workdir == ctx.execution_workdir
        assert restored.archival is None

    def test_custom_env_vars(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            env_vars={"API_KEY": "secret"},
        )
        assert ctx.env_vars == {"API_KEY": "secret"}


class TestWithExecution:
    """PlaygroundContext.with_execution() returns new frozen instance."""

    def test_with_execution_returns_new_instance_and_does_not_mutate_original(
        self,
    ) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )
        sentinel = object()
        other = ctx.with_execution(
            session=sentinel,
            session_type="ssh",
            execution_workdir="/remote/exec",
        )
        assert other is not ctx
        assert other.session is sentinel
        assert other.session_type == "ssh"
        assert other.execution_workdir == "/remote/exec"
        assert ctx.session is None
        assert ctx.session_type == "local"
        assert ctx.execution_workdir == str(ctx.workdir)


class TestWithBohrium:
    """PlaygroundContext.with_bohrium() returns new frozen instance."""

    def test_with_bohrium_uses_typed_snapshot(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
        )
        result = ctx.with_bohrium(BohriumRuntimeSnapshot(ssh_attached=True, node_id=9))
        snapshot = result.runtime_ports.bohrium.snapshot

        assert snapshot is not None
        assert snapshot.ssh_attached is True
        assert snapshot.node_id == 9
        assert "bohrium" not in RunMetadata.model_fields

    def test_with_bohrium_preserves_existing_metadata_without_writing_bohrium(
        self,
    ) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            metadata=RunMetadata(task_id="t1", source="test"),
        )
        result = ctx.with_bohrium(BohriumRuntimeSnapshot(ssh_attached=False))
        assert result.metadata.task_id == "t1"
        assert result.metadata.source == "test"
        assert "bohrium" not in RunMetadata.model_fields

    def test_with_bohrium_does_not_mutate_original(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            metadata=RunMetadata(task_id="t1"),
        )
        _ = ctx.with_bohrium(BohriumRuntimeSnapshot(ssh_attached=True))
        assert "bohrium" not in RunMetadata.model_fields
        assert ctx.metadata == RunMetadata(task_id="t1")

    def test_with_bohrium_preserves_execution_workdir(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            execution_workdir="/custom/exec",
        )
        result = ctx.with_bohrium(BohriumRuntimeSnapshot(ssh_attached=True))
        assert result.execution_workdir == "/custom/exec"
        assert ctx.execution_workdir == "/custom/exec"


# ── Edge case tests (QUAL-01) ─────────────────────────


class TestPlaygroundContextFrozenRejectMutation:
    """QUAL-01: Attempt setattr on frozen instance -> ValidationError."""

    def test_playground_context_frozen_reject_mutation(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
        )
        with pytest.raises(ValidationError):
            ctx.workdir = Path("/other")
        with pytest.raises(ValidationError):
            ctx.session_type = "ssh"
        with pytest.raises(ValidationError):
            ctx.env_vars = {"NEW": "val"}


class TestPlaygroundContextWithBohriumPreservesExistingMeta:
    """QUAL-01: with_bohrium preserves existing metadata fields."""

    def test_playground_context_with_bohrium_preserves_existing_meta(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            metadata=RunMetadata(source="test", task_id="task-1"),
        )
        result = ctx.with_bohrium(BohriumRuntimeSnapshot(ssh_attached=True))
        assert result.metadata.source == "test"
        assert result.metadata.task_id == "task-1"
        assert "bohrium" not in RunMetadata.model_fields
        assert result.runtime_ports.bohrium.snapshot is not None


class TestPlaygroundContextEmptyArchival:
    """QUAL-01: archival=None accepted without error."""

    def test_playground_context_empty_archival(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            archival=None,
        )
        assert ctx.archival is None


class TestWorkspaceArchivalConfigDefaults:
    """QUAL-01: All defaults applied correctly."""

    def test_workspace_archival_config_defaults(self) -> None:
        cfg = WorkspaceArchivalConfig()
        assert cfg.enabled is False
        assert cfg.oss_bucket == ""
        assert cfg.oss_prefix == ""
        assert cfg.credential_ref == ""


class TestPlaygroundContextSessionAndConfigDir:
    """PlaygroundContext session and config_dir fields (D-09, D-10)."""

    def test_session_field_accepts_session_protocol(self) -> None:
        """session= accepting Session Protocol instance."""
        mock_session = MagicMock(spec=Session)
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            session=mock_session,
        )
        assert ctx.session is mock_session

    def test_config_dir_field(self) -> None:
        """config_dir=Path stores correctly as Path."""
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            config_dir=Path("/config"),
        )
        assert ctx.config_dir == Path("/config")

    def test_session_and_config_dir_default_none(self) -> None:
        """Both fields default to None when not provided."""
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
        )
        assert ctx.session is None
        assert ctx.config_dir is None

    def test_backward_compatible_construction(self) -> None:
        """Existing construction without session/config_dir still works."""
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            env_vars={"KEY": "val"},
            metadata=RunMetadata(task_id="t1"),
        )
        assert ctx.workdir == Path("/tmp/work")
        assert ctx.session_type == "docker"
        assert ctx.env_vars == {"KEY": "val"}
        assert ctx.session is None
        assert ctx.config_dir is None

    def test_model_dump_includes_session_and_config_dir(self) -> None:
        """model_dump works with Session Protocol object."""
        mock_session = MagicMock(spec=Session)
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            session=mock_session,
            config_dir=Path("/config"),
        )
        data = ctx.model_dump()
        assert "config_dir" in data
        assert "session" in data


class TestPlaygroundContextLLMProvider:
    """PlaygroundContext.llm_provider field — externally-determined capability."""

    def test_llm_provider_field_accepted(self) -> None:
        """llm_provider accepts an arbitrary object (LLMProvider instance)."""

        class MockLLMProvider:
            pass

        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            llm_provider=MockLLMProvider(),
        )
        assert ctx.llm_provider is not None

    def test_llm_provider_defaults_to_none(self) -> None:
        """llm_provider defaults to None when not provided."""
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
        )
        assert ctx.llm_provider is None


class TestPlaygroundContextRuntimePorts:
    def test_runtime_ports_default_exists_and_is_excluded_from_dump(self) -> None:
        from matmaster.types.runtime_ports import PlaygroundRuntimePorts

        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )

        assert isinstance(ctx.runtime_ports, PlaygroundRuntimePorts)
        assert ctx.runtime_ports.compaction.history is None
        assert "runtime_ports" not in ctx.model_dump()
        assert "runtime_ports" not in ctx.model_dump(mode="json")

    def test_with_updates_can_update_metadata_and_runtime_ports_together(
        self,
    ) -> None:
        from matmaster.types.figures import FigureUploadConfig
        from matmaster.types.runtime_ports import FigureUploadPort

        cfg = FigureUploadConfig(
            session_id="sess-1",
            task_id="task-1",
            asset_key_prefix="figures/sess-1/task-1",
            upload_bytes=lambda data, name: f"https://oss.example/{name}",
        )
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="ssh",
            cache_area=Path("/tmp/cache"),
            execution_workdir="/remote/work",
            env_vars={"A": "B"},
            metadata=RunMetadata(task_id="task-1"),
        )

        updated = ctx.with_updates(
            metadata={"source": "web"},
            runtime_ports={"figure_upload": FigureUploadPort(config=cfg)},
        )

        assert updated is not ctx
        assert updated.runtime_ports.figure_upload.config is cfg
        assert updated.metadata.source == "web"
        assert updated.workdir == ctx.workdir
        assert updated.session_type == "ssh"
        assert updated.execution_workdir == "/remote/work"
        assert updated.env_vars == {"A": "B"}
        assert updated.metadata.task_id == "task-1"

    def test_with_updates_runtime_ports_merges_single_field_only(self) -> None:
        from matmaster.types.figures import FigureUploadConfig
        from matmaster.types.runtime_ports import (
            EmptySessionEventHistory,
            FigureUploadPort,
            PlaygroundCompactionPort,
            PlaygroundRuntimePorts,
        )

        def child_sink(event) -> None:
            return None

        history = EmptySessionEventHistory()
        cfg = FigureUploadConfig(
            session_id="sess-1",
            task_id="task-1",
            asset_key_prefix="figures/sess-1/task-1",
            upload_bytes=lambda data, name: f"https://oss.example/{name}",
        )
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            runtime_ports=PlaygroundRuntimePorts(
                child_event_forward_sink=child_sink,
                compaction=PlaygroundCompactionPort(history=history),
            ),
        )

        updated = ctx.with_updates(
            runtime_ports={"figure_upload": FigureUploadPort(config=cfg)}
        )

        assert updated is not ctx
        assert updated.runtime_ports.figure_upload.config is cfg
        assert updated.runtime_ports.child_event_forward_sink is child_sink
        assert updated.runtime_ports.compaction.history is history

    def test_with_updates_runtime_ports_rejects_unknown_fields(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )

        with pytest.raises(ValueError, match="Unknown PlaygroundRuntimePorts field"):
            ctx.with_updates(runtime_ports={"ghost_port": object()})

    def test_model_validate_accepts_runtime_ports_dataclass(self) -> None:
        from matmaster.types.runtime_ports import PlaygroundRuntimePorts

        ports = PlaygroundRuntimePorts()

        ctx = PlaygroundContext.model_validate(
            {
                "workdir": Path("/tmp/work"),
                "session_type": "local",
                "cache_area": Path("/tmp/cache"),
                "runtime_ports": ports,
            }
        )

        assert ctx.runtime_ports == ports


class TestPlaygroundPrepareSessionId:
    def test_prepare_keeps_session_id_out_of_metadata(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        playground = Playground(session_type="local")

        try:
            ctx = playground.prepare(
                RunMetadata(run_dir=str(run_dir), task_id="task-1"),
                session_id="sess-1",
            )
        finally:
            playground.cleanup()

        assert ctx.session_id == "sess-1"
        assert ctx.metadata.run_dir == str(run_dir)
        assert ctx.metadata.task_id == "task-1"
        assert "session_id" not in RunMetadata.model_fields
