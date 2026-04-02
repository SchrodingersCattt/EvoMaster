"""Tests for matmaster.types.tool_spec -- ToolSpec, ResourceClaim, ToolBinding, ToolInstance."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_spec import (
    ResourceClaim,
    ToolBinding,
    ToolInstance,
    ToolSpec,
)
from matmaster.types.topology import ToolPlane


class TestToolSpec:
    def test_tool_spec_frozen(self) -> None:
        """ToolSpec is frozen -- assignment raises ValidationError."""
        spec = ToolSpec(tool_name="bash", description="Run commands")
        with pytest.raises(ValidationError):
            spec.tool_name = "other"

    def test_tool_spec_defaults(self) -> None:
        """ToolSpec has correct defaults."""
        spec = ToolSpec(tool_name="bash")
        assert spec.capabilities == frozenset()
        assert spec.effect_level == "local_mutation"
        assert spec.exposed_to_model is True
        assert spec.fast_path_eligible is False
        assert spec.description == ""
        assert spec.args_schema == {}
        assert spec.source == "unknown"

    def test_tool_spec_custom_values(self) -> None:
        """ToolSpec accepts custom values."""
        spec = ToolSpec(
            tool_name="read_file",
            description="Read a file",
            args_schema={"path": {"type": "string"}},
            source="builtin",
            capabilities=frozenset({"file_read"}),
            effect_level="pure_read",
            exposed_to_model=True,
            fast_path_eligible=True,
        )
        assert spec.tool_name == "read_file"
        assert "file_read" in spec.capabilities


class TestResourceClaim:
    def test_resource_claim_modes(self) -> None:
        """ResourceClaim supports three modes."""
        exclusive = ResourceClaim(resource_id="r1", mode="exclusive")
        assert exclusive.mode == "exclusive"
        assert exclusive.limit is None

        shared = ResourceClaim(resource_id="r2", mode="shared_read")
        assert shared.mode == "shared_read"

        counted = ResourceClaim(resource_id="r3", mode="counted", limit=5)
        assert counted.mode == "counted"
        assert counted.limit == 5

    def test_resource_claim_frozen(self) -> None:
        """ResourceClaim is frozen."""
        claim = ResourceClaim(resource_id="r1", mode="exclusive")
        with pytest.raises(ValidationError):
            claim.resource_id = "r2"


class TestToolBinding:
    def test_tool_binding_frozen(self) -> None:
        """ToolBinding is frozen -- assignment raises ValidationError."""
        binding = ToolBinding(
            binding_key="session_shell:bash",
            plane=ToolPlane.SESSION_SHELL,
        )
        with pytest.raises(ValidationError):
            binding.binding_key = "other:key"

    def test_tool_binding_defaults(self) -> None:
        """ToolBinding has correct defaults."""
        binding = ToolBinding(
            binding_key="session_shell:bash",
            plane=ToolPlane.SESSION_SHELL,
        )
        assert binding.resource_claims == ()
        assert binding.state_mode == "stateless"
        assert binding.stop_mode == "immediate"

    def test_tool_binding_with_resource_claims(self) -> None:
        """ToolBinding can hold resource claims."""
        claim = ResourceClaim(resource_id="shell", mode="exclusive")
        binding = ToolBinding(
            binding_key="session_shell:bash",
            plane=ToolPlane.SESSION_SHELL,
            resource_claims=(claim,),
            state_mode="session_scoped",
            stop_mode="graceful",
        )
        assert len(binding.resource_claims) == 1
        assert binding.state_mode == "session_scoped"
        assert binding.stop_mode == "graceful"


class TestToolInstance:
    def test_tool_instance_frozen(self) -> None:
        """ToolInstance is frozen dataclass -- assignment raises FrozenInstanceError."""
        spec = ToolSpec(tool_name="bash")
        binding = ToolBinding(
            binding_key="session_shell:bash",
            plane=ToolPlane.SESSION_SHELL,
        )

        async def executor(args: dict) -> ToolResult:
            return ToolResult(content="ok")

        instance = ToolInstance(
            tool_spec=spec,
            tool_binding=binding,
            tool_executor=executor,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            instance.tool_spec = spec  # type: ignore[misc]

    def test_tool_instance_composition(self) -> None:
        """ToolInstance composes ToolSpec + ToolBinding + executor."""
        spec = ToolSpec(tool_name="bash", description="Run commands")
        binding = ToolBinding(
            binding_key="session_shell:bash",
            plane=ToolPlane.SESSION_SHELL,
        )

        async def executor(args: dict) -> ToolResult:
            return ToolResult(content="done")

        instance = ToolInstance(
            tool_spec=spec,
            tool_binding=binding,
            tool_executor=executor,
        )
        assert instance.tool_spec.tool_name == "bash"
        assert instance.tool_binding.plane == ToolPlane.SESSION_SHELL
        assert callable(instance.tool_executor)
