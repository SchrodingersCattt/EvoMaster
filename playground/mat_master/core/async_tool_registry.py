"""Async Tool Registry — **derived** from ``mcp.calculation_executors``.

Rule:  A tool is "async" (runs on a remote machine) if its server has
``executor.machine.remote_profile`` (or an ``executor_map`` entry has one)
AND the tool is **not** in ``sync_tools``.

No separate software list is needed — everything is inferred from the
existing MCP executor config that already defines images and machine types.

Usage::

    from .async_tool_registry import AsyncToolRegistry

    registry = AsyncToolRegistry(config_dict)   # full config.model_dump()
    print(registry.software_list_str())         # "DPA, ABACUS, LAMMPS, ..."
    text = registry.replace_placeholders(template)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Tiny data class for one async "software" entry
# ---------------------------------------------------------------------------

@dataclass
class _AsyncEntry:
    server_prefix: str        # MCP server name, e.g. "mat_dpa"
    software_name: str        # derived display name, e.g. "DPA"
    tool_key: str | None      # executor_map key (e.g. "run_lammps"), or None for server-level executor
    sync_tools: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_remote_profile(executor_cfg: Any) -> bool:
    """Return True if an executor dict tree contains ``machine.remote_profile``."""
    if not isinstance(executor_cfg, dict):
        return False
    machine = executor_cfg.get("machine") or {}
    return bool(machine.get("remote_profile"))


def _derive_name(key: str) -> str:
    """Derive a human-readable software name from a config key.

    ``mat_dpa``  → ``DPA``
    ``mat_abacus`` → ``ABACUS``
    ``run_lammps`` → ``LAMMPS``
    ``run_quantum_espresso`` → ``QUANTUM_ESPRESSO``
    """
    name = key
    for prefix in ("mat_", "run_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.upper()


# ---------------------------------------------------------------------------
# CRP defaults (used when config has no ``mat_master.crp`` section)
# ---------------------------------------------------------------------------

_DEFAULT_CRP: dict[str, Any] = {
    "allow_list": [
        "ABACUS", "LAMMPS", "DPA", "CP2K", "QE", "ABINIT", "ORCA",
        "OpenBabel",
        "mat_abacus", "mat_dpa", "mat_sg", "mat_doc", "mat_sn",
    ],
    "block_list": ["VASP", "Gaussian", "CASTEP", "Wien2k"],
    "tool_stack": {
        "preferred_dft": "ABACUS",
        "preferred_mlp": "DPA",
        "preferred_md": "LAMMPS",
    },
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class AsyncToolRegistry:
    """Registry of async (remote-execution) software, **derived** from config.

    Reads ``mcp.calculation_executors`` and applies the rule:
        *has remote_profile* ∧ *not in sync_tools*  →  async

    Also reads ``mat_master.crp`` for CRP policy (block/allow/preferred).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        # ── Derive async entries from mcp.calculation_executors ────────────
        mcp_cfg = config.get("mcp") or {}
        calc_executors: dict[str, Any] = mcp_cfg.get("calculation_executors") or {}
        self._entries: list[_AsyncEntry] = self._parse_executors(calc_executors)

        # ── CRP policy ────────────────────────────────────────────────────
        mat = config.get("mat_master") or {}
        crp_raw = mat.get("crp") or _DEFAULT_CRP
        self._crp: dict[str, Any] = {
            "allow_list": crp_raw.get("allow_list", _DEFAULT_CRP["allow_list"]),
            "block_list": crp_raw.get("block_list", _DEFAULT_CRP["block_list"]),
            "tool_stack": crp_raw.get("tool_stack", _DEFAULT_CRP["tool_stack"]),
        }

    # ── Parsing ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_executors(calc_executors: dict[str, Any]) -> list[_AsyncEntry]:
        """Walk ``calculation_executors`` and collect every async entry."""
        entries: list[_AsyncEntry] = []
        for server_name, server_cfg in calc_executors.items():
            if not isinstance(server_cfg, dict):
                continue
            executor = server_cfg.get("executor")
            executor_map: dict[str, Any] = server_cfg.get("executor_map") or {}
            sync_tools = frozenset(server_cfg.get("sync_tools") or [])

            # Case 1: server-level executor with remote_profile
            if _has_remote_profile(executor):
                entries.append(_AsyncEntry(
                    server_prefix=server_name,
                    software_name=_derive_name(server_name),
                    tool_key=None,
                    sync_tools=sync_tools,
                ))

            # Case 2: per-tool executor_map entries with remote_profile
            for tool_key, tool_executor in executor_map.items():
                if tool_key in sync_tools:
                    continue
                if _has_remote_profile(tool_executor):
                    entries.append(_AsyncEntry(
                        server_prefix=server_name,
                        software_name=_derive_name(tool_key),
                        tool_key=tool_key,
                        sync_tools=sync_tools,
                    ))

        return entries

    # ── Accessors ─────────────────────────────────────────────────────────

    @property
    def entries(self) -> list[_AsyncEntry]:
        return list(self._entries)

    @property
    def software_names(self) -> list[str]:
        """Unique uppercase names: ``["SG", "DPA", "ABACUS", "LAMMPS", ...]``"""
        seen: set[str] = set()
        out: list[str] = []
        for e in self._entries:
            if e.software_name not in seen:
                seen.add(e.software_name)
                out.append(e.software_name)
        return out

    @property
    def software_names_lower(self) -> list[str]:
        return [n.lower() for n in self.software_names]

    @property
    def crp(self) -> dict[str, Any]:
        return dict(self._crp)

    def get_server_prefix(self, software: str) -> str | None:
        sw = software.upper()
        for e in self._entries:
            if e.software_name == sw:
                return e.server_prefix
        return None

    # ── Formatted text snippets for prompt injection ──────────────────────

    def software_list_str(self) -> str:
        """``"SG, DPA, ABACUS, LAMMPS, CP2K, ABINIT, QUANTUM_ESPRESSO"``"""
        return ", ".join(self.software_names)

    def server_mapping_str(self) -> str:
        """``"mat_dpa_* for DPA; mat_abacus_* for ABACUS; mat_binary_calc_* for LAMMPS, CP2K, ..."``"""
        by_server: dict[str, list[str]] = {}
        for e in self._entries:
            by_server.setdefault(e.server_prefix, []).append(e.software_name)
        return "; ".join(
            f"{prefix}_* for {', '.join(names)}"
            for prefix, names in by_server.items()
        )

    def job_manager_software_arg(self) -> str:
        """For ``--software`` help: ``"sg, dpa, abacus, lammps, ..."``"""
        return ", ".join(n.lower() for n in self.software_names)

    # ── CRP snippets ──────────────────────────────────────────────────────

    def crp_allow_str(self) -> str:
        return ", ".join(self._crp["allow_list"])

    def crp_block_str(self) -> str:
        return ", ".join(self._crp["block_list"])

    def crp_context_dict(self) -> dict[str, Any]:
        """Build the CRP context dict used by the planner."""
        ts = self._crp["tool_stack"]
        return {
            "Protocol_Name": "MatMaster_CRP_v1.0",
            "License_Registry": {
                "Allow_List": list(self._crp["allow_list"]),
                "Block_List": list(self._crp["block_list"]),
                "Policy": "Strict_Block_Execution",
            },
            "Tool_Stack": {
                "Preferred_DFT": ts.get("preferred_dft", "ABACUS"),
                "Preferred_MLP": ts.get("preferred_mlp", "DPA"),
                "Preferred_MD":  ts.get("preferred_md", "LAMMPS"),
            },
        }

    # ── Prompt section generators ─────────────────────────────────────────

    def format_calculation_rules(self) -> str:
        sw = self.software_list_str()
        sm = self.server_mapping_str()
        return (
            f"# Calculation & Jobs (MANDATORY)\n"
            f"To run any heavy calculation ({sw}, etc.), follow this workflow:\n"
            f"1. **Compliance check**: Before submitting, call use_skill with skill_name=compliance-guardian, "
            f"script_name=check_compliance.py. Stop if allowed=false.\n"
            f"2. **Input generation**: Use the **input-manual-helper** skill to write and validate input files "
            f"(see tool_rules for the full procedure).\n"
            f"3. **Submit**: Call the appropriate MCP submit tool ({sm}). Note the returned job_id.\n"
            f"4. **Monitor & Resilience**: Call `use_skill(skill_name=\"job-manager\", action=\"run_script\", "
            f"script_name=\"run_resilient_job.py\", "
            f"script_args=\"--job_id <ID> --software <SW> --workspace <PATH>\")`. "
            f"Supported --software values: {self.job_manager_software_arg()}. "
            f"The script blocks until the job succeeds or fails, handles status polling, result downloading, "
            f"and error diagnosis internally.\n"
            f"5. **On failure with fix suggestion**: If job-manager returns status=\"needs_fix\", apply the "
            f"suggested parameter changes to the input files, re-submit via MCP, and call job-manager again "
            f"with the new job_id.\n"
            f"Do NOT manually poll job_status in the chat loop — that wastes tokens and is fragile. "
            f"Let job-manager handle the entire lifecycle.\n"
            f"**File inputs**: All file/path arguments to calculation MCP tools must be **OSS URLs** "
            f"(https://...), not local paths. The system auto-uploads workspace files; ensure the file "
            f"exists locally before calling the tool. Pre-trained model shortcuts (e.g. \"DPA2.4-7M\") "
            f"are auto-resolved to full OSS URLs."
        )

    def format_execution_constraints(self) -> str:
        sw = self.software_list_str()
        sm = self.server_mapping_str()
        return (
            f"# Execution Environment Constraints\n"
            f"1. The local sandbox is ephemeral and computationally restricted (ASE, Pymatgen, data processing "
            f"only). No {sw} binaries are available locally.\n"
            f"2. Heavy calculations MUST be submitted via MCP tools ({sm}). "
            f"Never run these codes via execute_bash."
        )

    def format_planner_license_firewall(self) -> str:
        block = self.crp_block_str()
        ts = self._crp["tool_stack"]
        return (
            f"1. **Commercial License Barrier**\n"
            f"   - You are STRICTLY PROHIBITED from planning execution of proprietary software "
            f"({block}) unless explicitly listed in `License_Keys`.\n"
            f"   - **Mandatory Mapping** when the user asks for blocked software but licenses are missing:\n"
            f"     - DFT tasks -> plan goals that can be fulfilled by **{ts.get('preferred_dft', 'ABACUS')}** "
            f"(open source), **CP2K**, **Quantum Espresso**, or **ABINIT**.\n"
            f"     - MD / Potential / screening -> goals achievable with "
            f"**{ts.get('preferred_mlp', 'DPA')}**, **{ts.get('preferred_md', 'LAMMPS')}**, or **CP2K**.\n"
            f"     - If the request cannot be fulfilled with open alternatives, return status `REFUSED` "
            f"with a clear refusal_reason."
        )

    # ── Placeholder replacement helper ────────────────────────────────────

    def replace_placeholders(self, text: str) -> str:
        """Replace ``{{PLACEHOLDER}}`` tokens in *text* with derived values.

        Supported::

            {{ASYNC_SOFTWARE_LIST}}       - DPA, ABACUS, LAMMPS, ...
            {{ASYNC_SERVER_MAPPING}}      - mat_dpa_* for DPA; ...
            {{ASYNC_JOB_MANAGER_SW}}      - dpa, abacus, lammps, ...
            {{CRP_BLOCK_LIST}}            - VASP, Gaussian, CASTEP, Wien2k
            {{CRP_ALLOW_LIST}}            - ABACUS, LAMMPS, DPA, ...
            {{CRP_PREFERRED_DFT}}         - ABACUS
            {{CRP_PREFERRED_MLP}}         - DPA
            {{CRP_PREFERRED_MD}}          - LAMMPS
            {{CALC_RULES}}                - Full "Calculation & Jobs" section
            {{EXEC_CONSTRAINTS}}          - Full "Execution Environment Constraints" section
        """
        ts = self._crp["tool_stack"]
        replacements = {
            "{{ASYNC_SOFTWARE_LIST}}":   self.software_list_str(),
            "{{ASYNC_SERVER_MAPPING}}":  self.server_mapping_str(),
            "{{ASYNC_JOB_MANAGER_SW}}":  self.job_manager_software_arg(),
            "{{CRP_BLOCK_LIST}}":        self.crp_block_str(),
            "{{CRP_ALLOW_LIST}}":        self.crp_allow_str(),
            "{{CRP_PREFERRED_DFT}}":     ts.get("preferred_dft", "ABACUS"),
            "{{CRP_PREFERRED_MLP}}":     ts.get("preferred_mlp", "DPA"),
            "{{CRP_PREFERRED_MD}}":      ts.get("preferred_md", "LAMMPS"),
            "{{CALC_RULES}}":            self.format_calculation_rules(),
            "{{EXEC_CONSTRAINTS}}":      self.format_execution_constraints(),
        }
        for token, value in replacements.items():
            text = text.replace(token, value)
        return text
