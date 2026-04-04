"""MatMaster ``matmaster/exps/{name}.toml`` prompt token budget (devshell loop).

Counts the **full assembled prompt** that ``ContextBuilder.build()`` produces at
agent start: ``system_prompt`` + ``developer_instructions`` + tool descriptions +
skill meta info (section headers and separators included).

Uses the same tiktoken model encoding as ``matmaster.core.context_compactor``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matmaster.config.exp import ExpConfig

# Recommended target for day-to-day iteration. Hard cap remains separate.
TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS = 12_000
# Hard cap for DevShell 迭代：被测 Agent 的完整初始系统 prompt（含 tools/skills 展开）
MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS = 15_000

# SkillTool has @property; values are fixed strings, kept here to avoid instantiation.
_SKILL_TOOL_ENTRY = (
    "use_skill",
    "Use a skill by name. Three actions: "
    "'get_info' retrieves full skill documentation, "
    "'get_reference' fetches a specific reference file, "
    "'run_script' executes a script from the skill.",
)


def token_count_gpt4o(text: str) -> int:
    import tiktoken

    try:
        enc = tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text or ""))


# ---------------------------------------------------------------------------
# Prompt assembly helpers (mirror ContextBuilder + Exp tool registration)
# ---------------------------------------------------------------------------


def _collect_tool_entries(cfg: ExpConfig) -> list[tuple[str, str]]:
    """Collect ``(name, description)`` for tools that would be registered.

    Mirrors ``Exp._init_builtin_tools`` / ``Exp._init_skill_tools`` but only
    reads class-level metadata — no session or workdir needed.
    """
    from matmaster.tools.builtin import (
        BashTool,
        EditTool,
        GlobTool,
        GrepTool,
        ListDirTool,
        ReadTool,
        SpawnTool,
        TaskCompleteTool,
        TaskCreateTool,
        TaskGetTool,
        TaskListTool,
        TaskUpdateTool,
        WebFetchTool,
        WebSearchTool,
        WriteTool,
    )

    builtin_cfg = cfg.tools.builtin
    allow_all = "*" in builtin_cfg
    allowed: set[str] | None = None if allow_all else set(builtin_cfg)

    def _want(name: str) -> bool:
        return allowed is None or name in allowed

    entries: list[tuple[str, str]] = []

    # 1. Native builtin tools (same order as Exp._init_builtin_tools)
    native_classes: list[type] = [
        BashTool,
        ListDirTool,
        ReadTool,
        WriteTool,
        EditTool,
        GlobTool,
        GrepTool,
        TaskCreateTool,
        TaskGetTool,
        TaskListTool,
        TaskUpdateTool,
        TaskCompleteTool,
        WebSearchTool,
        WebFetchTool,
    ]
    for cls in native_classes:
        if _want(cls.name):
            entries.append((cls.name, cls.description))

    # 2. Evo adapter tools (may not be importable in all environments)
    try:
        from evomaster.agent.tools.builtin.monitor_job import MonitorJobTool

        t = MonitorJobTool()
        if _want(t.name):
            entries.append((t.name, (t.params_class.__doc__ or "").strip()))
    except Exception:
        pass
    try:
        from playground.mat_master.tools.web_search import get_web_search_tool

        t = get_web_search_tool()
        if _want(t.name):
            entries.append((t.name, (t.params_class.__doc__ or "").strip()))
    except Exception:
        pass

    # 3. SpawnTool (registered separately in Exp.build_runtime)
    if _want(SpawnTool.name):
        entries.append((SpawnTool.name, SpawnTool.description))

    # 4. SkillTool (registered when skills.enabled)
    if cfg.skills.enabled:
        entries.append(_SKILL_TOOL_ENTRY)

    return entries


def _build_skills_meta(cfg: ExpConfig) -> str:
    """Build skill meta-info context, as ``SkillRegistry.get_meta_info_context()``."""
    if not cfg.skills.enabled:
        return ""
    try:
        from matmaster.skills.registry import SkillRegistry

        roots_raw = cfg.skills.skills_root
        if isinstance(roots_raw, list):
            roots = [Path(r) for r in roots_raw if r]
        else:
            roots = [Path(roots_raw)] if roots_raw else []
        if not roots:
            return ""
        name_filter = cfg.skills.skill_names or None
        sr = SkillRegistry(roots, skills=name_filter)
        return sr.get_meta_info_context()
    except Exception:
        return ""


def _build_full_prompt_text(cfg: ExpConfig) -> str:
    """Reconstruct the text ``ContextBuilder.build()`` would produce.

    Includes: system_prompt, developer_instructions, skills meta, tool
    descriptions.  Excludes dynamic runtime content (memory, task context,
    lazy MCP tools injected after initial registration).
    """
    SEPARATOR = "\n\n---\n\n"
    sections: list[str] = []

    # 1. System prompt
    sp = (cfg.system_prompt or "").strip()
    if sp:
        sections.append(f"# System\n\n{sp}")

    # 2. Identity (developer_instructions)
    di = (cfg.developer_instructions or "").strip()
    if di:
        sections.append(f"# Identity\n\n{di}")

    # 3. Skills meta context
    skills_text = _build_skills_meta(cfg)
    if skills_text:
        sections.append(f"# Skills\n\n{skills_text}")

    # 4. Tool descriptions
    tool_entries = _collect_tool_entries(cfg)
    if tool_entries:
        lines = [f"- {name}: {desc}" for name, desc in tool_entries]
        sections.append("# Available Tools\n\n" + "\n".join(lines))

    return SEPARATOR.join(sections)


def static_prompt_token_total_for_exp(
    exp_name: str,
    *,
    exps_dir: Path | None = None,
) -> tuple[int, int, int]:
    """Return ``(system_prompt_tokens, developer_instructions_tokens, total)``.

    ``total`` is the token count for the full assembled prompt including
    system_prompt, developer_instructions, tool descriptions, and skill meta
    — matching what ``ContextBuilder.build()`` produces at agent start.
    """
    from matmaster.config.loader import load_exp_config

    cfg = load_exp_config(exp_name, exps_dir=exps_dir)
    sp = token_count_gpt4o(cfg.system_prompt)
    di = token_count_gpt4o(cfg.developer_instructions)
    full_text = _build_full_prompt_text(cfg)
    total = token_count_gpt4o(full_text)
    return sp, di, total


def budget_status(
    total_tokens: int,
    *,
    target_tokens: int = TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
    max_tokens: int = MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
) -> str:
    """Return ``ok``, ``warn``, or ``error`` for the given token total."""
    if total_tokens > max_tokens:
        return "error"
    if total_tokens > target_tokens:
        return "warn"
    return "ok"


def check_exp(
    exp_name: str,
    *,
    exps_dir: Path | None = None,
    max_tokens: int = MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
) -> tuple[bool, int, int, int]:
    """Return ``(ok, sp_tokens, di_tokens, total)``."""
    sp, di, total = static_prompt_token_total_for_exp(exp_name, exps_dir=exps_dir)
    return total <= max_tokens, sp, di, total


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Count tiktoken (gpt-4o) tokens for the full MatMaster exp prompt "
            "(system_prompt + developer_instructions + tools + skills), with a "
            f"recommended target of {TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS} "
            f"and a hard cap of {MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS}."
        )
    )
    p.add_argument(
        "exp",
        nargs="?",
        default="direct",
        help="Exp name (matmaster/exps/{exp}.toml). Default: direct",
    )
    p.add_argument(
        "--max",
        type=int,
        default=MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
        help=f"Maximum allowed total tokens (default: {MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS}).",
    )
    p.add_argument(
        "--target",
        type=int,
        default=TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS,
        help=(
            "Recommended total tokens before warning "
            f"(default: {TARGET_MATMASTER_EXP_STATIC_PROMPT_TOKENS})."
        ),
    )
    p.add_argument(
        "--exps-dir",
        type=Path,
        default=None,
        help="Override matmaster/exps directory (for tests).",
    )
    args = p.parse_args(argv)

    try:
        ok, sp, di, total = check_exp(
            args.exp,
            exps_dir=args.exps_dir,
            max_tokens=int(args.max),
        )
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    tools_skills = total - sp - di
    print(
        f"exp={args.exp!r} system_prompt_tokens={sp} "
        f"developer_instructions_tokens={di} "
        f"tools_skills_overhead={tools_skills} total={total} "
        f"target={args.target} max={args.max}",
        file=sys.stderr,
    )
    status = budget_status(
        total, target_tokens=int(args.target), max_tokens=int(args.max)
    )
    if status == "ok":
        print("ok: within recommended budget", file=sys.stderr)
        return 0
    if status == "warn" and ok:
        print(
            "warning: exceeds recommended budget but still within hard limit",
            file=sys.stderr,
        )
        return 0
    print(
        f"error: total {total} exceeds hard max {args.max} — shorten or merge before commit",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
