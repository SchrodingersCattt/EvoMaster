from __future__ import annotations

from matmaster.context.assembly import ContextAssemblyIntent


def decide_turn_context_intent(
    *,
    current_hash: str,
    latest_anchor_hash: str | None,
) -> ContextAssemblyIntent:
    if latest_anchor_hash is None or latest_anchor_hash != current_hash:
        return ContextAssemblyIntent.ANCHOR_TURN
    return ContextAssemblyIntent.CONTINUATION_TURN
