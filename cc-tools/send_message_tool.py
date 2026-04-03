"""SendMessage tool -- CC-style agent-to-agent communication.

Supports:
- Point-to-point messages between named agents
- Broadcast to all teammates via "*"
- Structured protocol messages (shutdown, plan approval)
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Awaitable, ClassVar

from .base import BuiltinTool, ToolResult

# Protocol message types
PROTOCOL_TYPES = frozenset({
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
})


class SendMessageTool(BuiltinTool):
    """Send messages to other agents for multi-agent collaboration."""

    name: ClassVar[str] = "SendMessage"
    description: ClassVar[str] = (
        "Send a message to another agent.\n\n"
        "Usage:\n"
        '- to: "researcher" -- send to teammate by name\n'
        '- to: "*" -- broadcast to all teammates (expensive, use sparingly)\n\n'
        "Your plain text output is NOT visible to other agents. "
        "To communicate, you MUST call this tool.\n\n"
        "Protocol responses:\n"
        "If you receive a JSON message with type 'shutdown_request' or "
        "'plan_approval_request', respond with the matching _response type."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": 'Recipient: teammate name, or "*" for broadcast',
            },
            "summary": {
                "type": "string",
                "description": "5-10 word preview summary (required for text messages)",
            },
            "message": {
                "description": "Message content (string or structured protocol message)",
                "oneOf": [
                    {
                        "type": "string",
                        "description": "Plain text message",
                    },
                    {
                        "type": "object",
                        "description": "Structured protocol message",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": list(PROTOCOL_TYPES),
                            },
                            "request_id": {"type": "string"},
                            "approve": {"type": "boolean"},
                            "reason": {"type": "string"},
                            "feedback": {"type": "string"},
                        },
                        "required": ["type"],
                    },
                ],
            },
        },
        "required": ["to", "message"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        send_fn: Callable[..., Awaitable[None]] | None = None,
        agent_id: str = "main",
        teammates: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            send_fn: Async function(to, message, summary) to deliver messages.
            agent_id: This agent's identifier (for routing).
            teammates: Map of teammate_name -> agent_id for resolution.
        """
        super().__init__(session=session, workdir=workdir)
        self._send_fn = send_fn
        self._agent_id = agent_id
        self._teammates = teammates or {}

    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """Override for async message delivery."""
        to: str = arguments.get("to", "")
        message: Any = arguments.get("message", "")
        summary: str = arguments.get("summary", "")

        if not to:
            return "Error: 'to' is required"
        if not message:
            return "Error: 'message' is required"

        # Validate text messages require summary
        if isinstance(message, str) and not summary:
            return "Error: 'summary' is required for text messages"

        # Determine if this is a protocol message
        is_protocol = isinstance(message, dict) and message.get("type") in PROTOCOL_TYPES

        # Resolve recipient
        if to == "*":
            recipients = list(self._teammates.keys())
            if not recipients:
                return "Error: no teammates available for broadcast"
        else:
            if to not in self._teammates and self._send_fn is None:
                available = ", ".join(sorted(self._teammates.keys())) or "(none)"
                return f"Error: unknown teammate '{to}'. Available: {available}"
            recipients = [to]

        # Deliver message
        if self._send_fn is not None:
            try:
                for recipient in recipients:
                    await self._send_fn(
                        to=recipient,
                        message=message,
                        summary=summary,
                        from_agent=self._agent_id,
                    )
            except Exception as e:
                return f"Error: failed to send message: {e}"

            if len(recipients) == 1:
                return ToolResult.ok(
                    f"Message sent to {recipients[0]}",
                    to=recipients[0],
                    is_protocol=is_protocol,
                )
            return ToolResult.ok(
                f"Message broadcast to {len(recipients)} teammates",
                to=recipients,
                is_protocol=is_protocol,
            )

        # No send_fn: dry run mode
        return ToolResult.ok(
            f"Message would be sent to: {', '.join(recipients)}\n"
            f"Summary: {summary}\n"
            f"Content: {message}",
            dry_run=True,
        )

    def _execute(self, arguments: dict[str, Any]) -> str:
        """Sync fallback -- not normally used."""
        return "Error: SendMessage requires async execution"

    def register_teammate(self, name: str, agent_id: str) -> None:
        """Register a teammate for message routing."""
        self._teammates[name] = agent_id

    def remove_teammate(self, name: str) -> None:
        """Remove a teammate from routing."""
        self._teammates.pop(name, None)
