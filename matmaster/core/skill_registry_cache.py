from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from matmaster.config.exp import ExpSkillsConfig
from matmaster.skills.registry import (
    SkillRegistry,
    SkillRegistryCache,
    _normalize_remote_roots,
    read_disabled_plugins,
)
from matmaster.skills.settings import (
    disabled_skill_names_from_remote_settings as _disabled_skill_names_from_remote_settings,
)
from matmaster.skills.settings import (
    disabled_skill_names_from_settings as _disabled_skill_names_from_settings,
)
from matmaster.skills.settings import local_user_skills_root as _local_user_skills_root
from matmaster.skills.settings import remote_skill_roots as _remote_skill_roots

logger = logging.getLogger(__name__)

SkillRegistryCacheKey = tuple[
    tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]
]


def _normalized_names(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(name.strip() for name in names if name.strip()))


def skill_registry_cache_key(
    *,
    local_roots: list[Path],
    remote_roots: list[str],
    config_disabled_skill_names: Iterable[str],
    disabled_plugins: Iterable[str],
) -> SkillRegistryCacheKey:
    return (
        tuple(str(root) for root in local_roots),
        tuple(_normalize_remote_roots(remote_roots)),
        _normalized_names(config_disabled_skill_names),
        _normalized_names(disabled_plugins),
    )


def build_cached_skill_registry(
    *,
    skills_cfg: ExpSkillsConfig,
    session: Any | None,
    skill_cache: SkillRegistryCache,
) -> SkillRegistry | None:
    roots_raw = skills_cfg.skills_root
    if isinstance(roots_raw, list):
        roots = [Path(root) for root in roots_raw if root]
    else:
        roots = [Path(roots_raw)] if roots_raw else []

    local_user_skills_root = _local_user_skills_root(session)
    if local_user_skills_root is not None:
        roots.append(local_user_skills_root)

    remote_roots = _remote_skill_roots(session)
    if not roots and not remote_roots:
        return None

    disabled_plugins = read_disabled_plugins(
        Path(skills_cfg.config_dir) / "plugins.yaml"
    )
    config_disabled_skill_names = _normalized_names(skills_cfg.disabled_skill_names)

    key = skill_registry_cache_key(
        local_roots=roots,
        remote_roots=remote_roots,
        config_disabled_skill_names=config_disabled_skill_names,
        disabled_plugins=disabled_plugins,
    )

    def build() -> SkillRegistry:
        # Resolving disabled names reads .settings.json files; the remote reads
        # are SSH round-trips. Keep this inside the memoized builder so repeated
        # spawns within one query reuse it instead of re-reading on cache hits.
        disabled_skill_names = set(config_disabled_skill_names)
        for root in roots:
            disabled_skill_names.update(_disabled_skill_names_from_settings(root))
        if remote_roots and session is not None:
            for remote_root in remote_roots:
                disabled_skill_names.update(
                    _disabled_skill_names_from_remote_settings(session, remote_root)
                )
        registry = SkillRegistry(
            roots,
            remote_session=session if remote_roots else None,
            remote_roots=remote_roots,
        )
        removed_members = registry.remove_plugin_members(disabled_plugins)
        if disabled_skill_names:
            registry.remove_skills(disabled_skill_names)
        if removed_members:
            for skill in registry.get_all_skills():
                broken = [
                    dep for dep in skill.meta_info.depends_on if dep in removed_members
                ]
                if broken:
                    logger.warning(
                        "Skill %r depends_on member(s) of disabled plugin(s): %s",
                        skill.meta_info.name,
                        ", ".join(broken),
                    )
        return registry

    return skill_cache.get_or_build(key, build)
