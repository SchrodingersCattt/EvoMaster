"""Tests for DevShell loop prompt guardrails."""

from __future__ import annotations

from evaluation.devshell_agent import loop_prompts


def test_main_prompt_requires_skill_layering_rules() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_MAIN

    assert "candidate_layers" in prompt
    assert "`playground-skills/` 计划废弃" in prompt


def test_optimization_prompt_requires_skill_layering_rules() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_OPTIMIZATION

    assert "`SKILL.md` 正文只承载" in prompt
    assert "`references/`" in prompt
    assert "`scripts/`" in prompt


def test_main_prompt_defines_exp_prompt_boundaries() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_MAIN

    assert "delegate_optimization" in prompt
    assert "proposal" in prompt


def test_optimization_prompt_requires_proposals_for_exp_overreach() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_OPTIMIZATION

    assert "proposed_optimization_changes.md" in prompt
    assert "提案" in prompt or "proposal" in prompt.lower()


def test_main_prompt_requires_candidate_layer_in_optimization_delegation() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_MAIN

    assert "candidate_layers" in prompt
    assert "skill" in prompt
    assert "tool" in prompt
