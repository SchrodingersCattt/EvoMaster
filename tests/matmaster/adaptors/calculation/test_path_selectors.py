from __future__ import annotations

import json
from pathlib import Path

from matmaster.adaptors.calculation.path_selectors import (
    collect_path_selectors,
    is_output_like_path_name,
    rewrite_selected_paths,
    validate_selector_paths,
)


def _load_cached_tool(server_name: str, tool_name: str) -> dict:
    cache_path = Path("matmaster/cache") / f"{server_name}.json"
    tools = json.loads(cache_path.read_text(encoding="utf-8"))
    for tool in tools:
        if tool["name"] == tool_name:
            return tool
    raise KeyError(f"Tool not found in cache: {server_name}.{tool_name}")


def _load_cached_schema(server_name: str, tool_name: str) -> dict:
    return _load_cached_tool(server_name, tool_name)["input_schema"]


def test_collect_path_selectors_dereferences_local_defs_for_compdart_schema():
    schema = _load_cached_schema("mat_compdart", "submit_run_dart_ga")

    selectors = collect_path_selectors(schema)

    assert "structure_config.template_path" in selectors


def test_validate_selector_paths_accepts_nested_array_selector():
    schema = _load_cached_schema("mat_compdart", "submit_run_dart_ga")

    validate_selector_paths(schema, {"targets[].model_path"})


def test_rewrite_selected_paths_updates_nested_targets_model_path():
    payload = {
        "targets": [{"name": "bulk_modulus", "type": "surrogate", "model_path": "model.pt"}]
    }

    rewritten = rewrite_selected_paths(
        payload,
        selectors={"targets[].model_path"},
        rewrite_leaf=lambda selector, value, schema_leaf: f"https://oss.test/{value}",
    )

    assert rewritten["targets"][0]["model_path"] == "https://oss.test/model.pt"


def test_union_path_enum_literal_passes_through():
    schema = _load_cached_schema("mat_compdart", "submit_run_dart_ga")
    payload = {"structure_config": {"mode": "template", "template_path": "fcc"}}

    rewritten = rewrite_selected_paths(
        payload,
        selectors={"structure_config.template_path"},
        schema=schema,
        rewrite_leaf=lambda selector, value, schema_leaf: "SHOULD_NOT_RUN",
    )

    assert rewritten["structure_config"]["template_path"] == "fcc"


def test_output_like_path_name_is_excluded():
    assert is_output_like_path_name(
        "plot_path", "File path to save the phonon band structure plot."
    )
