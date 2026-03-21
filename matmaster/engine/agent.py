"""AgentKernel -- pure execution loop for the agent kernel.

Consumes an AgentRuntimeSpec and executes the LLM -> guard -> hook -> tool
-> message accumulate -> loop cycle. All termination paths go through
_finish() which produces a FinishEvent.

Termination conditions:
- natural: LLM returns no tool_calls
- max_turns: turn counter reaches spec.max_turns
- cancelled: stop_event is set externally
- hook_stopped: should_continue hook returns False
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from matmaster.types.events import FinishEvent
from matmaster.engine.guard_pipeline import GuardPipeline

if TYPE_CHECKING:
    from matmaster.types.runtime import AgentRuntimeSpec
from matmaster.engine.hooks import (
    HookAction,
    run_on_stream_chunk,
    run_post_tool_call,
    run_pre_llm_call,
    run_pre_tool_call,
    run_should_continue,
)
from matmaster.engine.types import (
    AssistantMessage,
    LLMResponse,
    Message,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)


class AgentKernel:
    """Pure execution loop -- consumes AgentRuntimeSpec, no config assembly."""

    def run(
        self,
        spec: AgentRuntimeSpec,
        task: str,
        stop_event: threading.Event | None = None,
    ) -> FinishEvent:
        """Execute the agent loop until termination.

        Termination conditions:
        - natural: LLM returns no tool_calls
        - max_turns: turn counter reaches spec.max_turns
        - cancelled: stop_event is set
        - hook_stopped: should_continue hook returns False

        Returns FinishEvent with the reason.
        """
        messages: list[Message] = [
            SystemMessage(content=spec.system_prompt),
            UserMessage(content=task),
        ]
        guard_pipeline = GuardPipeline(spec.guards)
        turn = 0

        while turn < spec.max_turns:
            # External cancel check (before each turn)
            if stop_event and stop_event.is_set():
                return self._finish(spec, messages, "cancelled")

            turn += 1

            # pre_llm_call hook (observation, all hooks called)
            run_pre_llm_call(spec.hooks, messages, turn)

            # should_continue hook (intercepting, short-circuit)
            if not run_should_continue(spec.hooks, messages, turn):
                return self._finish(spec, messages, "hook_stopped")

            # LLM call (streaming by default)
            response = self._call_llm(spec, messages)

            # Natural finish: no tool_calls
            if not response.tool_calls:
                messages.append(
                    AssistantMessage(
                        content=response.content,
                        reasoning_content=response.reasoning_content,
                    )
                )
                return self._finish(
                    spec,
                    messages,
                    "natural",
                    final_content=response.content,
                )

            # Has tool_calls: append assistant message then process each serially
            messages.append(
                AssistantMessage(
                    content=response.content,
                    tool_calls=response.tool_calls,
                    reasoning_content=response.reasoning_content,
                )
            )

            for tc in response.tool_calls:
                # Guard evaluation (before hooks)
                guard_result = guard_pipeline.evaluate(tc, turn, spec.max_turns)
                if not guard_result.allowed:
                    # Blocked: ToolMessage error response, NO hooks triggered
                    blocked_content = f"BLOCKED: {guard_result.reason}"
                    if guard_result.guidance:
                        blocked_content += f"\n{guard_result.guidance}"
                    messages.append(
                        ToolMessage(
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content=blocked_content,
                        )
                    )
                    continue

                # pre_tool_call hook (intercepting, short-circuit)
                action = run_pre_tool_call(spec.hooks, tc)
                if action == HookAction.SKIP:
                    messages.append(
                        ToolMessage(
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content="Tool call skipped by hook.",
                        )
                    )
                    continue

                # Tool execution
                result = spec.tool_registry.execute(tc.name, tc.arguments)
                messages.append(
                    ToolMessage(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=str(result),
                    )
                )

                # post_tool_call hook (observation, all hooks called)
                run_post_tool_call(spec.hooks, tc, str(result))

        # max_turns exhausted
        return self._finish(spec, messages, "max_turns")

    def _call_llm(
        self, spec: AgentRuntimeSpec, messages: list[Message]
    ) -> LLMResponse:
        """Call LLM via streaming, accumulate chunks into LLMResponse."""
        api_messages = [m.to_api_dict() for m in messages]
        tool_defs = (
            spec.tool_registry.get_tool_definitions()
            if spec.tool_registry
            and hasattr(spec.tool_registry, "get_tool_definitions")
            else None
        )

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None

        for chunk in spec.llm_provider.chat_stream(api_messages, tool_defs):
            # Forward chunk to hooks
            run_on_stream_chunk(spec.hooks, chunk)

            if chunk.content:
                content_parts.append(chunk.content)
            if chunk.reasoning_content:
                reasoning_parts.append(chunk.reasoning_content)
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.tool_call_deltas:
                for delta in chunk.tool_call_deltas:
                    idx = delta.get("index", 0)
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                    if delta.get("id"):
                        tool_calls_acc[idx]["id"] = delta["id"]
                    if delta.get("name"):
                        tool_calls_acc[idx]["name"] += delta["name"]
                    if delta.get("arguments"):
                        tool_calls_acc[idx]["arguments"] += delta["arguments"]

        # Assemble tool_calls from accumulated deltas
        tool_calls: list[ToolCallData] | None = None
        if tool_calls_acc:
            tool_calls = []
            for _, v in sorted(tool_calls_acc.items()):
                args = self._parse_arguments(v["arguments"])
                tool_calls.append(
                    ToolCallData(id=v["id"], name=v["name"], arguments=args)
                )

        return LLMResponse(
            content="".join(content_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _parse_arguments(raw: str) -> dict[str, Any]:
        """Parse JSON arguments string from streaming accumulation."""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse tool call arguments: %s", raw[:200])
            return {"_raw": raw}

    @staticmethod
    def _finish(
        spec: AgentRuntimeSpec,
        messages: list[Message],
        reason: str,
        final_content: str | None = None,
    ) -> FinishEvent:
        """Unified exit path -- all termination goes through here."""
        status = "cancelled" if reason == "cancelled" else "completed"
        return FinishEvent(
            source="agent",
            status=status,
            reason=reason,
            final_content=final_content,
        )
