"""Unit tests for the active-MCP-server resolver helper."""

from __future__ import annotations

import json
from pathlib import Path

from src.services.agent_run_service import _resolve_active_mcp_servers_from_events


def _make_cache_dir(tmp_path: Path, names: list[str]) -> Path:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    for name in names:
        (cache_dir / f"{name}.json").write_text("[]")
    return cache_dir


def test_empty_inputs_yield_empty_set(tmp_path):
    cache_dir = _make_cache_dir(tmp_path, [])
    assert _resolve_active_mcp_servers_from_events([], cache_dir, None) == set()
    assert (
        _resolve_active_mcp_servers_from_events(
            [{"type": "tool_call", "tool_name": "mat_xrd_read"}], cache_dir, None
        )
        == set()
    )


def test_assistant_state_tool_calls_use_longest_prefix(tmp_path):
    cache_dir = _make_cache_dir(tmp_path, ["mat_xrd", "mat_struct_db", "mat_sg"])
    events = [
        {
            "type": "assistant_state",
            "content": {
                "tool_calls": [
                    {"name": "mat_xrd_read", "arguments": {}},
                    {"name": "mat_struct_db_query", "arguments": {}},
                ]
            },
        }
    ]
    result = _resolve_active_mcp_servers_from_events(events, cache_dir, None)
    # 'mat' must NOT be inferred -- mat_xrd / mat_struct_db are longer matches.
    assert result == {"mat_xrd", "mat_struct_db"}


def test_tool_call_event_alone_resolves_server(tmp_path):
    cache_dir = _make_cache_dir(tmp_path, ["mat_sg"])
    events = [{"type": "tool_call", "tool_name": "mat_sg_build_bulk"}]
    assert _resolve_active_mcp_servers_from_events(
        events, cache_dir, None
    ) == {"mat_sg"}


def test_unknown_server_prefix_is_ignored(tmp_path):
    cache_dir = _make_cache_dir(tmp_path, ["mat_xrd"])
    events = [{"type": "tool_call", "tool_name": "totally_unrelated_tool"}]
    assert (
        _resolve_active_mcp_servers_from_events(events, cache_dir, None) == set()
    )


def test_skill_hit_resolves_via_registry(tmp_path):
    cache_dir = _make_cache_dir(tmp_path, ["mat_sg"])

    skill_root = tmp_path / "skills" / "test-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: T\nmcp_server: mat_sg\n---\nbody\n"
    )

    from matmaster.skills.registry import SkillRegistry

    registry = SkillRegistry([tmp_path / "skills"])
    events = [{"type": "skill_hit", "content": {"skill_name": "test-skill"}}]

    assert _resolve_active_mcp_servers_from_events(
        events, cache_dir, registry
    ) == {"mat_sg"}


def test_skill_hit_without_registry_is_dropped(tmp_path):
    cache_dir = _make_cache_dir(tmp_path, ["mat_sg"])
    events = [{"type": "skill_hit", "content": {"skill_name": "anything"}}]
    assert (
        _resolve_active_mcp_servers_from_events(events, cache_dir, None) == set()
    )


def test_skill_hit_with_unknown_skill_is_dropped(tmp_path):
    cache_dir = _make_cache_dir(tmp_path, ["mat_sg"])

    skill_root = tmp_path / "skills"
    skill_root.mkdir()

    from matmaster.skills.registry import SkillRegistry

    registry = SkillRegistry([skill_root])
    events = [{"type": "skill_hit", "content": {"skill_name": "nope"}}]

    assert _resolve_active_mcp_servers_from_events(
        events, cache_dir, registry
    ) == set()
