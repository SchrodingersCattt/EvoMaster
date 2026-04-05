"""MatMaster ``matmaster/exps/{name}.toml`` prompt token budget (devshell loop).

Counts the **full assembled prompt** that current runtime wiring produces at
agent start: ``ContextBuilder.build()`` output plus builtin/skill prompt
injections collected from the registered tool catalog.

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
_STATIC_WORKSPACE_ROOT = Path("/workspace")


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


def _collect_tool_prompts(cfg: ExpConfig) -> str:
    """Collect tool prompt injections using the current runtime tool surface."""
    from matmaster.tools.builtin import (
        AgentTool,
        BashTool,
        BohriumTool,
        EditTool,
        GlobTool,
        GrepTool,
        ReadTool,
        TodoWriteTool,
        WebFetchTool,
        WebSearchTool,
        WriteTool,
    )
    from matmaster.tools.builtin.skill_tool import SkillTool
    from matmaster.tools.tool_catalog import ToolCatalog
    from matmaster.tools.tool_registry import ToolRegistry
    from matmaster.types.tool_desc_ctx import ToolDescriptionContext
    from matmaster.types.topology import RuntimeTopology

    async def _noop_spawn(
        exp_name: str, task: str, cancel_token=None
    ) -> str:  # pragma: no cover - never executed
        del exp_name, task, cancel_token
        return ""

    builtin_cfg = cfg.tools.builtin
    allow_all = "*" in builtin_cfg
    allowed: set[str] | None = None if allow_all else set(builtin_cfg)

    def _want(name: str) -> bool:
        return allowed is None or name in allowed

    workdir = _STATIC_WORKSPACE_ROOT
    tools = [
        BashTool(workdir=workdir),
        ReadTool(workdir=workdir),
        WriteTool(workdir=workdir),
        EditTool(workdir=workdir),
        GlobTool(workdir=workdir),
        GrepTool(workdir=workdir),
        TodoWriteTool(workdir=workdir),
        WebSearchTool(),
        WebFetchTool(workdir=workdir),
        BohriumTool(workdir=workdir),
    ]

    registry = ToolRegistry()
    for tool in tools:
        if _want(tool.name):
            registry.register(tool, source="builtin")

    if _want("Agent"):
        registry.register(
            AgentTool(workdir=workdir, spawn_fn=_noop_spawn, available_exps=[]),
            source="builtin",
        )

    if cfg.skills.enabled:
        registry.register(SkillTool(), source="skill")

    topology = RuntimeTopology(
        session_kind="local",
        control_root=str(workdir),
        workspace_root=str(workdir),
    )
    catalog = ToolCatalog(registry, topology=topology)
    desc_ctx = ToolDescriptionContext(
        session_kind=topology.session_kind,
        workspace_root=topology.workspace_root,
        topology=topology,
    )
    return catalog.collect_prompts(desc_ctx)


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

    Includes: ``ContextBuilder`` sections plus prompt injections collected
    from the current builtin / skill tool surface. Excludes dynamic runtime
    content (memory, task context, lazy MCP tools injected after initial
    registration).
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

    prompt = builder.SEPARATOR.join(section for section in sections if section)
    tool_prompts = _collect_tool_prompts(cfg)
    if tool_prompts:
        prompt = f"{prompt}\n\n{tool_prompts}" if prompt else tool_prompts
    return prompt


def static_prompt_token_total_for_exp(
    exp_name: str,
    *,
    exps_dir: Path | None = None,
) -> tuple[int, int, int]:
    """Return ``(system_prompt_tokens, developer_instructions_tokens, total)``.

    ``total`` is the token count for the full assembled prompt including
    system_prompt, developer_instructions, the generic tools section,
    tool prompt injections, and skill meta.
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
