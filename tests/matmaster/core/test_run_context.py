"""Tests for the agent run boundary contract objects.

The runtime boundary is represented by three contract types:

  * ``ExecutionEnvironment`` -- the physical execution substrate produced by
    ``Playground.prepare()`` (workspace, session, cache, archival, bohrium
    snapshot). Holds a *live* session handle, so it is the authoritative
    environment object, not a throwaway "snapshot".
  * ``AgentRunRequest`` -- the per-run runtime ingredients the service layer
    resolves and hands to Exp (llm provider, turn input, user instructions,
    active skills, interaction bridge, runtime ports).
  * ``AgentRunContext`` -- the composition ``environment + request`` that Exp
    consumes as a single argument.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from matmaster.core.playground import ExecutionEnvironment, WorkspaceArchivalConfig
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.runtime_ports import (
    AgentRunPorts,
    BohriumRuntimePort,
    BohriumRuntimeSnapshot,
    FigureUploadPort,
)
from matmaster.types.session import Session

# ── ExecutionEnvironment ─────────────────────────────────────────


class TestExecutionEnvironment:
    def test_instantiation_with_required_physical_fields(self) -> None:
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="local",
            session_id="sess-1",
            cache_area=Path("/tmp/cache"),
        )
        assert env.workdir == Path("/tmp/work")
        assert env.session_type == "local"
        assert env.session_id == "sess-1"
        assert env.cache_area == Path("/tmp/cache")

    def test_execution_workdir_defaults_to_workdir(self) -> None:
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )
        assert env.execution_workdir == str(env.workdir)

    def test_frozen_rejects_assignment(self) -> None:
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )
        with pytest.raises(ValidationError):
            env.workdir = Path("/other")

    def test_carries_slimmed_run_metadata(self) -> None:
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            metadata=RunMetadata(run_dir="/runs/r1", task_id="t1", source="web"),
        )
        assert env.metadata.run_dir == "/runs/r1"
        assert env.metadata.task_id == "t1"
        assert env.metadata.source == "web"

    def test_does_not_carry_runtime_fields(self) -> None:
        """Physical environment must not own runtime-assembly fields."""
        fields = ExecutionEnvironment.model_fields
        for leaked in (
            "llm_provider",
            "llm_config",
            "interaction_bridge",
            "runtime_ports",
            "turn_input",
            "user_instructions",
            "active_skills",
        ):
            assert leaked not in fields

    def test_drops_dead_physical_fields(self) -> None:
        """env_vars / config_dir were dead (never read / always empty)."""
        fields = ExecutionEnvironment.model_fields
        assert "env_vars" not in fields
        assert "config_dir" not in fields

    def test_session_accepts_session_protocol(self) -> None:
        mock_session = MagicMock(spec=Session)
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="ssh",
            cache_area=Path("/tmp/cache"),
            session=mock_session,
        )
        assert env.session is mock_session

    def test_with_execution_returns_new_instance(self) -> None:
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )
        sentinel = object()
        other = env.with_execution(
            session=sentinel,
            session_type="ssh",
            execution_workdir="/remote/exec",
        )
        assert other is not env
        assert other.session is sentinel
        assert other.session_type == "ssh"
        assert other.execution_workdir == "/remote/exec"
        # original untouched
        assert env.session is None
        assert env.session_type == "local"
        assert env.execution_workdir == str(env.workdir)

    def test_with_bohrium_sets_snapshot_on_env(self) -> None:
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="ssh",
            cache_area=Path("/tmp/cache"),
        )
        result = env.with_bohrium(BohriumRuntimeSnapshot(ssh_attached=True, node_id=9))
        assert result is not env
        assert isinstance(result.bohrium, BohriumRuntimePort)
        assert result.bohrium.snapshot is not None
        assert result.bohrium.snapshot.node_id == 9
        # original untouched
        assert env.bohrium.snapshot is None

    def test_with_bohrium_preserves_execution_workdir(self) -> None:
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="ssh",
            cache_area=Path("/tmp/cache"),
            execution_workdir="/custom/exec",
        )
        result = env.with_bohrium(BohriumRuntimeSnapshot(ssh_attached=True))
        assert result.execution_workdir == "/custom/exec"

    def test_model_dump_roundtrip_physical_fields(self) -> None:
        archival = WorkspaceArchivalConfig(enabled=True, oss_bucket="b")
        env = ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
            metadata=RunMetadata(task_id="t1"),
            archival=archival,
        )
        data = env.model_dump()
        assert "workdir" in data
        assert "execution_workdir" in data
        assert "archival" in data
        restored = ExecutionEnvironment.model_validate(data)
        assert restored.workdir == env.workdir
        assert restored.execution_workdir == env.execution_workdir
        assert restored.metadata == env.metadata
        assert restored.archival is not None and restored.archival.enabled is True


# ── AgentRunRequest ──────────────────────────────────────────────


class TestAgentRunRequest:
    def test_all_fields_default_to_empty(self) -> None:
        request = AgentRunRequest()
        assert request.llm_provider is None
        assert request.llm_config is None
        assert request.interaction_bridge is None
        assert request.turn_input is None
        assert request.user_instructions is None
        assert request.active_skills == frozenset()
        assert isinstance(request.ports, AgentRunPorts)

    def test_frozen_rejects_assignment(self) -> None:
        request = AgentRunRequest()
        with pytest.raises(ValidationError):
            request.llm_provider = object()

    def test_carries_resolved_runtime_ingredients(self) -> None:
        provider = object()
        bridge = object()
        request = AgentRunRequest(
            llm_provider=provider,
            interaction_bridge=bridge,
            active_skills=frozenset({"alpha"}),
        )
        assert request.llm_provider is provider
        assert request.interaction_bridge is bridge
        assert request.active_skills == frozenset({"alpha"})

    def test_interaction_bridge_excluded_from_dump(self) -> None:
        request = AgentRunRequest(interaction_bridge=object())
        assert "interaction_bridge" not in request.model_dump()

    def test_ports_carry_runtime_capabilities(self) -> None:
        def sink(event) -> None:
            return None

        request = AgentRunRequest(
            ports=AgentRunPorts(
                child_event_forward_sink=sink,
                figure_upload=FigureUploadPort(config=None),
            ),
        )
        assert request.ports.child_event_forward_sink is sink

    def test_carries_invocation_id_as_runtime_request_identity(self) -> None:
        request = AgentRunRequest(invocation_id="inv-1")

        assert request.invocation_id == "inv-1"
        assert "invocation_id" in request.model_dump()
        assert "invocation_id" not in RunMetadata.model_fields


# ── AgentRunContext ──────────────────────────────────────────────


class TestAgentRunContext:
    def _env(self) -> ExecutionEnvironment:
        return ExecutionEnvironment(
            workdir=Path("/tmp/work"),
            session_type="local",
            session_id="sess-1",
            cache_area=Path("/tmp/cache"),
        )

    def test_composes_environment_and_request(self) -> None:
        env = self._env()
        provider = object()
        request = AgentRunRequest(llm_provider=provider)
        ctx = AgentRunContext(environment=env, request=request)
        assert ctx.environment is env
        assert ctx.request is request
        # physical via environment, runtime via request
        assert ctx.environment.session_id == "sess-1"
        assert ctx.request.llm_provider is provider

    def test_frozen_rejects_assignment(self) -> None:
        ctx = AgentRunContext(environment=self._env(), request=AgentRunRequest())
        with pytest.raises(ValidationError):
            ctx.request = AgentRunRequest()

    def test_request_defaults_when_omitted(self) -> None:
        ctx = AgentRunContext(environment=self._env())
        assert isinstance(ctx.request, AgentRunRequest)
        assert ctx.request.llm_provider is None
