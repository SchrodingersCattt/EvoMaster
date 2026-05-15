"""Phase-1 helpers for durable user-turn context events."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

from matmaster.core.context_builder import ContextBuilder
from matmaster.types.messages import ImageContentPart, UserMessage
from src.services.agent_run_instructions import (
    _USER_INSTRUCTIONS_PATH,
    _render_user_instructions_block,
)

logger = logging.getLogger(__name__)

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


def _context_events_newest_first(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    event_ids = [event.get("id") for event in events]
    if event_ids and all(isinstance(event_id, int) for event_id in event_ids):
        return sorted(events, key=lambda event: int(event["id"]), reverse=True)
    return list(events)


def latest_anchor_user_instructions_hash(events: list[dict[str, Any]]) -> str | None:
    """Return the most recent user-instructions anchor hash.

    DAO-backed event rows carry monotonically increasing integer ids; normalize
    those to newest-first so callers cannot accidentally pass chronological
    history and pierce a checkpoint barrier. Synthetic tests without ids are
    interpreted in the already-newest-first order used by
    get_recent_context_anchor_events().
    """
    for event in _context_events_newest_first(events):
        event_type = event.get("type")
        content = event.get("content") or {}
        if not isinstance(content, dict):
            content = {}

        if event_type == "user_turn_context":
            if content.get("kind") != "anchor":
                continue
            anchor_hash = content.get("user_instructions_hash")
            return anchor_hash if isinstance(anchor_hash, str) and anchor_hash else None

        if event_type == "history_checkpoint":
            checkpoint_hash = content.get("user_instructions_hash")
            return (
                checkpoint_hash
                if isinstance(checkpoint_hash, str) and checkpoint_hash
                else None
            )

    return None


def decide_user_turn_context_kind(
    current_hash: str,
    latest_anchor_hash: str | None,
) -> UserTurnContextKind:
    if not latest_anchor_hash or latest_anchor_hash != current_hash:
        return "anchor"
    return "continuation"


def render_runtime_task_for_user_turn_context(
    *,
    user_prompt: str,
    user_instructions: UserInstructionsInfo,
    kind: UserTurnContextKind,
) -> str:
    prompt = (user_prompt or "").strip()
    if kind == "anchor" and user_instructions.text.strip():
        return _render_user_instructions_block(
            user_instructions=user_instructions.text,
            user_query=prompt,
        )
    return prompt


def build_user_turn_context_payload(
    *,
    kind: UserTurnContextKind,
    rendered_message_content: str,
    images: list[dict[str, Any] | ImageContentPart],
    user_instructions: UserInstructionsInfo,
    transform: UserTurnContextTransform = DEFAULT_TURN_TRANSFORM,
) -> dict[str, Any]:
    validated_images = [
        image
        if isinstance(image, ImageContentPart)
        else ImageContentPart.model_validate(image)
        for image in images
    ]
    message = UserMessage(content=rendered_message_content, images=validated_images)

    return {
        "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
        "kind": kind,
        "message": message.model_dump(mode="json"),
        "user_instructions_hash": (
            user_instructions.hash if kind == "anchor" else None
        ),
        "transform": transform,
        "render_version": USER_CONTEXT_RENDER_VERSION,
    }


def render_provider_facing_current_message_content(
    *,
    rendered_runtime_task: str,
    attachment_text: str | None,
) -> str:
    return ContextBuilder().build_user_request(
        user_text=rendered_runtime_task,
        attachments=attachment_text,
    )


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
        existing_payload = existing.get("content") if isinstance(existing, dict) else None
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
