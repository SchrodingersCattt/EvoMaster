"""Tests for PlaygroundContext frozen model and WorkspaceArchivalConfig."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from matmaster.types.context import PlaygroundContext, WorkspaceArchivalConfig


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
            cache_area=Path("/tmp/cache"),
        )
        assert ctx.workdir == Path("/tmp/work")
        assert ctx.session_type == "docker"
        assert ctx.cache_area == Path("/tmp/cache")

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
        assert ctx.run_meta == {}
        assert ctx.archival is None

    def test_no_mcp_manager_field(self) -> None:
        """PlaygroundContext must not have mcp_manager field."""
        assert "mcp_manager" not in PlaygroundContext.model_fields

    def test_no_skill_registry_field(self) -> None:
        """PlaygroundContext must not have skill_registry field."""
        assert "skill_registry" not in PlaygroundContext.model_fields

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
            run_meta={"task_id": "t1"},
            archival=archival,
        )
        data = ctx.model_dump()
        assert isinstance(data, dict)
        assert "workdir" in data
        assert "session_type" in data
        assert "env_vars" in data
        assert "archival" in data

        restored = PlaygroundContext.model_validate(data)
        assert restored.workdir == ctx.workdir
        assert restored.session_type == ctx.session_type
        assert restored.env_vars == ctx.env_vars
        assert restored.run_meta == ctx.run_meta
        assert restored.archival is not None
        assert restored.archival.enabled is True
        assert restored.archival.oss_bucket == "b"

    def test_model_dump_roundtrip_no_archival(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            env_vars={"KEY": "val"},
            run_meta={"task_id": "t1"},
        )
        data = ctx.model_dump()
        restored = PlaygroundContext.model_validate(data)
        assert restored.workdir == ctx.workdir
        assert restored.archival is None

    def test_custom_env_vars(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            env_vars={"API_KEY": "secret"},
        )
        assert ctx.env_vars == {"API_KEY": "secret"}


class TestWithBohrium:
    """PlaygroundContext.with_bohrium() returns new frozen instance."""

    def test_with_bohrium_returns_new_instance_with_bohrium_in_run_meta(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
        )
        result = ctx.with_bohrium({"ssh_attached": True, "node_id": "abc"})
        assert result.run_meta["bohrium"] == {"ssh_attached": True, "node_id": "abc"}

    def test_with_bohrium_preserves_existing_run_meta(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            run_meta={"task_id": "t1", "extra": 42},
        )
        result = ctx.with_bohrium({"ssh_attached": False})
        assert result.run_meta["task_id"] == "t1"
        assert result.run_meta["extra"] == 42
        assert result.run_meta["bohrium"] == {"ssh_attached": False}

    def test_with_bohrium_does_not_mutate_original(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            run_meta={"task_id": "t1"},
        )
        _ = ctx.with_bohrium({"ssh_attached": True})
        assert "bohrium" not in ctx.run_meta
        assert ctx.run_meta == {"task_id": "t1"}


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
    """QUAL-01: with_bohrium preserves existing run_meta keys."""

    def test_playground_context_with_bohrium_preserves_existing_meta(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            run_meta={"key": "val", "number": 42},
        )
        result = ctx.with_bohrium({"ssh": True})
        # Both original and bohrium keys present
        assert result.run_meta["key"] == "val"
        assert result.run_meta["number"] == 42
        assert result.run_meta["bohrium"] == {"ssh": True}


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
