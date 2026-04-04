"""Event routing tests for sub-agent source prefix.

Verifies that:
1. normalize_event_source preserves MatMaster:* prefix
2. _normalize_public_source preserves MatMaster:* prefix
3. _is_matmaster_source helper matches both MatMaster and MatMaster:*
"""

import pytest

from matmaster.integration.event_payloads import _normalize_public_source

_is_matmaster_source = pytest.importorskip(
    "src.services.chat_history",
    reason="src not available (isolation test)",
)._is_matmaster_source
normalize_event_source = pytest.importorskip(
    "src.utils.chat_event_source",
    reason="src not available (isolation test)",
).normalize_event_source

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
