"""Tests for DevShell loop prompt guardrails."""

from __future__ import annotations

from evaluation.devshell_agent import loop_prompts


def test_main_prompt_requires_skill_layering_rules() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_MAIN

    assert "`SKILL.md` 正文只承载" in prompt
    assert "`references/` / `reference/`" in prompt
    assert "`scripts/`" in prompt
    assert "不要把长篇参考、长表格、长案例直接堆进 `SKILL.md`" in prompt


def test_optimization_prompt_requires_skill_layering_rules() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_OPTIMIZATION

    assert "若修改 `matmaster/skills/`，先判断内容应落在哪一层" in prompt
    assert "`SKILL.md` 正文只承载" in prompt
    assert "`references/` / `reference/`" in prompt
    assert "`scripts/`" in prompt


def test_main_prompt_defines_exp_prompt_boundaries() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_MAIN

    assert "`matmaster/exps/_base.toml`" in prompt
    assert "`matmaster/exps/direct.toml`" in prompt
    assert "跨任务、跨领域都成立的全局原则" in prompt
    assert "跨任务执行与交付契约" in prompt


def test_optimization_prompt_requires_proposals_for_exp_overreach() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_OPTIMIZATION

    assert "只有在真正属于 system prompt / exp 契约层时，才考虑" in prompt
    assert "领域 workflow、软件专属步骤、题目类技巧" in prompt
    assert "默认应改 Skills、tool descriptions" in prompt
    assert "proposed_matmaster_exps_changes.md" in prompt
    assert "为何不能放到 skill / tool 层" in prompt
    assert "Target file" in prompt
    assert "Existing rule(s) to replace or merge" in prompt
    assert "Proposed text" in prompt
    assert "Expected cross-task benefit" in prompt
    assert "Prompt budget impact" in prompt


def test_main_prompt_requires_candidate_layer_in_optimization_delegation() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_MAIN

    assert "candidate_layers" in prompt
    assert "skill / tool / system_prompt / runtime" in prompt
