"""Subagent orchestration -- the cross-run concern lifted out of Exp.

A :class:`SubagentOrchestrator` owns one parent agent's spawn lifecycle:

  * mint a per-child ``spawn_id``
  * emit ``SUBAGENT_START`` / ``SUBAGENT_STOP`` hooks
  * retag each child event's ``source`` / ``spawn_id`` and multiplex it back
    into the parent stream via the child-event sink
  * drain the child run and extract its final content

It depends only on narrow seams -- a child-run factory (which actually builds
and runs the child agent), a hook executor, and the child-event sink -- never on
the service fanout, ``AgentRunContext``, or Exp internals. Exp keeps the job of
*assembling* the child runtime (the factory it injects); the orchestrator owns
*running* the spawn.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from matmaster.core.hooks import HookEvent, HookExecutor, SubagentContext
from matmaster.types.cancellation import CancellationToken
from matmaster.types.stream_drain import DrainResult

logger = logging.getLogger(__name__)

# Builds the event stream for one child agent run. Exp injects the concrete
# implementation (load child config -> child Exp(allow_spawn=False) ->
# child.run_stream(...)); the orchestrator only consumes the stream.
ChildRunFactory = Callable[..., AsyncIterator[Any]]

# Receives each already-retagged child event for multiplexing into the parent
# stream. May be sync or async.
ChildEventSink = Callable[[Any], Any]


class SubagentOrchestrator:
    """Run child agents on behalf of one parent agent run."""

    def __init__(
        self,
        *,
        child_run_factory: ChildRunFactory,
        child_event_sink: ChildEventSink | None = None,
        hook_executor: HookExecutor | None = None,
        parent_session_id: str = "",
        source_prefix: str = "MatMaster",
    ) -> None:
        self._child_run_factory = child_run_factory
        self._child_event_sink = child_event_sink
        self._hook_executor = hook_executor
        self._parent_session_id = parent_session_id
        self._source_prefix = source_prefix

    def make_spawn_fn(self) -> Callable[..., Awaitable[DrainResult]]:
        """Return the ``spawn_fn`` closure AgentTool forwards LLM calls to."""

        async def spawn_fn(
            exp_name: str,
            task: str,
            cancel_token: CancellationToken | None = None,
        ) -> DrainResult:
            return await self.spawn(exp_name, task, cancel_token=cancel_token)

        return spawn_fn

    async def spawn(
        self,
        exp_name: str,
        task: str,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> DrainResult:
        """Run one child agent and return its drained terminal result."""
        from matmaster.core.stream_drain import drain_run_stream

        child_source = f"{self._source_prefix}:{exp_name}"
        spawn_id = uuid.uuid4().hex[:16]

        async def _forward_child_event(event: Any) -> None:
            sink = self._child_event_sink
            if sink is None:
                return
            try:
                forwarded = event.model_copy(
                    update={"source": child_source, "spawn_id": spawn_id}
                )
                result = sink(forwarded)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.warning(
                    "subagent event forwarding failed type=%s spawn_id=%s",
                    getattr(event, "type", "?"),
                    spawn_id,
                    exc_info=True,
                )

        await self._emit(HookEvent.SUBAGENT_START, spawn_id, exp_name, task)
        try:
            return await drain_run_stream(
                self._child_run_factory(
                    exp_name, task, cancel_token=cancel_token, spawn_id=spawn_id
                ),
                on_event=_forward_child_event,
            )
        finally:
            await self._emit(HookEvent.SUBAGENT_STOP, spawn_id, exp_name, task)

    async def _emit(
        self, event: HookEvent, spawn_id: str, exp_name: str, task: str
    ) -> None:
        if self._hook_executor is None:
            return
        await self._hook_executor.emit(
            event,
            SubagentContext(
                agent_id=spawn_id,
                agent_type=exp_name,
                parent_session_id=self._parent_session_id,
                task_preview=task[:200],
            ),
        )
