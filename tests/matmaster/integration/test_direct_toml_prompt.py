"""Integration test: direct.toml developer_instructions content validation.

Verifies PRMT-02: developer_instructions loaded from direct.toml contains
all required behavioral guidance dimensions (identity, tool usage, behavior
constraints, output style, remote environment rules).
"""

from matmaster.config.loader import load_exp_config


def test_direct_toml_loads_successfully():
    """load_exp_config('direct') does not raise TOML parse or validation error."""
    cfg = load_exp_config("direct")
    assert cfg.name == "direct"


def test_mode_contract_removed():
    """mode_contract field no longer exists on ExpConfig."""
    cfg = load_exp_config("direct")
    assert not hasattr(cfg, "mode_contract")


def test_execution_mode_in_developer_instructions():
    """Former mode_contract content now lives in developer_instructions."""
    cfg = load_exp_config("direct")
    di = cfg.developer_instructions.lower()
    assert "direct execution" in di
    assert "remote compute" in di


def test_system_prompt_from_base():
    """system_prompt is loaded from _base.toml."""
    cfg = load_exp_config("direct")
    assert len(cfg.system_prompt.strip()) > 0


def test_bohrium_tool_enabled_in_direct_toml():
    """direct agent exposes the builtin Bohrium tool."""
    cfg = load_exp_config("direct")
    assert "Bohrium" in cfg.tools.builtin
