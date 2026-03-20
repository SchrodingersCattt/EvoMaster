"""
Mat Master prompt generation.

System and user prompts are built by functions so tool list and rules stay in one place.
- Tool list: maintain TOOL_GROUPS; add new MCP entries here when you onboard a server.
- Async software list, CRP block/allow lists, and calculation rules are injected from
  ``AsyncToolRegistry`` — **no hardcoded software names in the prompt text**.
- Current date (with OS/shell info) is appended at the end for cache-friendly prefix caching.
"""

import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.async_tool_registry import AsyncToolRegistry

# Single source of truth: MCP tool groups (prefix, short name, description).
# Descriptions should stay generic; specific software names are injected via registry.
TOOL_GROUPS = [
    (
        'mat_sg',
        'Structure Generator',
        'Generate, optimize, or process crystal/molecule structures; tools like mat_sg_*',
    ),
    (
        'mat_sn',
        'Science Navigator',
        'Literature search, web search; tools like mat_sn_*',
    ),
    (
        'mat_doc',
        'Document Parser',
        'Extract information from PDFs and web pages; PDF tool: mat_doc_extract_material_data_from_pdf (registered MCP, sync). Webpage tool: extract_info_from_webpage (built-in, no server required).',
    ),
    ('mat_dpa', 'DPA Calculator', 'DPA-related calculations; tools like mat_dpa_*'),
    (
        'mat_compdart',
        'CompDART Optimizer',
        'Composition optimization and DART GA workflows; tools like mat_compdart_*',
    ),
    (
        'mat_nmr',
        'NMR Characterization',
        'NMR-based structure retrieval/prediction/reverse prediction; tools like mat_nmr_NMR_search_tool, mat_nmr_NMR_predict_tool, mat_nmr_NMR_reverse_predict_tool',
    ),
    (
        'mat_xrd',
        'XRD Characterization',
        'XRD phase identification from processed diffraction data; tools like mat_xrd_xrd_phase_identification',
    ),
    (
        'mat_electron_microscope',
        'Electron Microscopy',
        'TEM/SEM image particle and morphology recognition; tools like mat_electron_microscope_get_electron_microscope_recognize',
    ),
    (
        'mat_struct_db',
        'Structure Database',
        'Crystal/structure retrieval and database search; tools like mat_struct_db_*',
    ),
    (
        'mat_abacus',
        'ABACUS first-principles',
        'Structure relaxation, SCF, bands, phonons, elasticity, etc.; tools like mat_abacus_*',
    ),
    (
        'mat_binary_calc',
        'Binary Calculators',
        'Input preparation: prepare_lammps_job, prepare_cp2k_job, prepare_qe_job, '
        'prepare_abinit_job, prepare_orca_job, prepare_pyatb_job. '
        'Submit after prepare_* via bohrium-job skill (no mat_binary_calc_submit_* MCP tools). '
        'GROMACS: submit via MCP mat_binary_calc_run_gromacs, then monitor_job (software=gromacs).',
    ),
]


# ── Global output language rule (single source of truth) ──────────────────
# Exported so that other prompt builders (planner, router, etc.) can import
# and inject into their own system prompts, avoiding language drift.
LANGUAGE_RULE = (
    '**Output language**: Always respond in the same language the user writes in. '
    "Match the user's language for all replies, file content, and summaries "
    'unless they explicitly ask for another language.'
)

_DEFAULT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / 'mat_master_system_prompt.txt'
)
_TOOL_RULES_PATH = Path(__file__).resolve().parent / 'tool_rules.txt'


def _format_tool_groups(groups: list[tuple[str, str, str]]) -> str:
    lines = ['Mat tools (names have mat_ prefix):']
    for _, name, desc in groups:
        lines.append(f"- {name}: {desc}")
    return '\n'.join(lines)


def _mode_contract(mode_profile: str) -> tuple[str, str]:
    mode = (mode_profile or 'direct').strip().lower()
    if mode == 'planner':
        mode_contract = (
            '# Mode Contract (Planner)\n'
            '- This run is in **planner** mode: prioritize long-horizon quality and robustness.\n'
            '- For long-form writing/data aggregation tasks, use staged workflows with checkpoints and validation/fix loops.\n'
            '- Do not stop early when quality gates fail; continue fix -> re-validate until pass or explicit blocker.\n'
            '- For manuscript/review deliverables, depth and completeness are preferred over minimal latency.\n'
        )
        final_doc_rule = (
            '**Final document delivery (survey / manuscript / report)**: '
            'When the deliverable is a written report or manuscript, output the complete final document '
            'in chat first, ensure it is saved to file, then call finish.'
        )
    else:
        mode_contract = (
            '# Mode Contract (Direct)\n'
            '- This run is in **direct** mode: prioritize fast, reliable completion with minimal orchestration.\n'
            '- Default to concise outputs unless the user explicitly requests a long report/file deliverable.\n'
            '- Avoid heavy multi-stage writing/data pipelines unless the task clearly requires them.\n'
            '- For long-form deliverables, still follow mandatory quality/citation rules, but keep execution pragmatic.\n'
            '- For **multi-batch aggregation** (e.g. literature over time intervals → one big CSV): each mat_sn_* result is **auto-saved** under _tmp/tool_outputs/. For the final CSV, **merge from files** (run a script that reads _tmp/tool_outputs/mat_sn_*/ or your batch_*.csv); never generate the final CSV from conversation content.\n'
        )
        final_doc_rule = (
            '**Final document delivery (survey / manuscript / report)**: '
            'For direct mode, default to concise summary in chat plus file path. '
            'Output the full document in chat only when the user explicitly asks to see full text.'
        )
    return mode_contract, final_doc_rule


