"""Tests for CapabilityPolicy -- Layer C effect_level + capability policy.

Tests effect_level constraints, capability matching, and Protocol
conformance.
"""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.core.capability_policy import CapabilityPolicy, DefaultCapabilityPolicy
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_spec import ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import (
    RuntimeTopology,
    SessionCapabilities,
    ToolPlane,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop_executor(args: dict[str, Any]) -> Any:
    """Dummy executor for ToolInstance construction."""
    raise NotImplementedError("should not be called in policy tests")


def _make_instance(
    tool_name: str = "test_tool",
    plane: ToolPlane = ToolPlane.CONTROL_PLANE,
    capabilities: frozenset[str] = frozenset(),
    effect_level: str = "local_mutation",
) -> ToolInstance:
    spec = ToolSpec(
        tool_name=tool_name,
        capabilities=capabilities,
        effect_level=effect_level,
    )
    binding = ToolBinding(
        binding_key=f"{plane.value}:{tool_name}",
        plane=plane,
    )
    return ToolInstance(
        tool_spec=spec,
        tool_binding=binding,
        tool_executor=_noop_executor,
    )


def _make_topology(
    active_planes: frozenset[ToolPlane] | None = None,
    session_capabilities: SessionCapabilities | None = None,
) -> RuntimeTopology:
    if active_planes is None:
        active_planes = frozenset({ToolPlane.CONTROL_PLANE})
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=active_planes,
        session_capabilities=session_capabilities,
    )


# ---------------------------------------------------------------------------
# TestEffectLevel
# ---------------------------------------------------------------------------

class TestEffectLevel:
    """effect_level constraint checks."""

    def setup_method(self) -> None:
        self.policy = DefaultCapabilityPolicy()

    def test_deny_external_write_without_external_plane(self) -> None:
        """external_write effect with no EXTERNAL_SERVICE plane -> deny + guidance."""
        instance = _make_instance(effect_level="external_write")
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
        )
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "deny"
        assert "external" in result.reason.lower()
        assert result.guidance is not None
        assert len(result.guidance) > 0

    def test_allow_external_write_with_external_plane(self) -> None:
        """external_write effect with EXTERNAL_SERVICE plane active -> allow."""
        instance = _make_instance(effect_level="external_write")
        topo = _make_topology(
            active_planes=frozenset({
                ToolPlane.CONTROL_PLANE,
                ToolPlane.EXTERNAL_SERVICE,
            }),
        )
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "allow"

    def test_allow_pure_read(self) -> None:
        """pure_read effect -> allow."""
        instance = _make_instance(effect_level="pure_read")
        topo = _make_topology()
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "allow"

    def test_allow_local_mutation(self) -> None:
        """local_mutation effect -> allow."""
        instance = _make_instance(effect_level="local_mutation")
        topo = _make_topology()
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "allow"


class TestEffectLevelWithRealBuiltinMeta:
    """Verify policy checks match the real builtin metadata values."""

    def test_builtin_meta_web_tools_have_external_write(self) -> None:
        """Builtin web tools use the canonical external_write effect level."""
        from matmaster.tools.tool_catalog import BUILTIN_META

        for tool_name in ("mm_web_search", "web_fetch", "monitor_job"):
            _plane, effect_level, _fast, *_rest = BUILTIN_META[tool_name]
            assert effect_level == "external_write", (
                f"{tool_name} effect_level={effect_level!r}, "
                "expected 'external_write'"
            )

    def test_deny_web_tool_via_builtin_meta_values(self) -> None:
        """Policy deny still triggers when using real builtin metadata."""
        from matmaster.tools.tool_catalog import BUILTIN_META

        plane, effect_level, _fast, *_rest = BUILTIN_META["mm_web_search"]
        instance = _make_instance(effect_level=effect_level, plane=plane)
        topo = RuntimeTopology(
            session_kind="local",
            control_root="/ctrl",
            workspace_root="/ws",
            active_planes=frozenset(
                {ToolPlane.SESSION_SHELL, ToolPlane.SESSION_FS}
            ),
        )

        decision = DefaultCapabilityPolicy().evaluate(topo, instance, {})

        assert decision.decision == "deny"
        assert decision.guidance is not None
        assert "external" in decision.reason.lower()


# ---------------------------------------------------------------------------
# TestCapabilityMatch
# ---------------------------------------------------------------------------

class TestCapabilityMatch:
    """Fine-grained capability matching."""

    def setup_method(self) -> None:
        self.policy = DefaultCapabilityPolicy()

    def test_deny_artifact_download_without_upload_support(self) -> None:
        """artifact.download capability but upload_support=False -> deny + guidance."""
        instance = _make_instance(
            capabilities=frozenset({"artifact.download"}),
        )
        caps = SessionCapabilities(upload_support=False)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
            session_capabilities=caps,
        )
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "deny"
        assert result.guidance is not None
        assert "upload" in result.guidance.lower() or "download" in result.guidance.lower()

    def test_allow_artifact_download_with_upload_support(self) -> None:
        """artifact.download with upload_support=True -> allow."""
        instance = _make_instance(
            capabilities=frozenset({"artifact.download"}),
        )
        caps = SessionCapabilities(upload_support=True)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
            session_capabilities=caps,
        )
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "allow"

    def test_deny_shell_execute_without_shell_input(self) -> None:
        """shell.execute capability but shell_input=False -> deny + guidance."""
        instance = _make_instance(
            capabilities=frozenset({"shell.execute"}),
        )
        caps = SessionCapabilities(shell_input=False)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
            session_capabilities=caps,
        )
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "deny"
        assert result.guidance is not None
        assert "shell" in result.guidance.lower()

    def test_allow_empty_capabilities(self) -> None:
        """Empty capabilities set -> allow."""
        instance = _make_instance(capabilities=frozenset())
        caps = SessionCapabilities(upload_support=False, shell_input=False)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
            session_capabilities=caps,
        )
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "allow"

    def test_allow_when_no_session_capabilities(self) -> None:
        """session_capabilities=None skips capability checks -> allow."""
        instance = _make_instance(
            capabilities=frozenset({"artifact.download", "shell.execute"}),
        )
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
            session_capabilities=None,
        )
        result = self.policy.evaluate(topo, instance, {})

        assert result.decision == "allow"


# ---------------------------------------------------------------------------
# TestProtocol
# ---------------------------------------------------------------------------

class TestProtocol:
    """Protocol conformance."""

    def test_default_is_capability_policy(self) -> None:
        """DefaultCapabilityPolicy is a runtime_checkable CapabilityPolicy."""
        policy = DefaultCapabilityPolicy()
        assert isinstance(policy, CapabilityPolicy)

    def test_protocol_is_runtime_checkable(self) -> None:
        """CapabilityPolicy Protocol supports isinstance checks."""

        class CustomPolicy:
            def evaluate(
                self,
                runtime_topology: RuntimeTopology,
                tool_instance: ToolInstance,
                tool_args: dict[str, Any],
            ) -> ToolDecision:
                return ToolDecision(decision="allow")

        assert isinstance(CustomPolicy(), CapabilityPolicy)
