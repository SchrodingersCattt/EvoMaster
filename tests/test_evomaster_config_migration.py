"""阶段 1.3 验收：现有 YAML（enable_tools）经新加载逻辑后行为与 v0.0.2 tools 一致。"""

from evomaster.config import ConfigManager


def test_stage_1_3_acceptance_enable_tools_normalized_to_tools():
    """现有带 enable_tools 的 YAML 经 getter 得到 tools 规范化结果。"""
    mgr = ConfigManager(
        config_dir='configs/minimal_multi_agent',
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

    first = mgr.get_agent_config()
    assert 'tools' in first
