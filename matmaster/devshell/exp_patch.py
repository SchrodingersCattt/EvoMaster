"""Narrow ``direct`` exp for local mm-devshell default (no duplicate toml).

``load_exp_config("direct")`` is the single source of truth for tools / max_turns /
developer text. We only override ``[skills].skills_root`` to the struct-DB lazymcp
stub so local REPL does not scan ``playground/mat_master/skills``.

- **Omit ``--exp`` or ``--exp devshell``**: ``direct`` + patch below.
- **``--exp direct``**: unpatched ``direct.toml`` (same as production skill trees).
"""

from __future__ import annotations

from matmaster.config.exp import ExpConfig

# Same path historically used by removed ``DevMcpConfig.skills_root`` default.
STRUCT_DB_LAZYMCP_ROOT = "matmaster/skills/lazymcp/mcp-mat-struct-db"


def patch_direct_skills_for_devshell_default(exp_cfg: ExpConfig) -> ExpConfig:
    """Return a copy of *exp_cfg* with ``skills_root`` replaced by the struct-DB stub only."""
    if not exp_cfg.skills.enabled:
        return exp_cfg
    narrowed = exp_cfg.skills.model_copy(
        update={"skills_root": [STRUCT_DB_LAZYMCP_ROOT]},
    )
    return exp_cfg.model_copy(update={"skills": narrowed})


def devshell_default_exp_config() -> ExpConfig:
    """``load_exp_config('direct')`` + :func:`patch_direct_skills_for_devshell_default`."""
    from matmaster.config.loader import load_exp_config

    return patch_direct_skills_for_devshell_default(load_exp_config("direct"))
