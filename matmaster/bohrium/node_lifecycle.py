"""Transport-independent Bohrium Node lifecycle policy contract."""

from __future__ import annotations

from enum import Enum


class NodeLifecyclePolicy(str, Enum):
    RUN_END = "run_end"
    IDLE_TIMEOUT = "idle_timeout"
    KEEP_RUNNING = "keep_running"


NODE_IDLE_TIMEOUT_OPTIONS_SECONDS = frozenset({900, 1800, 7200})


def resolve_node_lifecycle(
    policy: str | NodeLifecyclePolicy | None,
    idle_timeout_seconds: int | None,
) -> tuple[NodeLifecyclePolicy, int | None]:
    """Validate and normalize one per-invocation lifecycle snapshot."""
    try:
        resolved = NodeLifecyclePolicy(policy or NodeLifecyclePolicy.RUN_END)
    except ValueError as exc:
        raise ValueError(
            f"unsupported Bohrium Node lifecycle policy: {policy}"
        ) from exc
    if resolved is NodeLifecyclePolicy.IDLE_TIMEOUT:
        if idle_timeout_seconds not in NODE_IDLE_TIMEOUT_OPTIONS_SECONDS:
            raise ValueError("unsupported Bohrium Node idle timeout")
        return resolved, idle_timeout_seconds
    if idle_timeout_seconds is not None:
        raise ValueError("idle timeout is only valid for idle_timeout policy")
    return resolved, None
