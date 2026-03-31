"""MatMaster-native Skill & SkillRegistry

与 evomaster/skills/base.py 解耦的独立实现。

核心差异：
- 无 ABC 抽象基类，Skill 为具体类（不需要 to_context_string 抽象）
- SkillRegistry 支持多根目录 (list[Path]) + rglob 递归发现
- 后注册的根目录覆盖先注册的同名 skill
- 跳过 _ 前缀目录和嵌套在其他 skill 子目录内的 SKILL.md
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SkillMetaInfo
# ---------------------------------------------------------------------------


class SkillMetaInfo(BaseModel):
    """Skill 元信息，从 SKILL.md 的 frontmatter 解析。"""

    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述")
    extras: dict[str, Any] = Field(default_factory=dict, description="扩展字段")


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


class Skill:
    """单个 Skill 的运行时表示。

    从磁盘上的 skill 目录加载：
    - SKILL.md frontmatter → meta_info (SkillMetaInfo)
    - SKILL.md body → full_info (延迟缓存)
    - scripts/ 子目录 → available_scripts
    """

    def __init__(self, skill_path: Path) -> None:
        self.skill_path = skill_path
        self.meta_info = self._parse_meta_info()
        self._full_info_cache: str | None = None
        self.available_scripts: list[Path] = self._scan_scripts()

    # -- meta_info parsing --------------------------------------------------

    def _parse_meta_info(self) -> SkillMetaInfo:
        skill_md = self.skill_path / "SKILL.md"
        if not skill_md.exists():
            raise FileNotFoundError(f"SKILL.md not found in {self.skill_path}")

        content = skill_md.read_text(encoding="utf-8")

        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not fm_match:
            raise ValueError(f"Invalid SKILL.md: no frontmatter in {skill_md}")

        known_keys = {"name", "description"}
        data: dict[str, str] = {}
        for line in fm_match.group(1).split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip()

        extras = {k: v for k, v in data.items() if k not in known_keys}

        return SkillMetaInfo(
            name=data.get("name", self.skill_path.name),
            description=data.get("description", ""),
            extras=extras,
        )

    # -- full_info ----------------------------------------------------------

    def get_full_info(self) -> str:
        """返回 SKILL.md frontmatter 之后的 body 部分（缓存）。"""
        if self._full_info_cache is not None:
            return self._full_info_cache

        content = (self.skill_path / "SKILL.md").read_text(encoding="utf-8")
        body_match = re.search(r"^---\s*\n.*?\n---\s*\n(.*)$", content, re.DOTALL)
        self._full_info_cache = body_match.group(1).strip() if body_match else content
        return self._full_info_cache

    # -- references ---------------------------------------------------------

    def get_reference(self, name: str) -> str:
        """查找参考文档，按优先级搜索多个候选路径。"""
        candidates = [
            self.skill_path / name,
            self.skill_path / "references" / name,
            self.skill_path / "reference" / name,
            self.skill_path / "prompts" / name,
            self.skill_path.parent / "_common" / "reference" / name,
            self.skill_path.parent / "_common" / name,
        ]
        for p in candidates:
            if p.exists():
                return p.read_text(encoding="utf-8")
        raise FileNotFoundError(
            f"Reference {name!r} not found for skill at {self.skill_path}"
        )

    # -- scripts ------------------------------------------------------------

    def _scan_scripts(self) -> list[Path]:
        scripts_dir = self.skill_path / "scripts"
        if not scripts_dir.exists():
            return []
        return [
            p
            for p in scripts_dir.iterdir()
            if p.is_file() and p.suffix in {".py", ".sh", ".js"}
        ]

    def get_script_path(self, name: str) -> Path | None:
        for s in self.available_scripts:
            if s.name == name:
                return s
        return None


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------


class SkillRegistry:
    """多根目录递归 Skill 注册中心。

    - 支持单个 Path 或 list[Path] 作为根目录
    - rglob("SKILL.md") 递归发现
    - 跳过 _ 前缀目录、跳过嵌套在其他 skill 子目录内的 SKILL.md
    - 后注册根目录的同名 skill 覆盖先注册的
    """

    def __init__(
        self,
        skills_root: Path | list[Path],
        skills: list[str] | None = None,
    ) -> None:
        if isinstance(skills_root, Path):
            self._roots = [skills_root]
        else:
            self._roots = list(skills_root)

        self._skills: dict[str, Skill] = {}
        self._load_skills(skills)

    # -- discovery ----------------------------------------------------------

    def _load_skills(self, name_filter: list[str] | None = None) -> None:
        for root in self._roots:
            if not root.exists():
                continue
            # 收集此 root 下所有 SKILL.md，按路径排序以保证确定性
            skill_md_paths = sorted(root.rglob("SKILL.md"))

            # 预计算已知 skill 目录，用于判断嵌套
            skill_dirs: set[Path] = set()

            for md_path in skill_md_paths:
                skill_dir = md_path.parent

                # 跳过：目录链上任何一级以 _ 开头
                if self._has_underscore_ancestor(skill_dir, root):
                    continue

                # 跳过：嵌套在已注册 skill 目录的子目录内
                if self._is_nested_under(skill_dir, skill_dirs):
                    continue

                try:
                    skill = Skill(skill_dir)
                except Exception:
                    logger.error(
                        "Failed to load skill from %s", skill_dir, exc_info=True
                    )
                    continue

                if name_filter is not None and skill.meta_info.name not in name_filter:
                    continue

                if skill.meta_info.name in self._skills:
                    logger.warning(
                        "Skill %r overridden by %s",
                        skill.meta_info.name,
                        skill_dir,
                    )
                self._skills[skill.meta_info.name] = skill
                skill_dirs.add(skill_dir)

    @staticmethod
    def _has_underscore_ancestor(skill_dir: Path, root: Path) -> bool:
        """skill_dir 到 root 之间是否有以 _ 开头的目录名。"""
        current = skill_dir
        while current != root and current != current.parent:
            if current.name.startswith("_"):
                return True
            current = current.parent
        return False

    @staticmethod
    def _is_nested_under(skill_dir: Path, known_skill_dirs: set[Path]) -> bool:
        """skill_dir 是否嵌套在某个已知 skill 目录的子目录内。"""
        parent = skill_dir.parent
        while parent != parent.parent:
            if parent in known_skill_dirs:
                return True
            parent = parent.parent
        return False

    # -- public API ---------------------------------------------------------

    def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def get_all_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def get_meta_info_context(self) -> str:
        """生成 [Skill: name] description 格式的汇总字符串。"""
        lines: list[str] = []
        for skill in self._skills.values():
            lines.append(
                f"[Skill: {skill.meta_info.name}] {skill.meta_info.description}"
            )
        return "\n".join(lines)
