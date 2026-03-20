from evomaster.agent.tools.base import create_registry


def test_default_registry_does_not_register_think_tool():
    registry = create_registry(['*'])
    assert registry.get_tool('think') is None
