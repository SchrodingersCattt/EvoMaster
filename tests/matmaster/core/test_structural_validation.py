"""Tests for StructuralValidation -- Layer A stateless validation.

Tests args_schema validation, plane activation check, and
path normalization / stateless shell semantics.
"""

from __future__ import annotations

from typing import Any

from matmaster.core.structural_validation import StructuralValidation
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
    raise NotImplementedError("should not be called in validation tests")


def _make_instance(
    tool_name: str = "test_tool",
    args_schema: dict[str, Any] | None = None,
    plane: ToolPlane = ToolPlane.CONTROL_PLANE,
    capabilities: frozenset[str] = frozenset(),
    effect_level: str = "local_mutation",
) -> ToolInstance:
    spec = ToolSpec(
        tool_name=tool_name,
        args_schema=args_schema if args_schema is not None else {},
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
# TestArgsSchema
# ---------------------------------------------------------------------------


class TestArgsSchema:
    """args_schema validation using jsonschema."""

    def setup_method(self) -> None:
        self.validator = StructuralValidation()

    def test_deny_when_required_field_missing(self) -> None:
        """Empty dict against schema requiring 'path' -> deny."""
        instance = _make_instance(
            args_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
        topo = _make_topology()
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "deny"
        assert "path" in result.reason.lower()
        assert "required" in result.reason.lower()

    def test_allow_when_schema_empty(self) -> None:
        """Empty args_schema skips validation -> allow."""
        instance = _make_instance(args_schema={})
        topo = _make_topology()
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "allow"

    def test_allow_when_schema_passes(self) -> None:
        """Valid args matching schema -> allow."""
        instance = _make_instance(
            args_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
        topo = _make_topology()
        result = self.validator.validate(topo, instance, {"path": "/foo"})

        assert result.decision == "allow"

    def test_deny_when_type_mismatch(self) -> None:
        """Wrong type for field -> deny."""
        instance = _make_instance(
            args_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
        )
        topo = _make_topology()
        result = self.validator.validate(topo, instance, {"count": "not_a_number"})

        assert result.decision == "deny"
        assert "invalid arguments" in result.reason.lower()

    def test_deny_when_additional_field_is_unknown(self) -> None:
        """additionalProperties=False rejects unknown fields."""
        instance = _make_instance(
            tool_name="Bash",
            args_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
            plane=ToolPlane.SESSION_SHELL,
            capabilities=frozenset({"shell.execute"}),
        )
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.SESSION_SHELL, ToolPlane.CONTROL_PLANE}),
        )
        result = self.validator.validate(
            topo,
            instance,
            {"command": "pwd", "unexpected": True},
        )

        assert result.decision == "deny"
        assert "invalid arguments" in result.reason.lower()


# ---------------------------------------------------------------------------
# TestPlaneCheck
# ---------------------------------------------------------------------------


class TestPlaneCheck:
    """Plane activation validation."""

    def setup_method(self) -> None:
        self.validator = StructuralValidation()

    def test_deny_when_plane_not_active(self) -> None:
        """SESSION_SHELL tool with only CONTROL_PLANE active -> deny."""
        instance = _make_instance(plane=ToolPlane.SESSION_SHELL)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
        )
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "deny"
        assert "session_shell" in result.reason.lower()

    def test_allow_when_plane_active(self) -> None:
        """CONTROL_PLANE tool with CONTROL_PLANE active -> allow."""
        instance = _make_instance(plane=ToolPlane.CONTROL_PLANE)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
        )
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "allow"

    def test_deny_external_service_not_active(self) -> None:
        """EXTERNAL_SERVICE tool with no EXTERNAL_SERVICE plane -> deny."""
        instance = _make_instance(plane=ToolPlane.EXTERNAL_SERVICE)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
        )
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "deny"
        assert "external_service" in result.reason.lower()


# ---------------------------------------------------------------------------
# TestSessionShellExecution
# ---------------------------------------------------------------------------


