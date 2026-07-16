"""Typed Bohrium Node runtime failures shared across execution layers."""

from __future__ import annotations

from typing import Any


class BohriumNodeRuntimeError(RuntimeError):
    """Base error carrying stable retry semantics for tool execution."""

    error_code = "BOHRIUM_NODE_RUNTIME_ERROR"
    retryable = False
    failure_scope = "call"
    terminal_on_repeat = False
    stop_message: str | None = None

    def tool_meta(self) -> dict[str, Any]:
        """Return control metadata consumed by the runner and kernel."""
        meta: dict[str, Any] = {
            "error_code": self.error_code,
            "retryable": self.retryable,
            "failure_scope": self.failure_scope,
        }
        if self.terminal_on_repeat:
            meta["terminal_on_repeat"] = True
        if self.stop_message:
            meta["stop_message"] = self.stop_message
        return meta


class BohriumNodeConnectionInterruptedError(BohriumNodeRuntimeError):
    """The failed operation was not replayed, but the same Node reconnected."""

    error_code = "BOHRIUM_NODE_CONNECTION_INTERRUPTED"
    retryable = True

    @classmethod
    def create(cls) -> BohriumNodeConnectionInterruptedError:
        return cls(
            "The Bohrium Node connection was interrupted. The same Node has "
            "been reconnected, but the interrupted operation was not replayed "
            "because its completion state is unknown. Inspect state before "
            "retrying an operation with side effects."
        )


class BohriumNodeUnavailableError(BohriumNodeRuntimeError):
    """The run-level circuit is open after the recovery budget is exhausted."""

    error_code = "BOHRIUM_NODE_UNAVAILABLE"
    failure_scope = "run"
    terminal_on_repeat = True
    stop_message = (
        "Bohrium Node is unavailable, so Node-dependent work has been stopped "
        "for this run. Start a new turn to reconnect or choose another path."
    )

    @classmethod
    def from_failure(
        cls,
        cause: BaseException,
        *,
        reason: str,
    ) -> BohriumNodeUnavailableError:
        detail = str(cause).strip() or type(cause).__name__
        return cls(
            "Bohrium Node became unavailable and could not be recovered within "
            "this run's retry budget. Do not retry Node-dependent tools in this "
            f"run. Trigger: {reason}. Cause: {detail}"
        )
