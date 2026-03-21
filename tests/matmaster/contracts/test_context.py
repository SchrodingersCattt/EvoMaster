"""Tests for PlaygroundContext frozen model."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from matmaster.contracts.context import PlaygroundContext


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
        assert ctx.mcp_manager is None
        assert ctx.skill_registry is None

    def test_model_dump_roundtrip(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="docker",
            cache_area=Path("/tmp/cache"),
            env_vars={"KEY": "val"},
            run_meta={"task_id": "t1"},
        )
        data = ctx.model_dump()
        assert isinstance(data, dict)
        assert "workdir" in data
        assert "session_type" in data
        assert "env_vars" in data

        restored = PlaygroundContext.model_validate(data)
        assert restored.workdir == ctx.workdir
        assert restored.session_type == ctx.session_type
        assert restored.env_vars == ctx.env_vars
        assert restored.run_meta == ctx.run_meta

    def test_custom_env_vars(self) -> None:
        ctx = PlaygroundContext(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            env_vars={"API_KEY": "secret"},
        )
        assert ctx.env_vars == {"API_KEY": "secret"}
