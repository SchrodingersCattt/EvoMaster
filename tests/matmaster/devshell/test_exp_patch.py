"""Tests for ``matmaster.devshell.exp_patch``."""

from __future__ import annotations

from matmaster.config.loader import load_exp_config
from matmaster.devshell.exp_patch import (
    DEVSHELL_MAT_SG_TOOLS,
    STRUCT_DB_LAZYMCP_ROOT,
    STRUCT_GEN_LAZYMCP_ROOT,
    devshell_default_exp_config,
    patch_direct_skills_for_devshell_default,
)


def test_patch_narrows_skills_root_only() -> None:
    base = load_exp_config("direct")
    patched = patch_direct_skills_for_devshell_default(base)
    assert patched.name == base.name
    assert patched.max_turns == base.max_turns
    assert patched.tools.builtin == base.tools.builtin
    assert patched.skills.skills_root == [
        STRUCT_DB_LAZYMCP_ROOT,
        STRUCT_GEN_LAZYMCP_ROOT,
    ]
    assert patched.skills.mcp_runtime_patch == {
        "tool_include_only": {"mat_sg": list(DEVSHELL_MAT_SG_TOOLS)},
    }


def test_devshell_default_matches_patched_direct() -> None:
    a = devshell_default_exp_config()
    b = patch_direct_skills_for_devshell_default(load_exp_config("direct"))
    assert a.model_dump() == b.model_dump()
