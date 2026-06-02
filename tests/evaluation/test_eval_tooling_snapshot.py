"""Tests for ``evaluation.eval_tooling_snapshot``."""

from __future__ import annotations

from pathlib import Path

from evaluation.eval_tooling_snapshot import snapshot_eval_tooling

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_snapshot_eval_tooling_direct_matches_production_exp() -> None:
    snap = snapshot_eval_tooling(repo_root=REPO_ROOT, exp_name="direct")
    assert snap["schema"] == "matmaster_eval_tooling_v1"
    assert snap["exp_config_name"] == "direct"
    assert snap["skills_enabled"] is True
    assert snap["skills_skill_names_filter"] == []
    assert "Skill" in snap["tool_names_surface"]
    assert "WebSearch" in snap["builtin_tool_names"]
    assert "WebFetch" in snap["builtin_tool_names"]
    assert snap["session_type"] == "local"
    assert len(snap["skills_roots"]) >= 1
    joined = "\n".join(snap["skills_roots"])
    assert "matmaster/skills" in joined.replace("\\", "/")
    assert "abacus" in snap["skill_names"]
    assert "matmaster_exp" not in snap
