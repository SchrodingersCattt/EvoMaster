"""Narrow ``direct`` exp for local mm-devshell default (no duplicate toml).

``load_exp_config("direct")`` is the single source of truth for tools / max_turns /
developer text. We override ``[skills].skills_root`` to struct-DB, structure-gen,
tasker-polar-surface, and structure-manager skills, and set
``mcp_runtime_patch.tool_include_only.mat_sg`` to expose core structure-construction
tools (slab, interface, adsorbate, supercell, etc.) from the MCP server.

- **Omit ``--exp`` or ``--exp devshell``**: ``direct`` + patch below.
- **``--exp direct``**: unpatched ``direct.toml`` (same as production skill trees).
"""

from __future__ import annotations

from matmaster.config.exp import ExpConfig

# Same paths historically used by removed ``DevMcpConfig.skills_root`` default.
STRUCT_DB_LAZYMCP_ROOT = "matmaster/skills/lazymcp/mcp-mat-struct-db"
STRUCT_GEN_LAZYMCP_ROOT = "matmaster/skills/lazymcp/mcp-mat-sg"
# Skills with scripts for surface construction workflow and validation.
TASKER_POLAR_SURFACE_ROOT = "matmaster/skills/playground-skills/tasker-polar-surface"
STRUCTURE_MANAGER_ROOT = "matmaster/skills/playground-skills/structure-manager"

# Devshell: expose core structure-construction tools from mat_sg.
DEVSHELL_MAT_SG_TOOLS = (
    "generate_ordered_replicas",
    "build_bulk_structure_by_template",
    "build_bulk_structure_by_wyckoff",
    "build_molecule_structures_from_smiles",
    "build_surface_slab",
    "build_surface_adsorbate",
    "build_surface_interface",
    "make_supercell_structure",
    "get_structure_info",
    "apply_structure_transformation",
    "add_hydrogens",
)


def patch_direct_skills_for_devshell_default(exp_cfg: ExpConfig) -> ExpConfig:
    """Return a copy of *exp_cfg* with narrowed lazymcp roots and mat_sg tool filter."""
    if not exp_cfg.skills.enabled:
        return exp_cfg
    narrowed = exp_cfg.skills.model_copy(
        update={
            "skills_root": [
                STRUCT_DB_LAZYMCP_ROOT,
                STRUCT_GEN_LAZYMCP_ROOT,
                TASKER_POLAR_SURFACE_ROOT,
                STRUCTURE_MANAGER_ROOT,
            ],
            "mcp_runtime_patch": {
                "tool_include_only": {"mat_sg": list(DEVSHELL_MAT_SG_TOOLS)},
            },
        },
    )
    return exp_cfg.model_copy(update={"skills": narrowed})


def devshell_default_exp_config() -> ExpConfig:
    """``load_exp_config('direct')`` + :func:`patch_direct_skills_for_devshell_default`."""
    from matmaster.config.loader import load_exp_config

    return patch_direct_skills_for_devshell_default(load_exp_config("direct"))
