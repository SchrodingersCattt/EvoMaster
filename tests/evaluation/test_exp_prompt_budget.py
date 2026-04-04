"""Tests for evaluation.devshell_agent.exp_prompt_budget."""

from __future__ import annotations

from pathlib import Path

from evaluation.devshell_agent.exp_prompt_budget import (
    MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
    TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
    budget_status,
    check_exp,
    token_count_gpt4o,
)


def test_token_count_gpt4o_positive_for_text() -> None:
    assert token_count_gpt4o("hello") >= 1


def test_budget_status_thresholds() -> None:
    assert TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS == 12_000
    assert MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS == 15_000

    assert budget_status(TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS) == "ok"
    assert budget_status(TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS + 1) == "warn"
    assert budget_status(MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS) == "warn"
    assert budget_status(MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS + 1) == "error"


def _write_minimal_toml(exps: Path, *, tools_builtin: str = "[]") -> None:
    """Write _base.toml + tiny.toml with explicit empty tools config."""
    (exps / "_base.toml").write_text(
        'system_prompt = """alpha"""\n',
        encoding="utf-8",
    )
    (exps / "tiny.toml").write_text(
        f'name = "tiny"\n'
        f'developer_instructions = """bravo"""\n'
        f'\n[tools]\nbuiltin = {tools_builtin}\n',
        encoding="utf-8",
    )


def test_check_exp_minimal_workspace(tmp_path: Path) -> None:
    exps = tmp_path / "exps"
    exps.mkdir()
    _write_minimal_toml(exps)

    ok, sp, di, total = check_exp(
        "tiny",
        exps_dir=exps,
        max_tokens=MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
    )
    assert sp == token_count_gpt4o("alpha")
    assert di == token_count_gpt4o("bravo")
    # total includes formatting overhead (section headers + separators)
    assert total > sp + di
    assert ok is True


def test_check_exp_over_budget(tmp_path: Path) -> None:
    exps = tmp_path / "exps"
    exps.mkdir()
    _write_minimal_toml(exps)

    ok_loose, _sp, _di, total = check_exp(
        "tiny",
        exps_dir=exps,
        max_tokens=MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
    )
    assert ok_loose is True
    ok_tight, _, _, total2 = check_exp("tiny", exps_dir=exps, max_tokens=1)
    assert total2 == total
    assert ok_tight is False
