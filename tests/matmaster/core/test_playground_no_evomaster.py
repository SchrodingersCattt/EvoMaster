"""Import audit: playground.py has zero evomaster runtime dependency.

These tests verify that the refactored playground.py does not import
from evomaster at any level -- no ConfigManager, no BaseSession, no
PlaygroundSessionMixin.  This is the quality gate for Phase 25 Plan 03.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _playground_src() -> Path:
    """Resolve the playground.py source file."""
    return Path(__file__).resolve().parents[3] / "matmaster" / "core" / "playground.py"


def test_playground_no_evomaster_import():
    """playground.py MUST NOT import from evomaster at runtime."""
    src = _playground_src()
    tree = ast.parse(src.read_text(encoding="utf-8"))
    evomaster_imports = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and "evomaster" in node.module
        ):
            evomaster_imports.append(
                f"line {node.lineno}: from {node.module} import ..."
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "evomaster" in alias.name:
                    evomaster_imports.append(f"line {node.lineno}: import {alias.name}")
    assert (
        evomaster_imports == []
    ), "Found evomaster imports in playground.py:\n" + "\n".join(evomaster_imports)


def test_playground_no_playground_session_mixin():
    """playground.py MUST NOT reference PlaygroundSessionMixin."""
    content = _playground_src().read_text(encoding="utf-8")
    assert "PlaygroundSessionMixin" not in content


def test_playground_no_config_manager():
    """playground.py MUST NOT reference ConfigManager."""
    content = _playground_src().read_text(encoding="utf-8")
    assert "ConfigManager" not in content


def test_playground_no_base_session():
    """playground.py MUST NOT reference BaseSession."""
    content = _playground_src().read_text(encoding="utf-8")
    assert "BaseSession" not in content


def test_playground_has_parameterized_constructor():
    """playground.py MUST have Playground with parameterized __init__."""
    content = _playground_src().read_text(encoding="utf-8")
    assert (
        "def __init__(self, *, session_type" in content
        or "def __init__(\n        self,\n        *,\n        session_type" in content
    )


def test_playground_has_inlined_methods():
    """playground.py MUST contain attach_session, attach_ssh_session, detach_session."""
    content = _playground_src().read_text(encoding="utf-8")
    assert "def attach_session(" in content
    assert "def attach_ssh_session(" in content
    assert "def detach_session(" in content
