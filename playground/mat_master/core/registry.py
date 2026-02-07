"""Mat Master skill registry: wraps core SkillRegistry and adds dynamic skills.

Used in MatMasterPlayground.setup(): we build MatMasterSkillRegistry (which
wraps evomaster SkillRegistry + optional dynamic root), then pass it to
_setup_tools and _create_agent. Dynamic skills (e.g. from SkillEvolutionExp)
are loaded from playground/mat_master/skills/dynamic/ and merged for lookup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evomaster.skills import BaseSkill, SkillRegistry


class MatMasterSkillRegistry:
    """Composite skill registry: core skills + dynamic skills.

    - core_registry: SkillRegistry(evomaster/skills) or similar.
    - dynamic_root: optional Path for playground/mat_master/skills/dynamic/.
      If set, we load OperatorSkill from each subdir with SKILL.md and
      merge with core for get_skill / get_all_skills / get_meta_info_context.
    """

    def __init__(
        self,
        core_registry: SkillRegistry,
        dynamic_root: Path | None = None,
        mat_skills_root: Path | None = None,
    ):
        self.core_registry = core_registry
        self.dynamic_root = Path(dynamic_root) if dynamic_root else None
        self.mat_skills_root = Path(mat_skills_root) if mat_skills_root else None
        self.logger = logging.getLogger(self.__class__.__name__)
        self._dynamic_skills: dict[str, BaseSkill] = {}
        self._mat_skills: dict[str, BaseSkill] = {}
        if self.mat_skills_root and self.mat_skills_root.exists():
            self._load_skills_from(self.mat_skills_root, self._mat_skills)
        if self.dynamic_root and self.dynamic_root.exists():
            self._load_skills_from(self.dynamic_root, self._dynamic_skills)

    def _load_skills_from(self, root: Path, out: dict) -> None:
        """Load OperatorSkill from each subdir of root that has SKILL.md into out."""
        from evomaster.skills import OperatorSkill

        for skill_dir in root.iterdir():
            if not skill_dir.is_dir():
                continue
            if not (skill_dir / "SKILL.md").exists():
                continue
            try:
                skill = OperatorSkill(skill_dir)
                out[skill.meta_info.name] = skill
                self.logger.info("Loaded skill: %s", skill.meta_info.name)
            except Exception as e:
                self.logger.warning("Failed to load skill from %s: %s", skill_dir, e)

    def register_dynamic_skill(self, skill_path: Path) -> bool:
        """Load one skill from skill_path and add to dynamic layer.

        Call after SkillEvolutionExp writes a new skill to disk.
        Returns True if loaded successfully.
        """
        from evomaster.skills import OperatorSkill

        path = Path(skill_path)
        if not (path / "SKILL.md").exists():
            self.logger.warning("No SKILL.md at %s", path)
            return False
        try:
            skill = OperatorSkill(path)
            self._dynamic_skills[skill.meta_info.name] = skill
            self.logger.info("Registered dynamic skill: %s", skill.meta_info.name)
            return True
        except Exception as e:
            self.logger.warning("Failed to register dynamic skill from %s: %s", path, e)
            return False

    def get_skill(self, name: str) -> BaseSkill | None:
        """Look up skill: dynamic first, then mat_skills, then core."""
        if name in self._dynamic_skills:
            return self._dynamic_skills[name]
        if name in self._mat_skills:
            return self._mat_skills[name]
        return self.core_registry.get_skill(name)

    def get_all_skills(self) -> list[BaseSkill]:
        """All skills: core + mat_skills + dynamic (later overwrites same name)."""
        by_name: dict[str, BaseSkill] = {}
        for s in self.core_registry.get_all_skills():
            by_name[s.meta_info.name] = s
        for n, s in self._mat_skills.items():
            by_name[n] = s
        for n, s in self._dynamic_skills.items():
            by_name[n] = s
        return list(by_name.values())

    def get_meta_info_context(self) -> str:
        """Meta info for context: merged core + dynamic."""
        lines = ["# Available Skills\n"]
        for skill in self.get_all_skills():
            lines.append(skill.to_context_string())
            lines.append("")
        return "\n".join(lines)

    def search_skills(self, query: str) -> list[BaseSkill]:
        """Search in core, mat_skills, and dynamic."""
        results = list(self.core_registry.search_skills(query))
        seen = {s.meta_info.name for s in results}
        query_lower = query.lower()
        for skill in list(self._mat_skills.values()) + list(self._dynamic_skills.values()):
            if skill.meta_info.name in seen:
                continue
            if query_lower in skill.meta_info.name.lower() or query_lower in skill.meta_info.description.lower():
                results.append(skill)
        return results
