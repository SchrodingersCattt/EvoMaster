"""Static snapshot of tools / skills for devshell-backed evaluation.

Primary path: :func:`snapshot_eval_tooling` loads
:class:`~matmaster.config.exp.ExpConfig` via :func:`~matmaster.config.loader.load_exp_config`
(``matmaster/exps/{name}.toml``) — same as production / ``AgentRunService``. MCP paths resolve
through ``[skills].config_dir`` (typically ``matmaster_config/`` in repo).

:func:`snapshot_devshell_eval_tooling` uses production ``direct`` with the same skill-root
narrowing as mm-devshell default (see :mod:`matmaster.devshell.exp_patch`).

Used to populate ingest ``extra.eval_tooling`` so runs can be correlated with the
registered builtin list, skill catalog, and MCP server keys from config.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from matmaster.config.exp import ExpConfig, ExpSkillsConfig

logger = logging.getLogger(__name__)

# Order matches ``matmaster.core.exp.Exp._init_builtin_tools`` + spawn branch.
_BUILTIN_WHEN_STAR: list[str] = [
    "execute_bash",
    "list_dir",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "task_create",
    "task_get",
    "task_list",
    "task_update",
    "task_complete",
    "monitor_job",
    "web-search",
]


def _matmaster_config_session_type(repo_root: Path) -> str | None:
    """``session.type`` from ``matmaster_config/config.yaml`` (informational for ingest)."""
    from matmaster.config.loader import _load_raw

    path = repo_root / "matmaster_config" / "config.yaml"
    if not path.is_file():
        return None
    try:
        raw = _load_raw(path)
    except OSError:
        return None
    sess = raw.get("session") or {}
    if not isinstance(sess, dict):
        return None
    t = sess.get("type")
    return str(t).strip() if t else None


def _resolve_builtin_tool_names(builtin_cfg: list[str]) -> list[str]:
    """Resolve configured builtin list to concrete tool names (best-effort)."""
    if not builtin_cfg:
        return []
    if builtin_cfg == ["*"]:
        return list(_BUILTIN_WHEN_STAR) + ["spawn"]
    out: list[str] = []
    for name in builtin_cfg:
        if name == "*":
            continue
        if name not in out:
            out.append(name)
    return out


def _skills_roots_as_paths(skills_cfg: ExpSkillsConfig, repo_root: Path) -> list[Path]:
    raw = skills_cfg.skills_root
    paths: list[Path] = []
    if isinstance(raw, list):
        for r in raw:
            if not r:
                continue
            p = Path(str(r))
            paths.append(p if p.is_absolute() else (repo_root / p).resolve())
    elif raw:
        p = Path(str(raw))
        paths.append(p if p.is_absolute() else (repo_root / p).resolve())
    return paths


def _mcp_server_names(
    skills_cfg: ExpSkillsConfig,
    repo_root: Path,
) -> list[str]:
    """Read ``mcpServers`` keys using the same path rules as ``Exp._init_skill_tools``."""
    from matmaster.config.loader import _load_raw

    config_dir = Path(skills_cfg.config_dir)
    if not config_dir.is_absolute():
        config_dir = (repo_root / config_dir).resolve()
    mcp_runtime_path = config_dir / skills_cfg.mcp_runtime_file
    if not mcp_runtime_path.is_file():
        return []
    try:
        mcp_config = _load_raw(mcp_runtime_path)
    except OSError as e:
        logger.warning(
            "eval tooling: could not load MCP runtime %s: %s", mcp_runtime_path, e
        )
        return []

    mcp_config_file = mcp_config.get("config_file", skills_cfg.mcp_config_file)
    config_path = Path(str(mcp_config_file))
    if not config_path.is_absolute():
        config_path = config_dir / config_path

    if mcp_config.get("path_adaptor") == "calculation":
        try:
            from evomaster.adaptors.calculation import resolve_mcp_config_path

            config_path = resolve_mcp_config_path(config_path)
        except ImportError:
            pass

    if not config_path.is_file():
        return []
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        servers = raw.get("mcpServers") or {}
        if not isinstance(servers, dict):
            return []
        return sorted(str(k) for k in servers)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("eval tooling: could not read MCP JSON %s: %s", config_path, e)
        return []


def _build_eval_tooling_dict(
    *,
    repo_root: Path,
    exp_cfg: ExpConfig,
    matmaster_exp_reported: str,
) -> dict[str, Any]:
    """JSON-serializable tooling snapshot from an already-resolved :class:`ExpConfig`."""
    repo_root = repo_root.resolve()
    session_type = _matmaster_config_session_type(repo_root) or "local"

    builtin_cfg = list(exp_cfg.tools.builtin)
    builtin_names = _resolve_builtin_tool_names(builtin_cfg)

    skill_names: list[str] = []
    skills_roots_str: list[str] = []
    mcp_servers: list[str] = []
    surface_tools = list(builtin_names)

    if exp_cfg.skills.enabled:
        surface_tools.append("use_skill")
        root_paths = _skills_roots_as_paths(exp_cfg.skills, repo_root)
        skills_roots_str = [str(p) for p in root_paths]
        try:
            from matmaster.skills.registry import SkillRegistry

            existing = [p for p in root_paths if p.is_dir()]
            if existing:
                reg = SkillRegistry(existing)
                skill_names = sorted(s.meta_info.name for s in reg.get_all_skills())
        except Exception:
            logger.warning("eval tooling: SkillRegistry scan failed", exc_info=True)
        mcp_servers = _mcp_server_names(exp_cfg.skills, repo_root)

    return {
        "schema": "matmaster_eval_tooling_v1",
        "matmaster_exp": matmaster_exp_reported,
        "devshell_agent_name": exp_cfg.name,
        "devshell_max_turns": exp_cfg.max_turns,
        "session_type": session_type,
        "exp_config_name": exp_cfg.name,
        "max_turns": exp_cfg.max_turns,
        "tools_builtin_config": builtin_cfg,
        "tools_mcp_pattern": exp_cfg.tools.mcp,
        "builtin_tool_names": builtin_names,
        "tool_names_surface": surface_tools,
        "skills_enabled": exp_cfg.skills.enabled,
        "skills_roots": skills_roots_str,
        "skill_names": skill_names,
        "mcp_server_names": mcp_servers,
        "skills_skill_names_filter": list(exp_cfg.skills.skill_names),
    }


def snapshot_eval_tooling(
    *,
    repo_root: Path,
    exp_name: str = "direct",
) -> dict[str, Any]:
    """Snapshot from ``matmaster/exps/{exp_name}.toml`` (production-aligned).

    ``matmaster_exp`` in the output equals *exp_name*.
    """
    from matmaster.config.loader import load_exp_config

    name = exp_name.strip()
    exp_cfg = load_exp_config(name)
    return _build_eval_tooling_dict(
        repo_root=repo_root,
        exp_cfg=exp_cfg,
        matmaster_exp_reported=name,
    )


def snapshot_devshell_eval_tooling(*, repo_root: Path) -> dict[str, Any]:
    """Snapshot for mm-devshell default: ``direct`` + narrowed ``skills_root`` (see ``exp_patch``)."""
    from matmaster.devshell.exp_patch import devshell_default_exp_config

    exp_cfg = devshell_default_exp_config()
    return _build_eval_tooling_dict(
        repo_root=repo_root,
        exp_cfg=exp_cfg,
        matmaster_exp_reported="devshell",
    )
