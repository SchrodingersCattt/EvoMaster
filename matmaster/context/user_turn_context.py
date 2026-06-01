"""Shared user_turn_context constants.

Core runtime code may import this module. Durable DB writes stay in
src.services.user_turn_context_service.
"""

from __future__ import annotations

DEFAULT_TURN_TRANSFORM = "raw"
USER_CONTEXT_RENDER_VERSION = "user_context_render.v1"
USER_TURN_CONTEXT_SCHEMA_VERSION = "user_turn_context.v1"
