"""Per-run lazy Bohrium Node acquisition coordinator."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from enum import Enum

from matmaster.types.bohrium_node_approval import (
    BohriumNodeStartApprovalGate,
    BohriumNodeStartRequest,
)
from matmaster.types.cancellation import CancellationToken
from matmaster.types.runtime_ports import BohriumNodeBinding


class _AcquireState(str, Enum):
    COLD = "cold"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


class BohriumNodeRuntimeCoordinator:
    """Single-flight bridge from tool demand to the existing Node setup path."""

    def __init__(
        self,
        acquire_binding: Callable[
            [Callable[[], bool], str, int | None], BohriumNodeBinding
        ],
        *,
        approval_gate: BohriumNodeStartApprovalGate | None = None,
        request_id: str | None = None,
        default_lifecycle_policy: str = "run_end",
        default_idle_timeout_seconds: int | None = None,
        node_sku_id: int | None = None,
    ) -> None:
        self._acquire_binding = acquire_binding
        self._approval_gate = approval_gate
        self._approval_lock = asyncio.Lock()
        self._request_id = request_id
        self._selected_lifecycle_policy = default_lifecycle_policy
        self._selected_idle_timeout_seconds = default_idle_timeout_seconds
        self._node_sku_id = node_sku_id
        self._approval_complete = approval_gate is None
        self._condition = threading.Condition()
        self._state = _AcquireState.COLD
        self._binding: BohriumNodeBinding | None = None
        self._error: RuntimeError | None = None
        self._first_reason: str | None = None
        self._close_requested = False
        self._stop_event = threading.Event()

    @property
    def acquired(self) -> bool:
        with self._condition:
            return self._binding is not None

    @property
    def first_reason(self) -> str | None:
        with self._condition:
            return self._first_reason

    async def ensure_ready(
        self,
        *,
        reason: str,
        cancel_token: CancellationToken | None = None,
    ) -> BohriumNodeBinding:
        await self._ensure_approved(reason=reason)
        return await asyncio.to_thread(
            self.ensure_ready_sync,
            reason=reason,
            cancel_token=cancel_token,
        )

    async def _ensure_approved(self, *, reason: str) -> None:
        if self._approval_gate is None:
            return
        async with self._approval_lock:
            with self._condition:
                if self._approval_complete:
                    return
                if self._state is _AcquireState.FAILED:
                    assert self._error is not None
                    raise self._error
                if self._state is _AcquireState.CLOSED or self._close_requested:
                    raise RuntimeError("Bohrium Node acquisition is closed")
                if self._first_reason is None:
                    self._first_reason = reason

            try:
                decision = await self._approval_gate.review(
                    BohriumNodeStartRequest(
                        request_id=self._request_id or "bohrium-node-start",
                        trigger_reason=self._first_reason or reason,
                        default_lifecycle_policy=self._selected_lifecycle_policy,
                        default_idle_timeout_seconds=(
                            self._selected_idle_timeout_seconds
                        ),
                        node_sku_id=self._node_sku_id,
                    )
                )
            except Exception as exc:
                error = RuntimeError(f"Bohrium Node approval failed: {exc}")
                self._fail(error)
                raise error from exc

            if not decision.approved or decision.lifecycle_policy is None:
                messages = {
                    "rejected": "Bohrium Node start was rejected by the user",
                    "busy": "Bohrium Node start approval is busy",
                    "timeout": "Bohrium Node start approval timed out",
                    "cancelled": "Bohrium Node start approval was cancelled",
                    "invalid": "Bohrium Node start approval reply was invalid",
                }
                error = RuntimeError(
                    messages.get(
                        decision.review_outcome,
                        "Bohrium Node start was not approved",
                    )
                )
                self._fail(error)
                raise error

            with self._condition:
                self._selected_lifecycle_policy = decision.lifecycle_policy
                self._selected_idle_timeout_seconds = decision.idle_timeout_seconds
                self._approval_complete = True
                self._condition.notify_all()

    def _fail(self, error: RuntimeError) -> None:
        with self._condition:
            self._error = error
            self._state = _AcquireState.FAILED
            self._condition.notify_all()

    def ensure_ready_sync(
        self,
        *,
        reason: str,
        cancel_token: CancellationToken | None = None,
    ) -> BohriumNodeBinding:
        is_starter = False
        with self._condition:
            while True:
                if cancel_token is not None and cancel_token.is_cancelled:
                    raise RuntimeError("Bohrium Node acquisition cancelled")
                if self._state is _AcquireState.READY and self._binding is not None:
                    return self._binding
                if self._state is _AcquireState.FAILED:
                    assert self._error is not None
                    raise self._error
                if self._state is _AcquireState.CLOSED or self._close_requested:
                    raise RuntimeError("Bohrium Node acquisition is closed")
                if self._state is _AcquireState.COLD:
                    if not self._approval_complete:
                        raise RuntimeError(
                            "Bohrium Node requires asynchronous user approval "
                            "before synchronous acquisition"
                        )
                    self._state = _AcquireState.STARTING
                    if self._first_reason is None:
                        self._first_reason = reason
                    is_starter = True
                    break
                self._condition.wait(timeout=0.1)

        if not is_starter:  # pragma: no cover - loop exits only for the starter
            raise RuntimeError("Bohrium Node acquisition state error")

        try:
            binding = self._acquire_binding(
                lambda: self._stop_event.is_set()
                or bool(cancel_token and cancel_token.is_cancelled),
                self._selected_lifecycle_policy,
                self._selected_idle_timeout_seconds,
            )
        except Exception as exc:
            error = RuntimeError(str(exc))
            with self._condition:
                self._error = error
                self._state = _AcquireState.FAILED
                self._condition.notify_all()
            raise error from exc

        with self._condition:
            self._binding = binding
            self._state = (
                _AcquireState.CLOSED if self._close_requested else _AcquireState.READY
            )
            self._condition.notify_all()
            if self._state is _AcquireState.CLOSED:
                raise RuntimeError("Bohrium Node acquisition completed after close")
            return binding

    async def close(self) -> None:
        """Fence acquisition and wait for an in-flight provider setup to settle."""
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._close_requested = True
            while self._state is _AcquireState.STARTING:
                self._condition.wait(timeout=0.1)
            if self._state in {_AcquireState.COLD, _AcquireState.FAILED}:
                self._state = _AcquireState.CLOSED
            elif self._state is _AcquireState.READY:
                self._state = _AcquireState.CLOSED
            self._condition.notify_all()
