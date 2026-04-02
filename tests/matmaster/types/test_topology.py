"""Tests for matmaster.types.topology -- ToolPlane, SessionCapabilities, RuntimeTopology."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from matmaster.types.topology import (
    RuntimeTopology,
    SessionCapabilities,
    ToolPlane,
)


class TestToolPlane:
    def test_tool_plane_members(self) -> None:
        """ToolPlane has exactly 4 members and each is a str."""
        members = list(ToolPlane)
        assert len(members) == 4
        assert ToolPlane.SESSION_SHELL in members
        assert ToolPlane.SESSION_FS in members
        assert ToolPlane.CONTROL_PLANE in members
        assert ToolPlane.EXTERNAL_SERVICE in members

    def test_tool_plane_is_str(self) -> None:
        """ToolPlane values are str type (str, Enum)."""
        assert isinstance(ToolPlane.SESSION_SHELL, str)
        assert ToolPlane.SESSION_SHELL == "session_shell"
        assert ToolPlane.SESSION_FS == "session_fs"
        assert ToolPlane.CONTROL_PLANE == "control_plane"
        assert ToolPlane.EXTERNAL_SERVICE == "external_service"


class TestSessionCapabilities:
    def test_session_capabilities_frozen(self) -> None:
        """SessionCapabilities is frozen -- assignment raises ValidationError."""
        caps = SessionCapabilities()
        with pytest.raises(ValidationError):
            caps.shell_persistence = "persistent"

    def test_session_capabilities_defaults(self) -> None:
        """SessionCapabilities has correct defaults."""
        caps = SessionCapabilities()
        assert caps.shell_persistence == "stateless"
        assert caps.shell_input is False
        assert caps.file_ops == "native"
        assert caps.upload_support is False
        assert caps.exec_cancel is False

    def test_session_capabilities_custom_values(self) -> None:
        """SessionCapabilities accepts custom values."""
        caps = SessionCapabilities(
            shell_persistence="persistent",
            shell_input=True,
            file_ops="sftp",
            upload_support=True,
            exec_cancel=True,
        )
        assert caps.shell_persistence == "persistent"
        assert caps.shell_input is True
        assert caps.file_ops == "sftp"
        assert caps.upload_support is True
        assert caps.exec_cancel is True


class TestRuntimeTopology:
    def test_runtime_topology_frozen(self) -> None:
        """RuntimeTopology is frozen -- assignment raises ValidationError."""
        topo = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/ctrl",
            workspace_root="/workspace",
        )
        with pytest.raises(ValidationError):
            topo.session_kind = "ssh"

    def test_runtime_topology_defaults(self) -> None:
        """RuntimeTopology has correct defaults for optional fields."""
        topo = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/ctrl",
            workspace_root="/workspace",
        )
        assert topo.active_planes == frozenset()
        assert topo.session_capabilities is None

    def test_runtime_topology_active_planes_coercion(self) -> None:
        """RuntimeTopology.active_planes coerces set/list input to frozenset."""
        # From set
        topo1 = RuntimeTopology(
            session_kind="local",
            control_root="/tmp",
            workspace_root="/ws",
            active_planes={ToolPlane.SESSION_SHELL, ToolPlane.SESSION_FS},
        )
        assert isinstance(topo1.active_planes, frozenset)
        assert ToolPlane.SESSION_SHELL in topo1.active_planes

        # From list
        topo2 = RuntimeTopology(
            session_kind="local",
            control_root="/tmp",
            workspace_root="/ws",
            active_planes=[ToolPlane.CONTROL_PLANE],
        )
        assert isinstance(topo2.active_planes, frozenset)
        assert ToolPlane.CONTROL_PLANE in topo2.active_planes

    def test_runtime_topology_with_capabilities(self) -> None:
        """RuntimeTopology can hold SessionCapabilities."""
        caps = SessionCapabilities(shell_persistence="persistent", exec_cancel=True)
        topo = RuntimeTopology(
            session_kind="ssh",
            control_root="/ctrl",
            workspace_root="/ws",
            session_capabilities=caps,
        )
        assert topo.session_capabilities is not None
        assert topo.session_capabilities.shell_persistence == "persistent"
        assert topo.session_capabilities.exec_cancel is True
