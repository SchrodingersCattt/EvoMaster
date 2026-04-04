"""tests/matmaster/tools/builtin/test_agent_tool.py"""

import asyncio

from matmaster.tools.builtin.agent_tool import AgentTool


class TestAgentToolMetadata:
    def test_name(self):
        assert AgentTool.name == "Agent"

    def test_stop_mode(self):
        assert AgentTool.stop_mode == "non_cancellable"


class TestAgentRecursionGuard:
    def test_no_spawn_fn_hidden_from_model(self):
        """Schema-layer guard: exposed_to_model=False when spawn_fn=None."""
        tool = AgentTool(spawn_fn=None)
        assert tool.exposed_to_model is False

    def test_no_spawn_fn_runtime_error(self):
        """Runtime-layer guard: returns error even if somehow called."""
        tool = AgentTool(spawn_fn=None)
        result = asyncio.run(
            tool.execute(
                {
                    "description": "test",
                    "prompt": "do something",
                }
            )
        )
        assert "error" in result.lower() or "not available" in result.lower()

    def test_with_spawn_fn_visible(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return f"Result for: {task}"

        tool = AgentTool(spawn_fn=fake_spawn)
        assert tool.exposed_to_model is True

    def test_with_spawn_fn(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return f"Result for: {task}"

        tool = AgentTool(spawn_fn=fake_spawn)
        result = asyncio.run(
            tool.execute(
                {
                    "description": "test",
                    "prompt": "do something",
                }
            )
        )
        assert "Result for: do something" in result

    def test_exp_name_passed_to_spawn(self):
        async def fake_spawn(exp_name, task, cancel_token=None):
            return f"Ran {exp_name}: {task}"

        tool = AgentTool(spawn_fn=fake_spawn)
        result = asyncio.run(
            tool.execute(
                {
                    "description": "test",
                    "prompt": "do x",
                    "exp_name": "explore",
                }
            )
        )
        assert "explore" in result


class TestAgentDynamicSchema:
    def test_available_exps_modifies_schema(self):
        exps = [("explore", "Read-only exploration"), ("direct", "Full execution")]
        tool = AgentTool(spawn_fn=None, available_exps=exps)
        schema = tool.json_schema
        exp_prop = schema["properties"]["exp_name"]
        assert "enum" in exp_prop
        assert "explore" in exp_prop["enum"]
        assert "direct" in exp_prop["enum"]

    def test_description_includes_exps(self):
        exps = [("explore", "Read-only exploration")]
        tool = AgentTool(spawn_fn=None, available_exps=exps)
        assert "explore" in tool.description.lower()


class TestAgentValidation:
    def test_empty_prompt_error(self):
        async def fake_spawn(exp_name, task, stop_event=None):
            return "ok"

        tool = AgentTool(spawn_fn=fake_spawn)
        result = asyncio.run(
            tool.execute(
                {
                    "description": "test",
                    "prompt": "",
                }
            )
        )
        assert "error" in result.lower()