class TestCapabilities:
    """Session capability inputs do not block one-shot shell execution."""

    def setup_method(self) -> None:
        self.validator = StructuralValidation()

    def test_allow_shell_execute_without_shell_input(self) -> None:
        """Stateless shell execution does not require interactive shell_input."""
        instance = _make_instance(
            plane=ToolPlane.SESSION_SHELL,
            capabilities=frozenset({"shell.execute"}),
        )
        caps = SessionCapabilities(shell_input=False)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.SESSION_SHELL, ToolPlane.CONTROL_PLANE}),
            session_capabilities=caps,
        )
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "allow"

    def test_allow_when_no_session_capabilities(self) -> None:
        """session_capabilities=None still allows one-shot shell execution."""
        instance = _make_instance(
            plane=ToolPlane.SESSION_SHELL,
            capabilities=frozenset({"shell.execute"}),
        )
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.SESSION_SHELL, ToolPlane.CONTROL_PLANE}),
            session_capabilities=None,
        )
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "allow"

    def test_allow_when_capabilities_match(self) -> None:
        """Interactive-capable sessions also allow one-shot shell execution."""
        instance = _make_instance(
            plane=ToolPlane.SESSION_SHELL,
            capabilities=frozenset({"shell.execute"}),
        )
        caps = SessionCapabilities(shell_input=True)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.SESSION_SHELL, ToolPlane.CONTROL_PLANE}),
            session_capabilities=caps,
        )
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "allow"

    def test_allow_control_plane_no_capability_check(self) -> None:
        """CONTROL_PLANE tools are unaffected by session shell capabilities."""
        instance = _make_instance(plane=ToolPlane.CONTROL_PLANE)
        caps = SessionCapabilities(shell_input=False)
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.CONTROL_PLANE}),
            session_capabilities=caps,
        )
        result = self.validator.validate(topo, instance, {})

        assert result.decision == "allow"


# ---------------------------------------------------------------------------
# TestPathNormalization
# ---------------------------------------------------------------------------


class TestPathNormalization:
    """Path normalization in Layer A structural validation."""

    def setup_method(self) -> None:
        self.validator = StructuralValidation()

    def test_normalizes_relative_file_path_into_workspace_root(self) -> None:
        """Relative file_path gets expanded to workspace_root/relative."""
        instance = _make_instance(
            tool_name="Read",
            plane=ToolPlane.SESSION_FS,
        )
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.SESSION_FS, ToolPlane.CONTROL_PLANE}),
        )
        result = self.validator.validate(topo, instance, {"file_path": "src/app.py"})

        assert result.decision == "allow"
        assert result.modified_args is not None
        assert result.modified_args["file_path"] == "/tmp/workspace/src/app.py"

    def test_denies_path_outside_workspace_root(self) -> None:
        """Path traversal outside workspace -> deny."""
        instance = _make_instance(
            tool_name="Read",
            plane=ToolPlane.SESSION_FS,
        )
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.SESSION_FS, ToolPlane.CONTROL_PLANE}),
        )
        result = self.validator.validate(topo, instance, {"file_path": "../secret.txt"})

        assert result.decision == "deny"
        assert "outside workspace boundary" in result.reason

    def test_absolute_path_within_workspace_passes(self) -> None:
        """Absolute path within workspace -> allow, no modified_args."""
        instance = _make_instance(
            tool_name="Read",
            plane=ToolPlane.SESSION_FS,
        )
        topo = _make_topology(
            active_planes=frozenset({ToolPlane.SESSION_FS, ToolPlane.CONTROL_PLANE}),
        )
        result = self.validator.validate(
            topo, instance, {"file_path": "/tmp/workspace/foo.py"}
        )

        assert result.decision == "allow"

    def test_normalizes_path_key(self) -> None:
        """Also normalizes the 'path' argument key."""
        instance = _make_instance(
            tool_name="Glob",
            plane=ToolPlane.SESSION_SHELL,
        )
        topo = _make_topology(
            active_planes=frozenset(ToolPlane),
        )
        result = self.validator.validate(topo, instance, {"path": "subdir"})

        assert result.decision == "allow"
        assert result.modified_args is not None
        assert result.modified_args["path"] == "/tmp/workspace/subdir"
