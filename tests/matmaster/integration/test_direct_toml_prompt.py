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


def test_developer_instructions_identity():
    """developer_instructions contains agent identity (D-02: identity dimension)."""
    cfg = load_exp_config("direct")
    di = cfg.developer_instructions
    assert "Mat Master" in di
    assert "materials science" in di.lower() or "material" in di.lower()


def test_developer_instructions_tool_routing():
    """developer_instructions contains tool usage routing rules (D-02 + D-03)."""
    cfg = load_exp_config("direct")
    di = cfg.developer_instructions
    # All 5 dedicated tool routing targets must be mentioned
    for tool_name in ["read_file", "write_file", "edit_file", "glob", "grep"]:
        assert tool_name in di, f"Missing routing for {tool_name}"
    # Bash context reference
    assert "execute_bash" in di


def test_developer_instructions_behavior_constraints():
    """developer_instructions contains behavior constraints (D-02: behavior dimension)."""
    cfg = load_exp_config("direct")
    di = cfg.developer_instructions.lower()
    # Read-before-modify
    assert "read" in di and "before" in di and "modif" in di
    # Avoid over-engineering
    assert "over-engineer" in di or "only make changes" in di.lower() or "directly needed" in di


def test_developer_instructions_output_style():
    """developer_instructions contains output style guidance (D-02: output style)."""
    cfg = load_exp_config("direct")
    di = cfg.developer_instructions.lower()
    assert "concise" in di or "direct" in di


def test_developer_instructions_remote_environment():
    """developer_instructions contains remote environment rules (D-02: science domain)."""
    cfg = load_exp_config("direct")
    di = cfg.developer_instructions.lower()
    assert "remote" in di or "workspace" in di


def test_developer_instructions_reasonable_length():
    """developer_instructions is substantive but not excessive (500-3000 chars)."""
    cfg = load_exp_config("direct")
    length = len(cfg.developer_instructions)
    assert 500 <= length <= 3000, f"Length {length} outside [500, 3000]"


def test_mode_contract_nonempty_and_direct():
    """mode_contract is non-empty and references direct execution mode."""
    cfg = load_exp_config("direct")
    mc = cfg.mode_contract
    assert len(mc.strip()) > 0
    assert "direct" in mc.lower()