def _load_system_prompt_template(template_text: str | None) -> str:
    if template_text:
        return template_text
    if _DEFAULT_TEMPLATE_PATH.exists():
        return _DEFAULT_TEMPLATE_PATH.read_text(encoding='utf-8')
    raise FileNotFoundError(
        f"System prompt template not found at {_DEFAULT_TEMPLATE_PATH}. "
        'Provide template_text or ensure the file exists.'
    )


def compose_mat_master_system_prompt(
    template_text: str,
    *,
    registry: AsyncToolRegistry,
    mode_profile: str = 'direct',
    tool_groups: list[tuple[str, str, str]] | None = None,
) -> str:
    """Compose runtime-only dynamic fields into a prompt template text."""
    groups = tool_groups if tool_groups is not None else TOOL_GROUPS
    tool_block = _format_tool_groups(groups)
    mode_contract, final_doc_rule = _mode_contract(mode_profile)
    replacements = {
        '{{MAT_LANGUAGE_RULE}}': LANGUAGE_RULE,
        '{{MAT_SW_LIST}}': registry.software_list_str(),
        '{{MAT_SERVER_MAP}}': registry.server_mapping_str(),
        '{{MAT_TOOL_BLOCK}}': tool_block,
        '{{MAT_MODE_CONTRACT}}': mode_contract,
        '{{MAT_EXEC_CONSTRAINTS}}': registry.format_execution_constraints(),
        '{{MAT_CALC_RULES}}': registry.format_calculation_rules(),
        '{{MAT_CRP_BLOCK}}': registry.crp_block_str(),
        '{{MAT_PREFERRED_DFT}}': registry.crp['tool_stack'].get(
            'preferred_dft', 'ABACUS'
        ),
        '{{MAT_FINAL_DOC_RULE}}': final_doc_rule,
    }
    composed = template_text
    for token, value in replacements.items():
        composed = composed.replace(token, value)
    # Keep compatibility with existing {{ASYNC_*}} / {{CRP_*}} placeholders.
    composed = registry.replace_placeholders(composed)
    import re as _re

    unreplaced = _re.findall(r'\{\{MAT_[A-Z_]+\}\}', composed)
    if unreplaced:
        raise ValueError(
            f"Unreplaced MAT placeholders in system prompt: {unreplaced}. "
            'Check template and replacements dict.'
        )
    return composed


def build_mat_master_system_prompt(
    current_date: str | None = None,
    os_type: str | None = None,
    shell_type: str | None = None,
    tool_groups: list[tuple[str, str, str]] | None = None,
    registry: AsyncToolRegistry | None = None,
    mode_profile: str = 'direct',
    template_text: str | None = None,
) -> tuple[str, str, str, str]:
    """Build the Mat Master system prompt (constitution + tool affordance rules).

    Returns (static_prompt, current_date, os_type, shell_type). static_prompt already
    includes the composed template and tool_rules.txt. Caller should append working
    directory, citation format, skills meta, and "Today's date: ..." at the end.

    - current_date: e.g. '2026-02-07'; if not set, uses today (UTC).
    - tool_groups: default TOOL_GROUPS. For prompt caching, only the last line changes per day.
    - os_type: runtime OS type (e.g. Windows, Linux).
    - shell_type: runtime shell type (e.g. bash, zsh, cmd).
    - registry: AsyncToolRegistry for dynamic software-list injection (recommended).
    """
    if current_date is None:
        current_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if os_type is None:
        os_type = platform.system() or 'unknown'
    if shell_type is None:
        shell_path = (
            os.environ.get('SHELL')
            or os.environ.get('COMSPEC')
            or os.environ.get('ComSpec')
        )
        shell_type = os.path.basename(shell_path).lower() if shell_path else 'unknown'
    reg = registry or AsyncToolRegistry()
    static = compose_mat_master_system_prompt(
        _load_system_prompt_template(template_text),
        registry=reg,
        mode_profile=mode_profile,
        tool_groups=tool_groups,
    )
    # Centralized assembly: append tool affordance rules (single source; placeholders replaced)
    if _TOOL_RULES_PATH.exists():
        tool_rules = _TOOL_RULES_PATH.read_text(encoding='utf-8').strip()
        tool_rules = reg.replace_placeholders(tool_rules)
        static = static + '\n\n' + tool_rules
    return static, current_date, os_type, shell_type


def build_mat_master_user_prompt(
    task_id: str = '',
    task_type: str = '',
    description: str = '',
    input_data: str = '',
    **kwargs: Any,
) -> str:
    """Build the Mat Master user prompt. Same placeholders as evomaster Agent._get_user_prompt."""
    return f"""Complete the current task using the tools above.

Task ID: {task_id}
Task type: {task_type}
Description: {description}

Additional info:
{input_data}
"""
