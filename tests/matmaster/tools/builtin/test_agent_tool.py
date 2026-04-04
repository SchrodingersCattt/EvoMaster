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

    def test_empty_exp_name_error_when_available_exps_set(self):
        """exp_name 为空时，若已设置 available_exps，应返回错误并列出合法值。"""

        async def fake_spawn(exp_name, task, cancel_token=None):
            return "ok"

        exps = [("explore", "Read-only exploration"), ("direct", "Full execution")]
        tool = AgentTool(spawn_fn=fake_spawn, available_exps=exps)
        result = asyncio.run(
            tool.execute(
                {
                    "description": "test",
                    "prompt": "do something",
                    "exp_name": "",
                }
            )
        )
        assert "error" in result.lower()
        assert "explore" in result
        assert "direct" in result

    def test_invalid_exp_name_error_when_available_exps_set(self):
        """exp_name 不在合法集合中时，应返回错误并列出合法值。"""

        async def fake_spawn(exp_name, task, cancel_token=None):
            return "ok"

        exps = [("explore", "Read-only exploration"), ("direct", "Full execution")]
        tool = AgentTool(spawn_fn=fake_spawn, available_exps=exps)
        result = asyncio.run(
            tool.execute(
                {
                    "description": "test",
                    "prompt": "do something",
                    "exp_name": "nonexistent",
                }
            )
        )
        assert "error" in result.lower()
        assert "explore" in result
        assert "direct" in result

    def test_valid_exp_name_passes_when_available_exps_set(self):
        """exp_name 在合法集合中时，应正常调用 spawn_fn。"""

        async def fake_spawn(exp_name, task, cancel_token=None):
            return f"Ran {exp_name}"

        exps = [("explore", "Read-only exploration"), ("direct", "Full execution")]
        tool = AgentTool(spawn_fn=fake_spawn, available_exps=exps)
        result = asyncio.run(
            tool.execute(
                {
                    "description": "test",
                    "prompt": "do something",
                    "exp_name": "explore",
                }
            )
        )
        assert result == "Ran explore"

    def test_any_exp_name_allowed_when_no_available_exps(self):
        """未设置 available_exps 时，任意 exp_name（包括空值）应正常通过。"""

        async def fake_spawn(exp_name, task, cancel_token=None):
            return f"Ran '{exp_name}'"

        tool = AgentTool(spawn_fn=fake_spawn)
        result = asyncio.run(
            tool.execute(
                {
                    "description": "test",
                    "prompt": "do something",
                    "exp_name": "",
                }
            )
        )
        assert "error" not in result.lower()
