"""Tool Runtime v2 topology types -- ToolPlane, SessionCapabilities, RuntimeTopology.

ToolPlane categorizes tools into four execution planes:
- SESSION_SHELL: tools that run commands via session shell
- SESSION_FS: tools that perform file system operations via session
- CONTROL_PLANE: tools that operate on the control (local) side
- EXTERNAL_SERVICE: tools that call external APIs/services

SessionCapabilities describes what a session can do (shell persistence,
file ops mode, upload support, etc.).

RuntimeTopology captures the execution topology for a run: which session
kind, what workspace roots, which planes are active, and the session
capabilities.

All Pydantic models are frozen=True (immutable after construction).
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ToolPlane(str, enum.Enum):
    """Execution plane for a tool."""

    SESSION_SHELL = "session_shell"
    SESSION_FS = "session_fs"
    CONTROL_PLANE = "control_plane"
    EXTERNAL_SERVICE = "external_service"


class SessionCapabilities(BaseModel):
    """Describes what a session implementation can do.

    Used by ToolRunner/ToolScheduler to decide whether a tool can execute
    in the current session. Phase 34 will wire this into real Session
    implementations.
    """

    model_config = ConfigDict(frozen=True)

    shell_persistence: Literal["stateless", "persistent"] = "stateless"
    shell_input: bool = False
    file_ops: Literal["native", "sftp"] = "native"
    upload_support: bool = False
    exec_cancel: bool = False


class RuntimeTopology(BaseModel):
    """Execution topology for an agent run.

    Captures the session kind, workspace roots, active tool planes,
    and session capabilities. Built by Exp.assemble() and consumed
    by ToolRunner/ToolScheduler.
    """

    model_config = ConfigDict(frozen=True)

    session_kind: str  # "local" | "ssh" | "docker"
    control_root: str  # control plane working directory
    workspace_root: str  # session workspace root path
    active_planes: frozenset[ToolPlane] = frozenset()
    session_capabilities: SessionCapabilities | None = None
