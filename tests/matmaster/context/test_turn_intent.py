from __future__ import annotations

from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.turn_intent import decide_turn_context_intent


def test_decide_turn_context_intent_returns_anchor_when_no_latest_hash() -> None:
    assert (
        decide_turn_context_intent(
            current_hash="sha256:current",
            latest_anchor_hash=None,
        )
        == ContextAssemblyIntent.ANCHOR_TURN
    )


def test_decide_turn_context_intent_returns_anchor_when_hash_changed() -> None:
    assert (
        decide_turn_context_intent(
            current_hash="sha256:new",
            latest_anchor_hash="sha256:old",
        )
        == ContextAssemblyIntent.ANCHOR_TURN
    )


def test_decide_turn_context_intent_returns_continuation_when_hash_matches() -> None:
    assert (
        decide_turn_context_intent(
            current_hash="sha256:same",
            latest_anchor_hash="sha256:same",
        )
        == ContextAssemblyIntent.CONTINUATION_TURN
    )
