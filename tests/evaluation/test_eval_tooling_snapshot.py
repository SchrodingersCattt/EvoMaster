"""Tests for ``evaluation.eval_tooling_snapshot``."""

from __future__ import annotations

from pathlib import Path

from evaluation.eval_tooling_snapshot import (
    snapshot_devshell_eval_tooling,
    snapshot_eval_tooling,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_snapshot_eval_tooling_direct_matches_production_exp() -> None:
    snap = snapshot_eval_tooling(repo_root=REPO_ROOT, exp_name="direct")
    assert snap["schema"] == "matmaster_eval_tooling_v1"
    assert snap["matmaster_exp"] == "direct"
    assert snap["exp_config_name"] == "direct"
    assert snap["skills_enabled"] is True
    assert snap["skills_skill_names_filter"] == []
    assert "use_skill" in snap["tool_names_surface"]
    assert "mm_web_search" in snap["builtin_tool_names"]
    assert "web_fetch" in snap["builtin_tool_names"]
    assert snap["session_type"] == "local"
    assert len(snap["skills_roots"]) == 2
    joined = "\n".join(snap["skills_roots"])
    assert "mat_master" in joined or "lazymcp" in joined


def test_snapshot_devshell_is_direct_plus_narrow_skills() -> None:
    a = snapshot_devshell_eval_tooling(repo_root=REPO_ROOT)
    d = snapshot_eval_tooling(repo_root=REPO_ROOT, exp_name="direct")
    assert a["matmaster_exp"] == "devshell"
    assert a["exp_config_name"] == "direct"
    assert len(a["skills_roots"]) == 1
    assert "mcp-mat-struct-db" in a["skills_roots"][0]
    assert a["builtin_tool_names"] == d["builtin_tool_names"]
    assert a["max_turns"] == d["max_turns"]
