"""AgentKernel -- pure async execution loop for the agent kernel.

Consumes an AgentKernelRuntime plus AgentKernelTurnRequest and executes the
LLM -> hook -> tool -> message accumulate -> loop cycle via run_stream(), the
sole public API.
run_stream() yields BusEvent objects through the _run_items() generator.

Termination conditions:
- natural: LLM returns no tool_calls
- max_turns: turn counter reaches kernel_spec.max_turns
- cancelled: cancel_token is set (checked each turn, during stream chunks, retry
  backoff, and between serial tool_calls)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from matmaster.core.agent_compaction import (
    run_preflight_compaction_if_needed,
    run_runtime_compaction_if_needed,
)
from matmaster.core.agent_llm_stream import call_llm_streaming
from matmaster.core.agent_tool_dispatch import (
    InvalidToolUsageDelta,
    accumulate_usage,
    dispatch_tool_calls,
    validate_tool_call_ids,
)
from matmaster.core.finish_diagnostics import (
    build_finish_detail,
    is_valid_natural_finish,
)
from matmaster.core.kernel_items import (
    _KernelItem,
    _KernelState,
    _KernelStopRequested,
    _TerminalItem,
)
from matmaster.types.cancellation import CancellationToken
from matmaster.types.events import (
    AssistantStateEvent,
    CheckpointEvent,
    FinishDetail,
    ResponseEvent,
    ThoughtEvent,
    ToolCallEvent,
)

if TYPE_CHECKING:
    from matmaster.types.runtime import (
        AgentKernelResources,
        AgentKernelRuntime,
        AgentKernelSpec,
        AgentKernelTurnRequest,
    )

from matmaster.core.hooks import HookEvent, RunContext, UserPromptContext
from matmaster.response_text import is_trivial_response_text
from matmaster.types.message_normalization import (
    apply_tool_image_budget,
    validate_tool_turn_sequence,
)
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    Message,
    SystemMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)


_TERMINAL_REASON_TO_STATUS: dict[str, str] = {
    "cancelled": "cancelled",
    "interrupted": "completed",
    "internal_error": "failed",
    "invalid_finish": "failed",
    "natural": "completed",
    "max_turns": "completed",
}


def ensure_tool_definitions(
    kernel_resources: AgentKernelResources,
    state: _KernelState,
) -> list[dict[str, Any]] | None:
    """Resolve and cache tool definitions on kernel state."""
    if kernel_resources.tool_catalog is None:
        return None

    if kernel_resources.tool_catalog.version != state.last_catalog_version:
        state.cached_tool_definitions = None
        state.last_catalog_version = kernel_resources.tool_catalog.version

    if state.cached_tool_definitions is None:
        from matmaster.types.tool_desc_ctx import ToolDescriptionContext

        desc_ctx = None
        if kernel_resources.runtime_topology is not None:
            desc_ctx = ToolDescriptionContext(
                session_kind=kernel_resources.runtime_topology.session_kind,
                workspace_root=kernel_resources.runtime_topology.workspace_root,
                topology=kernel_resources.runtime_topology,
            )
        state.cached_tool_definitions = kernel_resources.tool_catalog.build_definitions(
            desc_ctx
        )
    return state.cached_tool_definitions


class AgentKernel:
    """Pure execution loop -- consumes AgentKernelRuntime, no config assembly."""

    async def run_stream(
        self,
        kernel_runtime: AgentKernelRuntime,
        turn_request: AgentKernelTurnRequest,
        history: list[Message] | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> AsyncIterator[Any]:
        """Generator-first entry point: yields BusEvent objects.

        Consumes _KernelItem from _run_items(), extracts .event for
        non-terminal items, and converts terminal items to RunResultEvent.
        Items with event=None (llm_response) are consumed internally and
        not yielded.
        """
        from matmaster.types.events import RunResultEvent

        kernel_spec = kernel_runtime.spec
        kernel_resources = kernel_runtime.resources

        async with kernel_resources.llm_provider:
            last_reason: str | None = None

            async def _consume_and_yield():
                nonlocal last_reason
                async for item in self._run_items(
                    kernel_spec,
                    kernel_resources,
                    turn_request,
                    history,
                    cancel_token,
                ):
                    if item.terminal is not None:
                        reason = item.terminal.reason
                        last_reason = reason
                        status = _TERMINAL_REASON_TO_STATUS.get(reason, "completed")
                        yield RunResultEvent(
                            source="agent",
                            status=status,
                            reason=reason,
                            final_content=item.terminal.final_content,
                            num_turns=item.terminal.num_turns,
                            usage=item.terminal.usage,
                            usage_vendor_by_turn=item.terminal.usage_vendor_by_turn,
                            messages=item.terminal.messages,
                            finish_detail=item.terminal.finish_detail,
                            model=item.terminal.model,
                            model_profile=item.terminal.model_profile,
                            model_route=item.terminal.model_route,
                        )
                        return
                    if item.event is not None:
                        yield item.event

            if kernel_resources.hook_executor is not None:
                await kernel_resources.hook_executor.emit(
                    HookEvent.RUN_START,
                    RunContext(
                        task_id=kernel_spec.run_identity.task_id,
                        session_id=kernel_spec.run_identity.session_id,
                        reason="startup",
                    ),
                )

            try:
                async for event in _consume_and_yield():
                    yield event
            except BaseException as exc:
                if last_reason is None:
                    if isinstance(exc, (GeneratorExit, asyncio.CancelledError)):
                        last_reason = "cancelled"
                    else:
                        last_reason = "error"
                raise
            finally:
                interrupt_checker = kernel_resources.runtime_ports.interrupt_checker
                if interrupt_checker is not None:
                    interrupt_checker.cleanup()
                if kernel_resources.hook_executor is not None:
                    await kernel_resources.hook_executor.emit(
                        HookEvent.RUN_END,
                        RunContext(
                            task_id=kernel_spec.run_identity.task_id,
                            session_id=kernel_spec.run_identity.session_id,
                            reason=last_reason or "error",
                        ),
                    )

    @staticmethod
    def _with_model_identity(
        item: _KernelItem,
        state: _KernelState,
    ) -> _KernelItem:
        """Attach resolved model identity to persisted assistant output events."""
        event = item.event
        if not isinstance(event, ResponseEvent):
            return item
        if event.stream_state != "complete":
            return item
        return _KernelItem(
            event=event.model_copy(
                update={
                    "model": state.llm_model,
                    "model_profile": state.llm_model_profile,
                    "model_route": state.llm_model_route,
                }
            ),
            llm_response=item.llm_response,
            terminal=item.terminal,
        )

    @staticmethod
    def _terminal(
        state: _KernelState,
        reason: str,
        *,
        final_content: str | None = None,
        turn_offset: int = 0,
        finish_detail: FinishDetail | None = None,
    ) -> _KernelItem:
        return _KernelItem(
            terminal=_TerminalItem(
                reason=reason,
                final_content=final_content,
                num_turns=state.turn + turn_offset,
                usage=dict(state.total_usage),
                usage_vendor_by_turn=[
                    dict(item) for item in state.usage_vendor_by_turn
                ],
                messages=list(state.messages),
                finish_detail=finish_detail,
                model=state.llm_model,
                model_profile=state.llm_model_profile,
                model_route=state.llm_model_route,
            )
        )

    async def _run_items(
        self,
        kernel_spec: AgentKernelSpec,
        kernel_resources: AgentKernelResources,
        turn_request: AgentKernelTurnRequest,
        history: list[Message] | None,
        cancel_token: CancellationToken | None,
    ) -> AsyncIterator[_KernelItem]:
        """Core generator loop: yields _KernelItem for each event.

        Yields events for streaming, AssistantState, and SkillHit.
        """
        task = turn_request.user_message_content
        turn_input = turn_request.turn_input
        if kernel_resources.hook_executor is not None:
            session_id = kernel_spec.run_identity.session_id
            if kernel_spec.prompt_submit_rewrite_enabled:
                prompt_ctx = UserPromptContext(prompt=task, session_id=session_id)
                task = await kernel_resources.hook_executor.emit_rewrite(
                    HookEvent.USER_PROMPT_SUBMIT,
                    prompt_ctx,
                    task,
                )
            await kernel_resources.hook_executor.emit(
                HookEvent.USER_PROMPT_SUBMIT,
                UserPromptContext(prompt=task, session_id=session_id),
            )

        turn_images = (
            list(turn_input.attachments.images_as_parts())
            if turn_input is not None
            else []
        )
        state = _KernelState(
            messages=[
                SystemMessage(content=kernel_spec.system_prompt),
                *(history or []),
                UserMessage(content=task, images=turn_images),
            ],
            llm_model=kernel_spec.llm_model,
            llm_model_profile=kernel_spec.llm_model_profile,
            llm_model_route=kernel_spec.llm_model_route,
        )

        checkpoint_sink = kernel_resources.runtime_ports.checkpoint_sink
        tool_definitions = ensure_tool_definitions(kernel_resources, state)

        async for item in run_preflight_compaction_if_needed(
            kernel_spec=kernel_spec,
            kernel_resources=kernel_resources,
            state=state,
            history=history,
            turn_input=turn_input,
            checkpoint_sink=checkpoint_sink,
            tool_definitions=tool_definitions,
        ):
            yield item

        while state.turn < kernel_spec.max_turns:
            if cancel_token and cancel_token.is_cancelled:
                yield self._terminal(state, "cancelled")
                return

            state.turn += 1
            tool_definitions = ensure_tool_definitions(kernel_resources, state)

            async for item in run_runtime_compaction_if_needed(
                kernel_spec=kernel_spec,
                kernel_resources=kernel_resources,
                state=state,
                checkpoint_sink=checkpoint_sink,
                turn_input=turn_input,
                tool_definitions=tool_definitions,
            ):
                yield item

            tool_defs = tool_definitions

            canonical_messages = state.pipeline.feed_tail(state.messages)
            canonical_messages = apply_tool_image_budget(canonical_messages)
            # 有意与 transport.convert_messages 双重校验：内核边界保证对任意
            # provider 实现都在发起调用前 fail-fast（test_tool_protocol_guardrails）
            validate_tool_turn_sequence(canonical_messages)

            llm_response: LLMResponse | None = None
            thought_persisted_this_turn = False
            try:
                async for item in self._call_llm_streaming(
                    kernel_resources,
                    canonical_messages,
                    tool_defs,
                    cancel_token=cancel_token,
                ):
                    if item.llm_response is not None:
                        llm_response = item.llm_response
                    elif (
                        isinstance(item.event, ThoughtEvent)
                        and item.event.stream_state == "complete"
                    ):
                        if not thought_persisted_this_turn:
                            thought_persisted_this_turn = True
                            yield item
                    elif item.event is not None:
                        yield self._with_model_identity(item, state)
            except _KernelStopRequested:
                yield self._terminal(state, "cancelled")
                return

            if llm_response is None:
                yield self._terminal(
                    state,
                    "invalid_finish",
                    finish_detail=build_finish_detail(None),
                )
                return

            response = llm_response
            state.turn_usage = response.usage
            accumulate_usage(state.total_usage, response.usage)
            state.usage_vendor_by_turn.append(
                dict(response.usage_vendor) if response.usage_vendor else {}
            )
            turn_index = state.turn - 1
            turn_usage_snapshot = dict(state.turn_usage)
            total_usage_snapshot = dict(state.total_usage)
            usage_vendor_snapshot = response.usage_vendor or None
            if not thought_persisted_this_turn and response.reasoning_content:
                yield _KernelItem(
                    event=ThoughtEvent(
                        source="agent",
                        content=response.reasoning_content,
                        stream_state="complete",
                        reasoning_content=response.reasoning_content,
                    )
                )
            is_root_run = kernel_spec.run_identity.spawn_id is None
            if (
                is_root_run
                and response.content
                and not is_trivial_response_text(response.content)
            ):
                state.last_emitted_content = response.content
                yield _KernelItem(
                    event=ResponseEvent(
                        source="agent",
                        content=response.content,
                        stream_state="complete",
                        turn_index=turn_index,
                        turn_usage=turn_usage_snapshot,
                        total_usage=total_usage_snapshot,
                        usage_vendor=usage_vendor_snapshot,
                        model=state.llm_model,
                        model_profile=state.llm_model_profile,
                        model_route=state.llm_model_route,
                    )
                )
            if kernel_resources.compactor:
                kernel_resources.compactor.update_message_count(len(state.messages))

            if response.tool_calls:
                validate_tool_call_ids(response.tool_calls)

            if not response.tool_calls:
                if not is_valid_natural_finish(response):
                    if state.last_emitted_content and response.finish_reason == "stop":
                        yield self._terminal(
                            state,
                            "natural",
                            final_content=state.last_emitted_content,
                        )
                    else:
                        yield self._terminal(
                            state,
                            "invalid_finish",
                            finish_detail=build_finish_detail(response),
                        )
                    return
                natural_msg = AssistantMessage(
                    content=response.content,
                    reasoning_content=response.reasoning_content,
                    provider_state=response.provider_state,
                )
                state.messages.append(natural_msg)
                if response.provider_state is not None:
                    yield _KernelItem(
                        event=AssistantStateEvent(
                            source="agent",
                            state=natural_msg.model_dump(mode="json"),
                            turn_index=turn_index,
                            turn_usage=dict(state.turn_usage),
                            total_usage=dict(state.total_usage),
                            model=state.llm_model,
                            model_profile=state.llm_model_profile,
                            model_route=state.llm_model_route,
                        )
                    )
                yield self._terminal(state, "natural", final_content=response.content)
                return

            assistant_msg = AssistantMessage(
                content=response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
                provider_state=response.provider_state,
            )
            assistant_finish_detail = None
            if response.finish_reason == "length":
                assistant_finish_detail = build_finish_detail(response)
                logger.warning(
                    "tool call response ended with length finish reason",
                    extra={
                        "turn": state.turn,
                        "tool_names": [tc.name for tc in response.tool_calls or []],
                        "finish_detail": assistant_finish_detail.model_dump(
                            mode="json"
                        ),
                    },
                )
            state.messages.append(assistant_msg)

            if assistant_msg.tool_calls:
                yield _KernelItem(
                    event=AssistantStateEvent(
                        source="agent",
                        state=assistant_msg.model_dump(mode="json"),
                        turn_index=turn_index,
                        turn_usage=dict(state.turn_usage),
                        total_usage=dict(state.total_usage),
                        finish_detail=assistant_finish_detail,
                        model=state.llm_model,
                        model_profile=state.llm_model_profile,
                        model_route=state.llm_model_route,
                    )
                )

            for tc in response.tool_calls:
                yield _KernelItem(
                    event=ToolCallEvent(
                        source="agent",
                        call_id=tc.id,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                        turn_index=turn_index,
                        turn_usage=turn_usage_snapshot,
                        total_usage=total_usage_snapshot,
                        usage_vendor=usage_vendor_snapshot,
                    )
                )

            # Checkpoint: check if user has queued messages to interrupt
            interrupt_checker = kernel_resources.runtime_ports.interrupt_checker
            if interrupt_checker is not None and interrupt_checker.has_hint():
                yield _KernelItem(
                    event=CheckpointEvent(
                        source="agent",
                        turn_index=turn_index,
                    )
                )
                confirmed = await interrupt_checker.wait_for_confirm(timeout=3.0)
                interrupt_checker.cleanup()
                if confirmed:
                    yield self._terminal(
                        state, "interrupted", final_content=response.content
                    )
                    return

            try:
                async for item in dispatch_tool_calls(
                    tool_calls=response.tool_calls,
                    tool_runner=kernel_resources.tool_runner,
                    max_turns=kernel_spec.max_turns,
                    state=state,
                    cancel_token=cancel_token,
                ):
                    yield item
            except InvalidToolUsageDelta:
                logger.exception("malformed tool usage delta; ending run as failed")
                yield self._terminal(state, "internal_error")
                return

        yield self._terminal(state, "max_turns")

    async def _call_llm_streaming(
        self,
        kernel_resources: AgentKernelResources,
        canonical_messages: list[Message],
        tool_defs: list[dict[str, Any]] | None,
        *,
        cancel_token: CancellationToken | None = None,
    ) -> AsyncIterator[_KernelItem]:
        """Indirection point so tests can monkey-patch the streaming call."""
        async for item in call_llm_streaming(
            kernel_resources,
            canonical_messages,
            tool_defs,
            cancel_token=cancel_token,
        ):
            yield item
