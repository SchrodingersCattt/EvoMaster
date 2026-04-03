"""Agent and teammate messaging GPT-style tools."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ..base import BaseTool
from ..models import AgentRunResult, OutboundMessage, ToolResult


class AgentTool(BaseTool):
    """Backend-only wrapper around an injected sub-agent launcher."""

    name: ClassVar[str] = "Agent"
    description: ClassVar[str] = (
        "Launch a new agent to handle complex multi-step tasks autonomously. "
        "Supports subagent_type, model overrides, background execution, and isolation hints."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short 3-5 word description of the task.",
            },
            "prompt": {
                "type": "string",
                "description": "Complete task instructions for the sub-agent.",
            },
            "subagent_type": {
                "type": "string",
                "description": "Optional specialized agent type.",
            },
            "model": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
                "description": "Optional model override.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Launch the agent asynchronously when supported.",
            },
            "isolation": {
                "type": "string",
                "enum": ["worktree"],
                "description": "Isolation mode hint for the launcher.",
            },
        },
        "required": ["description", "prompt"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        if self.context.agent_launcher is None:
            return ToolResult.error(
                "Error: Agent requires a configured agent_launcher on ToolContext."
            )

        result = self.context.agent_launcher(
            description=arguments["description"],
            prompt=arguments["prompt"],
            subagent_type=arguments.get("subagent_type"),
            model=arguments.get("model"),
            run_in_background=bool(arguments.get("run_in_background", False)),
            isolation=arguments.get("isolation"),
        )
        if isinstance(result, AgentRunResult):
            if result.status == "completed":
                return ToolResult.ok(result.content, **result.payload)
            return ToolResult(
                status=result.status,
                content=result.content,
                payload=dict(result.payload),
                meta=dict(result.meta),
            )
        return ToolResult.ok(str(result))


class SendMessageTool(BaseTool):
    """Backend-only agent-to-agent messaging tool."""

    name: ClassVar[str] = "SendMessage"
    description: ClassVar[str] = (
        "Send a plain text or structured protocol message to another agent. "
        "In the backend-only port this writes to an outbox or calls a message router."
    )
    defer_loading: ClassVar[bool] = True
    search_hint: ClassVar[str] = "send teammate or swarm message"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient agent name or '*' for broadcast."},
            "summary": {
                "type": "string",
                "description": "Short preview summary for plain text messages.",
            },
            "message": {
                "oneOf": [
                    {"type": "string", "description": "Plain text message body."},
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "shutdown_request",
                                    "shutdown_response",
                                    "plan_approval_response",
                                ],
                            },
                            "request_id": {"type": "string"},
                            "approve": {"type": "boolean"},
                            "reason": {"type": "string"},
                            "feedback": {"type": "string"},
                        },
                        "required": ["type"],
                    },
                ]
            },
        },
        "required": ["to", "message"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        recipient = arguments["to"].strip()
        message = arguments["message"]
        summary = arguments.get("summary")

        if "@" in recipient:
            return ToolResult.error("Error: recipient names must not contain '@'.")
        if not recipient:
            return ToolResult.error("Error: recipient is required.")
        if isinstance(message, dict) and recipient == "*":
            return ToolResult.error("Error: structured protocol messages cannot be broadcast.")
        if isinstance(message, str) and not summary:
            return ToolResult.error(
                "Error: summary is required when sending a plain text message."
            )

        normalized = OutboundMessage(to=recipient, message=message, summary=summary)
        if self.context.message_router is not None:
            self.context.message_router(normalized)
        else:
            self.context.outbox.append(normalized)

        return ToolResult.ok(
            json.dumps(
                {
                    "to": normalized.to,
                    "summary": normalized.summary,
                    "message": normalized.message,
                },
                ensure_ascii=False,
            ),
            to=normalized.to,
            summary=normalized.summary,
            message=normalized.message,
        )
