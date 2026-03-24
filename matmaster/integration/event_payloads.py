"""Public-facing event payload transformations.

Shared by PersistenceHandler and SSEHandler to normalize internal
bus events into the frontend SSE / persistence contract.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_public_source(source: object) -> str:
    """Collapse internal source labels to the public SSE set."""
    raw = str(source or "").strip()
    if raw in {"User", "System"}:
        return raw
    return "MatMaster"


def _flatten_bohrium_content(raw_payload: object) -> object:
    """Unwrap Bohrium callback payloads into the frontend-facing content shape."""
    if not isinstance(raw_payload, dict):
        return raw_payload

    nested = raw_payload.get("content")
    extras = {
        key: value
        for key, value in raw_payload.items()
        if key not in {"content", "type"}
    }

    if isinstance(nested, dict):
        content: dict[str, Any] = {**nested, **extras}
    elif nested is None:
        content = extras
    else:
        content = {"message": nested, **extras}

    event_type = raw_payload.get("type")
    if event_type is not None and "event_type" not in content:
        content["event_type"] = event_type

    return content


_CONTENT_META_KEYS = frozenset({"type", "source", "timestamp"})


def _public_content_for_event(
    event_type: str, payload: dict[str, Any]
) -> object | None:
    """Adapt internal event payloads to the frontend SSE contract."""
    if event_type == "tool_call":
        call_id = payload.get("call_id")
        return {
            "id": call_id,
            "call_id": call_id,
            "name": payload.get("tool_name"),
            "args": payload.get("arguments") or {},
        }

    if event_type == "tool_result":
        call_id = payload.get("call_id")
        return {
            "id": call_id,
            "call_id": call_id,
            "name": payload.get("tool_name"),
            "result": payload.get("result"),
            "info": payload.get("info") or {},
        }

    if event_type == "confirmation_request":
        return {
            "question": payload.get("question"),
            "mode": payload.get("mode"),
            "timeout_seconds": payload.get("timeout_seconds"),
            "context": payload.get("context"),
            "actions": payload.get("actions") or [],
            "origin": payload.get("origin"),
        }

    if event_type == "error":
        return {
            "message": payload.get("message"),
            "traceback": payload.get("traceback"),
        }

    if event_type == "workspace_upload_error":
        return {"message": payload.get("message")}

    if event_type == "bohrium_node":
        return _flatten_bohrium_content(payload.get("payload"))

    if event_type == "mcp_server_status":
        detail = payload.get("detail")
        content = {
            "server_name": payload.get("server_name"),
            "transport": payload.get("transport"),
            "phase": payload.get("phase"),
        }
        if isinstance(detail, dict):
            content.update(detail)
        return content

    if event_type == "mcp_connect":
        return {
            "phase": payload.get("phase"),
            "message": payload.get("message"),
            "elapsed_ms": payload.get("elapsed_ms"),
            "error": payload.get("error"),
        }

    if event_type == "context_compaction":
        return payload.get("payload")

    if event_type in ("run_result", "finish"):
        return {
            "content": payload.get("final_content") or "",
            "status": payload.get("status"),
            "reason": payload.get("reason"),
        }

    if event_type == "assistant_state":
        return payload.get("state")

    if event_type == "skill_hit":
        return {"skill_name": payload.get("skill_name")}

    if event_type == "cancelled":
        return {"reason": payload.get("reason", "")}

    if event_type == "confirmation_timeout":
        return {
            "question": payload.get("question"),
            "default_reply": payload.get("default_reply"),
        }

    if event_type == "exp_run":
        return {"exp_name": payload.get("exp_name")}

    raw_content = payload.get("content")
    if raw_content is not None:
        return raw_content

    extracted = {
        key: value for key, value in payload.items() if key not in _CONTENT_META_KEYS
    }
    if extracted:
        logger.warning(
            "No explicit content mapping for event type=%s, using extracted fields",
            event_type,
        )
        return extracted

    return None
