"""Event routing tests for sub-agent source prefix.

Verifies that:
1. normalize_event_source preserves MatMaster:* prefix
2. _normalize_public_source preserves MatMaster:* prefix
3. _is_matmaster_source helper matches both MatMaster and MatMaster:*
4. EventEmitterHook with prefixed source emits events with correct source
"""

import queue

import pytest

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import EventEmitterHook
from matmaster.integration.event_payloads import _normalize_public_source
from matmaster.types.messages import ToolCallData
from src.services.chat_history import _is_matmaster_source
from src.utils.chat_event_source import normalize_event_source


# ── normalize_event_source tests ──────────────────────


def test_normalize_event_source_preserves_subagent_prefix():
    """MatMaster:explore should be preserved, not collapsed to MatMaster."""
    assert normalize_event_source("MatMaster:explore") == "MatMaster:explore"


def test_normalize_event_source_preserves_subagent_prefix_other():
    """MatMaster:analysis should also be preserved."""
    assert normalize_event_source("MatMaster:analysis") == "MatMaster:analysis"


def test_normalize_event_source_collapses_plain_source():
    """Non-User/System/MatMaster:* sources collapse to MatMaster."""
    assert normalize_event_source("direct") == "MatMaster"
    assert normalize_event_source("explore") == "MatMaster"


def test_normalize_event_source_preserves_matmaster():
    """Plain MatMaster stays MatMaster."""
    assert normalize_event_source("MatMaster") == "MatMaster"


# ── _normalize_public_source tests ────────────────────


def test_normalize_public_source_preserves_subagent_prefix():
    """MatMaster:explore should be preserved in public source."""
    assert _normalize_public_source("MatMaster:explore") == "MatMaster:explore"


def test_normalize_public_source_collapses_plain():
    """Non-User/System/MatMaster:* sources collapse to MatMaster."""
    assert _normalize_public_source("direct") == "MatMaster"


# ── _is_matmaster_source tests ────────────────────────


def test_is_matmaster_source_exact():
    """Exact 'MatMaster' should match."""
    assert _is_matmaster_source("MatMaster") is True


def test_is_matmaster_source_prefixed():
    """'MatMaster:explore' should match."""
    assert _is_matmaster_source("MatMaster:explore") is True


def test_is_matmaster_source_other():
    """Non-MatMaster sources should not match."""
    assert _is_matmaster_source("User") is False
    assert _is_matmaster_source("System") is False
    assert _is_matmaster_source("direct") is False
    assert _is_matmaster_source("") is False


# ── EventEmitterHook with prefixed source ─────────────


async def test_event_emitter_hook_with_prefixed_source():
    """EventEmitterHook(bus, source='MatMaster:explore') should emit events
    with source='MatMaster:explore', not collapsed to 'MatMaster'."""
    bus = MessageBus()
    hook = EventEmitterHook(bus, source="MatMaster:explore")

    tool_call = ToolCallData(
        id="call_001",
        name="read_file",
        arguments={"path": "/tmp/test.txt"},
    )
    await hook.pre_tool_call(tool_call)

    event = bus.get(timeout=1.0)
    assert event.source == "MatMaster:explore"
    assert event.type == "tool_call"
    assert event.call_id == "call_001"
    assert event.tool_name == "read_file"
