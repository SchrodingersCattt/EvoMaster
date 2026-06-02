"""tests/matmaster/tools/builtin/test_agent_tool.py"""

import asyncio

from matmaster.config.exp import ExpSubagentMeta
from matmaster.tools.builtin.agent_tool import AgentTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.stream_drain import DrainResult


def _meta(**overrides):
    data = {
        "name": "explore",
        "description": "Read-only exploration subagent",
        "when_to_use": "Use for evidence gathering",
        "read_only": True,
        "visible_as_subagent": True,
        "context_mode": "fresh",
        "result_style": "findings",
        "tools_summary": "Builtin: Bash, Read, Glob, Grep; MCP: none; Skills: disabled",
    }
    data.update(overrides)
    return ExpSubagentMeta.model_validate(data)


class TestAgentToolMetadata:
    def test_name(self):
        assert AgentTool.name == "Agent"

    def test_stop_mode(self):
        assert AgentTool.stop_mode == "non_cancellable"

    def test_no_spawn_fn_hidden_from_model(self):
        tool = AgentTool(spawn_fn=None, available_exps=[_meta()])
        assert tool.exposed_to_model is False

    def test_schema_keeps_prompt_required_and_legacy_description_alias(self):
        tool = AgentTool(spawn_fn=None, available_exps=[_meta()])
        assert set(tool.json_schema["required"]) == {"prompt"}
        assert "exp_name" in tool.json_schema["properties"]
        assert "task_summary" in tool.json_schema["properties"]
        assert tool.json_schema["properties"]["description"]["deprecated"] is True

    def test_prompt_lists_subagent_usage(self):
        tool = AgentTool(spawn_fn=None, available_exps=[_meta()])
        text = tool.prompt()
        assert "explore" in text
        assert "Use for evidence gathering" in text
        assert "Tools:" in text
        assert "read-only" in text.lower()
        assert "spawn_id" in text
        assert "When NOT to use the Agent tool" in text
        assert (
            "Brief the agent like a smart colleague who just walked into the room"
            in text
        )
        assert "Never delegate understanding" in text
        assert "parallel" in text.lower()


class TestAgentValidation:
    def test_validate_input_maps_legacy_description_alias(self):
        tool = AgentTool(spawn_fn=None, available_exps=[_meta()])
        decision = asyncio.run(
            tool.validate_input(
                {
                    "exp_name": "explore",
                    "description": "trace parser flow",
                    "prompt": "Inspect the parser stack and summarize the path.",
                }
            )
        )
        assert decision is not None
        assert decision.modified_args["task_summary"] == "trace parser flow"

    def test_validate_input_defaults_missing_exp_name_to_direct(self):
        tool = AgentTool(
            spawn_fn=None,
            available_exps=[
                _meta(
                    name="direct",
                    description="Execution subagent",
                    when_to_use="Use for execution",
                    read_only=False,
                    result_style="completion",
                ),
                _meta(),
            ],
        )
        decision = asyncio.run(
            tool.validate_input(
                {
                    "description": "trace parser flow",
                    "prompt": "Inspect the parser stack and summarize the path.",
                }
            )
        )
        assert decision is not None
        assert decision.modified_args["exp_name"] == "direct"

    def test_validate_input_rejects_unknown_fields(self):
        tool = AgentTool(spawn_fn=None, available_exps=[_meta()])
        decision = asyncio.run(
            tool.validate_input(
                {
                    "exp_name": "explore",
                    "prompt": "Inspect the parser stack.",
                    "unexpected": True,
                }
            )
        )
        assert decision is not None
        assert decision.decision == "deny"

    def test_execute_returns_tool_result_payload(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return DrainResult(
                status="completed",
                reason="natural",
                final_content=f"Ran {exp_name}: {task}",
                num_turns=1,
                usage={},
                messages=[],
            )

        tool = AgentTool(spawn_fn=fake_spawn, available_exps=[_meta()])
        result = asyncio.run(
            tool.execute(
                {
                    "exp_name": "explore",
                    "task_summary": "trace parser flow",
                    "prompt": "Inspect the parser stack and summarize the path.",
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.payload["exp_name"] == "explore"
        assert result.payload["task_summary"] == "trace parser flow"

    def test_execute_maps_completed_drain_result_to_tool_result_payload(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return DrainResult(
                status="completed",
                reason="natural",
                final_content="child answer",
                num_turns=2,
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "cache_read_tokens": 40,
                },
                messages=[],
            )

        tool = AgentTool(spawn_fn=fake_spawn, available_exps=[_meta()])
        result = asyncio.run(
            tool.execute(
                {
                    "exp_name": "explore",
                    "task_summary": "trace parser flow",
                    "prompt": "Inspect the parser stack and summarize the path.",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.content == "child answer"
        assert result.payload["exp_name"] == "explore"
        assert result.payload["task_summary"] == "trace parser flow"
        assert result.payload["prompt"] == (
            "Inspect the parser stack and summarize the path."
        )
        assert result.payload["subagent_usage"] == {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cache_read_tokens": 40,
        }
        assert result.payload["subagent_status"] == "completed"
        assert result.payload["subagent_reason"] == "natural"
        assert result.payload["subagent_num_turns"] == 2

    def test_execute_maps_noncompleted_drain_result_to_status_content(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return DrainResult(
                status="cancelled",
                reason="user_stop",
                final_content=None,
                num_turns=1,
                usage={"prompt_tokens": 10, "total_tokens": 10},
                messages=[],
            )

        tool = AgentTool(spawn_fn=fake_spawn, available_exps=[_meta()])
        result = asyncio.run(
            tool.execute(
                {
                    "exp_name": "explore",
                    "task_summary": "trace parser flow",
                    "prompt": "Inspect the parser stack.",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert result.content == (
            "SubAgent finished with status=cancelled, reason=user_stop"
        )
        assert result.payload["subagent_usage"] == {
            "prompt_tokens": 10,
            "total_tokens": 10,
        }
        assert result.payload["subagent_status"] == "cancelled"
        assert result.payload["subagent_reason"] == "user_stop"
        assert result.payload["subagent_num_turns"] == 1
