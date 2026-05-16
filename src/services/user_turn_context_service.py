"""Durable user-turn context write boundary and AGENT.md helper.

Phase 1 renderer helpers were removed in the Phase 2C runtime cutover.
Rendering now belongs to matmaster.context.assembly.ContextAssembler; this
module keeps only shared constants, AGENT.md loading, and durable event write
deduplication.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

_USER_INSTRUCTIONS_PATH = "/personal/.matmaster/AGENT.md"
USER_INSTRUCTIONS_MAX_BYTES = 50 * 1024
USER_TURN_CONTEXT_SCHEMA_VERSION = "user_turn_context.v1"
USER_CONTEXT_RENDER_VERSION = "user_context_render.v1"
DEFAULT_TURN_TRANSFORM = "raw"

UserTurnContextKind = Literal["anchor", "continuation"]
UserTurnContextTransform = Literal["raw", "preflight_compacted", "oversized_summary"]
UserTurnContextWriteStatus = Literal["written", "duplicate"]


@dataclass(frozen=True)
class UserInstructionsInfo:
    text: str
    hash: str
    truncated: bool = False


def hash_user_instructions(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def make_user_instructions_info(
    text: str | None,
    *,
    truncated: bool = False,
) -> UserInstructionsInfo:
    raw_text = text or ""
    return UserInstructionsInfo(
        text=raw_text,
        hash=hash_user_instructions(raw_text),
        truncated=truncated,
    )


def load_user_instructions_from_session(session: Any) -> UserInstructionsInfo:
    if session is None:
        return make_user_instructions_info("")

    try:
        text = session.read_file(_USER_INSTRUCTIONS_PATH)
    except Exception:
        return make_user_instructions_info("")

    raw_text = str(text or "")
    truncated_text, truncated = _truncate_utf8(raw_text, USER_INSTRUCTIONS_MAX_BYTES)
    if not truncated:
        return make_user_instructions_info(truncated_text)

    logger.warning(
        "AGENT.md exceeds %s bytes; truncating user instructions for "
        "user_turn_context",
        USER_INSTRUCTIONS_MAX_BYTES,
    )
    return make_user_instructions_info(truncated_text, truncated=True)


async def write_user_turn_context_event(
    *,
    events_table: Any,
    session_id: str,
    task_id: str | None,
    invocation_id: str | None,
    spawn_id: str | None,
    payload: dict[str, Any],
) -> UserTurnContextWriteStatus:
    if not invocation_id:
        raise RuntimeError("user_turn_context write requires invocation_id")

    existing = await asyncio.to_thread(
        events_table.query_user_turn_context_by_invocation,
        session_id,
        invocation_id,
        spawn_id,
    )
    if existing:
        existing_payload = (
            existing.get("content") if isinstance(existing, dict) else None
        )
        if existing_payload == payload:
            return "duplicate"
        raise RuntimeError("user_turn_context payload differs for invocation_id")

    written = await asyncio.to_thread(
        events_table.add_event,
        session_id,
        "MatMaster",
        "user_turn_context",
        payload,
        task_id=task_id,
        invocation_id=invocation_id,
        spawn_id=spawn_id,
    )
    if not written:
        raise RuntimeError("user_turn_context write returned false")
    return "written"
