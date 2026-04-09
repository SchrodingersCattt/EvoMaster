"""Structural tests to prevent dual-implementation drift."""

from __future__ import annotations

import ast
from pathlib import Path

_BOHRIUM_TOOL_DIR = (
    Path(__file__).resolve().parents[3]
    / "matmaster"
    / "tools"
    / "builtin"
    / "bohrium_tool"
)


def test_no_status_map_in_bohrium_tool() -> None:
    for py_file in _BOHRIUM_TOOL_DIR.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_STATUS_MAP":
                        raise AssertionError(
                            f"{py_file.name} defines _STATUS_MAP — "
                            "use matmaster.bohrium.status instead"
                        )


def test_deleted_modules_do_not_exist() -> None:
    bohrium_dir = _BOHRIUM_TOOL_DIR.parents[2] / "bohrium"
    assert not (bohrium_dir / "jobs.py").exists(), "bohrium/jobs.py should be deleted"
    assert not (_BOHRIUM_TOOL_DIR / "api.py").exists(), "bohrium_tool/api.py should be deleted"
    assert not (_BOHRIUM_TOOL_DIR / "open_sdk.py").exists(), (
        "bohrium_tool/open_sdk.py should be deleted"
    )


def test_bohrium_tool_does_not_import_private_client_helpers() -> None:
    tool_path = _BOHRIUM_TOOL_DIR / "tool.py"
    tree = ast.parse(tool_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "matmaster.bohrium.client":
            continue
        imported = {alias.name for alias in node.names}
        assert all(not name.startswith("_") for name in imported), (
            "tool.py should not import private helpers from matmaster.bohrium.client"
        )
        assert "list_sandbox_machines" not in imported, (
            "tool.py should use public list_machines/list_images from client.py"
        )
