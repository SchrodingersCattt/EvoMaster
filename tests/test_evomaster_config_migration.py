"""阶段 1.3 / 2.4 验收：配置与 Playground 多 Agent 行为。"""

from pathlib import Path

from evomaster.config import ConfigManager
from evomaster.core.playground import BasePlayground

_FIXTURE_CONFIG_DIR = (
    Path(__file__).resolve().parent / 'fixtures' / 'evomaster_multi_agent'
)


def test_stage_1_3_acceptance_enable_tools_normalized_to_tools():
    """现有带 enable_tools 的 YAML 经 getter 得到 tools 规范化结果。"""
    mgr = ConfigManager(
        config_dir=_FIXTURE_CONFIG_DIR,
        config_file='config.yaml',
    )
    cfg = mgr.load()

    assert hasattr(cfg, 'agents')
    agents = mgr.get_agents_config()
    assert 'planning' in agents and 'coding' in agents

    planning_cfg = mgr.get_agent_config('planning')
    coding_cfg = mgr.get_agent_config('coding')
    assert 'tools' in planning_cfg
    assert planning_cfg['tools'] == {'builtin': [], 'mcp': ''}
    assert coding_cfg['tools'] == {'builtin': ['*'], 'mcp': ''}

    assert mgr.get_agent_tools_config('planning') == {'builtin': [], 'mcp': ''}
    assert mgr.get_agent_tools_config('coding') == {'builtin': ['*'], 'mcp': ''}

    first_name = next(iter(agents))
    first = mgr.get_agent_config(first_name)
    assert 'tools' in first


def test_stage_2_4_multi_agent_stored_in_agents_slots():
    """阶段 2.4 验收：多 agent 模式下每个 agent 正确存入 self.agents，self.agent 为其中之一。"""
    config_dir = _FIXTURE_CONFIG_DIR

    class MockSession:
        is_open = True

    class MockAgent:
        def set_agent_name(self, name):
            pass

    class PlaygroundForTest(BasePlayground):
        def _setup_session(self):
            self.session = MockSession()

        def _create_agent(
            self,
            name,
            agent_config=None,
            skill_registry=None,
            tool_config=None,
            llm_config=None,
            skill_config=None,
        ):
            return MockAgent()

    pg = PlaygroundForTest(config_dir=config_dir)
    pg.setup()

    assert 'planning_agent' in pg.agents
    assert 'coding_agent' in pg.agents
    assert pg.agent is not None
    assert pg.agent in (pg.agents['planning_agent'], pg.agents['coding_agent'])
