"""Tests for DevShell loop prompt guardrails."""

from __future__ import annotations

from evaluation.devshell_agent import loop_prompts


def test_main_prompt_requires_skill_layering_rules() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_MAIN

    assert "`SKILL.md` 只承载" in prompt
    assert "`references/` / `reference/`" in prompt
    assert "`scripts/`" in prompt
    assert "不要把长篇参考、长表格、长案例直接堆进 `SKILL.md`" in prompt


def test_optimization_prompt_requires_skill_layering_rules() -> None:
    prompt = loop_prompts.SYSTEM_PROMPT_OPTIMIZATION

    assert "若修改 `matmaster/skills/`，先判断内容应落在哪一层" in prompt
    assert "`SKILL.md` 只承载" in prompt
    assert "`references/` / `reference/`" in prompt
    assert "`scripts/`" in prompt
