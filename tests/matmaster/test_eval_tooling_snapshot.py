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
    assert snap["skills_skill_names_filter"] == []
    assert "use_skill" not in snap["tool_names_surface"]
    assert "execute_bash" in snap["builtin_tool_names"]
    assert "spawn" in snap["builtin_tool_names"]
