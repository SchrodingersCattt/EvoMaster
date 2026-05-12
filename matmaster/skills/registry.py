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
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _optional_strip(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s if s else None


def _parse_depends_on_list(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


SkillTypeLiteral = Literal["operator", "mcp-loader", "orchestrator"]


def _parse_skill_type(raw: str | None) -> SkillTypeLiteral | None:
    s = _optional_strip(raw)
    if s is None:
        return None
    if s == "operator":
        return "operator"
    if s == "mcp-loader":
        return "mcp-loader"
    if s == "orchestrator":
        return "orchestrator"
    raise ValueError(f"Invalid skill_type: {s!r}")


# ---------------------------------------------------------------------------
# SkillMetaInfo
# ---------------------------------------------------------------------------


class SkillMetaInfo(BaseModel):
    """Skill 元信息，从 SKILL.md 的 frontmatter 解析。"""

    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述")
    skill_type: SkillTypeLiteral | None = None
    mcp_server: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict, description="扩展字段")


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


class Skill:
    """单个 Skill 的运行时表示。

    从磁盘上的 skill 目录加载：
    - SKILL.md frontmatter → meta_info (SkillMetaInfo)
    - SKILL.md body → full_info (延迟缓存)
    """

    def __init__(self, skill_path: Path) -> None:
        self.skill_path = skill_path
        self.meta_info = self._parse_meta_info()
        self._full_info_cache: str | None = None

    # -- meta_info parsing --------------------------------------------------

    def _parse_meta_info(self) -> SkillMetaInfo:
        skill_md = self.skill_path / "SKILL.md"
        if not skill_md.exists():
            raise FileNotFoundError(f"SKILL.md not found in {self.skill_path}")

        content = skill_md.read_text(encoding="utf-8")
        return _parse_meta_info_from_content(
            content, fallback_name=self.skill_path.name
        )

    # -- full_info ----------------------------------------------------------

    def get_full_info(self) -> str:
        """返回 SKILL.md frontmatter 之后的 body 部分（缓存）。"""
        if self._full_info_cache is not None:
            return self._full_info_cache

        content = (self.skill_path / "SKILL.md").read_text(encoding="utf-8")
        self._full_info_cache = _extract_full_info(content)
        return self._full_info_cache


class RemoteSkill:
    """Skill loaded from a session-backed remote root."""

    is_remote: bool = True

    def __init__(self, skill_path: PurePosixPath, content: str) -> None:
        self.skill_path = skill_path
        self.meta_info = _parse_meta_info_from_content(
            content,
            fallback_name=skill_path.name,
        )
        self._full_info_cache = _extract_full_info(content)

    def get_full_info(self) -> str:
        return self._full_info_cache


def _parse_meta_info_from_content(content: str, *, fallback_name: str) -> SkillMetaInfo:
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not fm_match:
        raise ValueError("Invalid SKILL.md: no frontmatter")

    known_keys = {"name", "description", "skill_type", "mcp_server", "depends_on"}
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
        name=data.get("name", fallback_name),
        description=data.get("description", ""),
        skill_type=_parse_skill_type(data.get("skill_type")),
        mcp_server=_optional_strip(data.get("mcp_server")),
        depends_on=_parse_depends_on_list(data.get("depends_on")),
        extras=extras,
    )


def _extract_full_info(content: str) -> str:
    body_match = re.search(r"^---\s*\n.*?\n---\s*\n(.*)$", content, re.DOTALL)
    return body_match.group(1).strip() if body_match else content


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
        *,
        remote_session: Any | None = None,
        remote_roots: list[str] | None = None,
    ) -> None:
        if isinstance(skills_root, Path):
            self._roots = [skills_root]
        else:
            self._roots = list(skills_root)

        self._skills: dict[str, Skill | RemoteSkill] = {}
        self._load_skills(skills)
        self._load_remote_skills(
            remote_session=remote_session,
            remote_roots=remote_roots or [],
            name_filter=skills,
        )

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

    def _load_remote_skills(
        self,
        *,
        remote_session: Any | None,
        remote_roots: list[str],
        name_filter: list[str] | None = None,
    ) -> None:
        if remote_session is None:
            return
        for raw_root in remote_roots:
            root_text = raw_root.strip()
            if not root_text:
                continue
            root = PurePosixPath(root_text)
            try:
                if not remote_session.path_exists(str(root)):
                    continue
                result = remote_session.exec_bash(
                    f"find {shlex.quote(str(root))} -type f -name SKILL.md | sort",
                    timeout=10,
                )
            except Exception:
                logger.warning(
                    "Failed to scan remote skill root %s", root, exc_info=True
                )
                continue
            if result.get("exit_code") != 0:
                logger.warning(
                    "Remote skill scan failed root=%s exit_code=%s",
                    root,
                    result.get("exit_code"),
                )
                continue

            skill_dirs: set[PurePosixPath] = set()
            stdout = result.get("stdout") or ""
            for line in str(stdout).splitlines():
                md_path_text = line.strip()
                if not md_path_text:
                    continue
                md_path = PurePosixPath(md_path_text)
                skill_dir = md_path.parent
                if self._has_underscore_ancestor(skill_dir, root):
                    continue
                if self._is_nested_under(skill_dir, skill_dirs):
                    continue
                try:
                    content = remote_session.read_file(str(md_path))
                    skill = RemoteSkill(skill_dir, content)
                except Exception:
                    logger.error(
                        "Failed to load remote skill from %s",
                        skill_dir,
                        exc_info=True,
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
    def _has_underscore_ancestor(
        skill_dir: Path | PurePosixPath,
        root: Path | PurePosixPath,
    ) -> bool:
        """skill_dir 到 root 之间是否有以 _ 开头的目录名。"""
        current = skill_dir
        while current != root and current != current.parent:
            if current.name.startswith("_"):
                return True
            current = current.parent
        return False

    @staticmethod
    def _is_nested_under(
        skill_dir: Path | PurePosixPath,
        known_skill_dirs: set[Path] | set[PurePosixPath],
    ) -> bool:
        """skill_dir 是否嵌套在某个已知 skill 目录的子目录内。"""
        parent = skill_dir.parent
        while parent != parent.parent:
            if parent in known_skill_dirs:
                return True
            parent = parent.parent
        return False

    # -- public API ---------------------------------------------------------

    def get_skill(self, name: str) -> Skill | RemoteSkill | None:
        return self._skills.get(name)

    def get_all_skills(self) -> list[Skill | RemoteSkill]:
        return list(self._skills.values())

    def remove_skills(self, names: set[str]) -> None:
        """按名称移除已注册的 skill。"""
        for name in names:
            self._skills.pop(name, None)

    def get_meta_info_context(self) -> str:
        """生成 [Skill: name] description 格式的汇总字符串。"""
        lines: list[str] = []
        for skill in self._skills.values():
            lines.append(
                f"[Skill: {skill.meta_info.name}] {skill.meta_info.description}"
            )
        return "\n".join(lines)
