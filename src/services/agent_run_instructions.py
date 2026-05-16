"""User instructions path constant (Phase 2C final state).

Phase 1's runtime injection helper was removed during the Phase 2C cutover.
This module now only exposes the AGENT.md path used by
user_turn_context_service.load_user_instructions_from_session.
"""

from __future__ import annotations

_USER_INSTRUCTIONS_PATH = "/personal/.matmaster/AGENT.md"
