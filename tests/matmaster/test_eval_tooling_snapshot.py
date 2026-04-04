"""Tests for ``matmaster.eval_tooling_snapshot``."""

from __future__ import annotations

from pathlib import Path

from matmaster.eval_tooling_snapshot import snapshot_devshell_eval_tooling

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_snapshot_default_devshell_skills_disabled() -> None:
    snap = snapshot_devshell_eval_tooling(repo_root=REPO_ROOT)
    assert snap["schema"] == "matmaster_eval_tooling_v1"
    assert snap["skills_enabled"] is False
    assert snap["skill_names"] == []
    assert snap["mcp_server_names"] == []
    assert "Skill" not in snap["tool_names_surface"]
    assert "Bash" in snap["builtin_tool_names"]
    assert "Bohrium" in snap["builtin_tool_names"]
    assert "Agent" in snap["builtin_tool_names"]
