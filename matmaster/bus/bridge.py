"""QueueBridge -- SSE payload adapter for MessageBus events.

Consumes BusEvent objects from MessageBus and converts them to
the existing event_callback payload dict format {source, type, content, ...extra}.
"""

from typing import Any

from matmaster.types.events import (
    AssistantStateEvent,
    BohriumNodeEvent,
    BusEvent,
    CancelledEvent,
    ConfirmationRequestEvent,
    ConfirmationTimeoutEvent,
    ContextCompactionEvent,
    ErrorEvent,
    ExpRunEvent,
    FinishEvent,
    McpConnectEvent,
    McpServerStatusEvent,
    SkillHitEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    WorkspaceUploadErrorEvent,
)

from .queue import MessageBus


class QueueBridge:
    """将 MessageBus 事件桥接到现有 SSE payload 格式。

    从 MessageBus 消费 BusEvent，转换为现有 event_callback 的
    payload 格式 {source, type, content, ...extra}。
    单消费者模式。
    """

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    def next_payload(self, timeout: float | None = None) -> dict[str, Any]:
        """从 bus 消费一个事件并转换为 SSE payload dict。

        超时抛出 queue.Empty。
        """
        event = self._bus.get(timeout=timeout)
        return self._to_sse_payload(event)

    def _to_sse_payload(self, event: BusEvent) -> dict[str, Any]:  # type: ignore[arg-type]
        """将 BusEvent 转换为现有 SSE payload 格式。

        现有格式: event_callback(source, type, content, **extra)
        -> payload = {source, type, content, ...extra}
        """
        base: dict[str, Any] = {
            "source": event.source,
            "type": event.type,
        }

        if isinstance(event, ThoughtEvent):
            base["content"] = event.content
            if event.stream_state is not None:
                base["stream_state"] = event.stream_state
            if event.stream_id is not None:
                base["stream_id"] = event.stream_id
            if event.token_count:
                base["token_count"] = event.token_count
            if event.context:
                base["context"] = event.context

        elif isinstance(event, ToolCallEvent):
            base["content"] = {
                "id": event.call_id,
                "name": event.tool_name,
                "args": event.arguments,
            }

        elif isinstance(event, ToolResultEvent):
            base["content"] = {
                "id": event.call_id,
                "name": event.tool_name,
                "result": event.result,
                "info": event.info,
            }

        elif isinstance(event, FinishEvent):
            base["content"] = event.final_content or event.status

        elif isinstance(event, ErrorEvent):
            base["content"] = event.message

        elif isinstance(event, AssistantStateEvent):
            base["content"] = event.state

        elif isinstance(event, SkillHitEvent):
            base["content"] = event.skill_name

        elif isinstance(event, ExpRunEvent):
            base["content"] = event.exp_name

        elif isinstance(event, CancelledEvent):
            base["content"] = event.reason or "Task cancelled by user."

        elif isinstance(event, WorkspaceUploadErrorEvent):
            base["content"] = event.message

        elif isinstance(event, ConfirmationRequestEvent):
            content: dict[str, Any] = {
                "question": event.question,
                "mode": event.mode,
            }
            if event.timeout_seconds is not None:
                content["timeout_seconds"] = event.timeout_seconds
            if event.context:
                content["context"] = event.context
            if event.actions:
                content["actions"] = event.actions
            if event.origin:
                content["origin"] = event.origin
            base["content"] = content

        elif isinstance(event, ConfirmationTimeoutEvent):
            base["content"] = {
                "question": event.question,
                "default_reply": event.default_reply,
            }

        elif isinstance(event, ContextCompactionEvent):
            base["content"] = event.payload

        elif isinstance(event, BohriumNodeEvent):
            base["content"] = event.payload

        elif isinstance(event, McpServerStatusEvent):
            base["content"] = event.detail
            base["mcp_phase"] = event.phase
            base["mcp_server"] = event.server_name
            base["mcp_transport"] = event.transport

        elif isinstance(event, McpConnectEvent):
            content_mcp: dict[str, Any] = {
                "phase": event.phase,
                "message": event.message,
            }
            if event.elapsed_ms is not None:
                content_mcp["elapsed_ms"] = event.elapsed_ms
            if event.error:
                content_mcp["error"] = event.error
            base["content"] = content_mcp
            base["mcp_phase"] = event.phase

        return base
