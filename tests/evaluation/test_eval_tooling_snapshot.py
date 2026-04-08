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
    # Umbrella ``matmaster/skills`` expands to lazymcp roots when present.
    assert len(snap["skills_roots"]) >= 1
    joined = "\n".join(snap["skills_roots"])
    assert "mat_master" in joined or "lazymcp" in joined


def test_snapshot_devshell_matches_direct_except_matmaster_exp_label() -> None:
    """Default devshell tooling snapshot is ``direct``; only ingest label differs."""
    a = snapshot_devshell_eval_tooling(repo_root=REPO_ROOT)
    d = snapshot_eval_tooling(repo_root=REPO_ROOT, exp_name="direct")
    assert a["matmaster_exp"] == "devshell"
    assert d["matmaster_exp"] == "direct"
    a_rest = {k: v for k, v in a.items() if k != "matmaster_exp"}
    d_rest = {k: v for k, v in d.items() if k != "matmaster_exp"}
    assert a_rest == d_rest
