"""Tests for ToolCallData.arguments_json caching (E3 fix layer 1)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from matmaster.types.messages import ToolCallData


def test_arguments_json_cached_once():
    """Second access to arguments_json should not call json.dumps again."""
    tc = ToolCallData(id="c1", name="search", arguments={"q": "hello", "n": 5})
    orig_dumps = json.dumps
    call_count = 0

    def counting(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return orig_dumps(*args, **kwargs)

    with patch("matmaster.types.messages.json.dumps", side_effect=counting):
        first = tc.arguments_json
        second = tc.arguments_json
        third = tc.arguments_json

    assert first == second == third
    assert call_count == 1, f"expected 1 json.dumps call, got {call_count}"
    assert json.loads(first) == {"q": "hello", "n": 5}


def test_arguments_json_equivalent_to_direct_dumps():
    """arguments_json should match json.dumps(tc.arguments)."""
    args = {"complex": {"nested": [1, 2, 3]}, "str": "value"}
    tc = ToolCallData(id="c1", name="search", arguments=args)
    assert tc.arguments_json == json.dumps(args)


def test_tool_call_data_frozen_blocks_field_rebind():
    """frozen=True blocks rebinding the arguments field."""
    tc = ToolCallData(id="c1", name="search", arguments={"q": "x"})
    with pytest.raises(Exception):
        tc.arguments = {"q": "y"}  # type: ignore[misc]


def test_model_copy_update_arguments_is_forbidden():
    """model_copy(update={'arguments': ...}) would carry stale cached JSON."""
    tc = ToolCallData(id="c1", name="search", arguments={"q": "old"})
    assert json.loads(tc.arguments_json) == {"q": "old"}

    with pytest.raises(ValueError, match="fresh ToolCallData"):
        tc.model_copy(update={"arguments": {"q": "new"}})
