"""MatMaster ``matmaster/exps/{name}.toml`` prompt token budget (devshell loop).

Counts the **full assembled prompt** that current runtime wiring produces at
agent start: ``ContextBuilder.build()`` output only. Local builtin / skill
tool guidance now lives in ``function.description``, so it is intentionally
excluded from this budget.

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
# Hard cap for DevShell 迭代：被测 Agent 的完整初始系统 prompt 文本
MAX_MATMASTER_EXP_STATIC_PROMPT_TOKENS = 15_000


def token_count_gpt4o(text: str) -> int:
    import tiktoken

    try:
        enc = tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text or ""))


# ---------------------------------------------------------------------------
# Prompt assembly helpers (mirror ContextBuilder text assembly)
# ---------------------------------------------------------------------------


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

    Includes only the static text sections assembled by ``ContextBuilder``:
    system prompt, developer instructions, skill meta, and the generic
    ``# Tools`` section. Excludes dynamic runtime content and local tool
    descriptions now supplied via ``function.description``.
    """
    from matmaster.core.context_builder import ContextBuilder

    sections: list[str] = []
    builder = ContextBuilder()

    # 1. System prompt
    system_section = builder._build_system_prompt(cfg.system_prompt)
    if system_section:
        sections.append(system_section)

    # 2. Identity (developer_instructions)
    identity_section = builder._build_identity(cfg.developer_instructions)
    if identity_section:
        sections.append(identity_section)

    # 3. Skills meta context
    skills_text = _build_skills_meta(cfg)
    if skills_text:
        sections.append(f"# Skills\n\n{skills_text}")

    # 4. Generic tools section from ContextBuilder (always present)
    sections.append(builder._build_tools())

    return builder.SEPARATOR.join(section for section in sections if section)


def static_prompt_token_total_for_exp(
    exp_name: str,
    *,
    exps_dir: Path | None = None,
) -> tuple[int, int, int]:
    """Return ``(system_prompt_tokens, developer_instructions_tokens, total)``.

    ``total`` is the token count for the full assembled prompt including
    system_prompt, developer_instructions, the generic tools section,
    and skill meta.
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
            "(system_prompt + developer_instructions + generic tools + skills), with a "
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
