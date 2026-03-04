"""EvoMaster Skills 基类

提供 Skill 的基础抽象和注册机制。
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


class SkillMetaInfo(BaseModel):
    """Skill 元信息（Level 1）

    从 SKILL.md 的 YAML frontmatter 解析得到。
    与上游一致：仅 name、description、license。
    """

    name: str = Field(description='技能名称')
    description: str = Field(description='技能描述，包含使用场景和触发条件')
    license: str | None = Field(default=None, description='许可证信息')


class BaseSkill(ABC):
    """Skill 基类

    Skills 是 EvoMaster 的技能组件，包含：
    - Level 1 (meta_info): 技能元信息 (~100 tokens)，总在上下文
    - Level 2 (full_info): 完整信息 (500-2000 tokens)，按需加载
    - Level 3 (scripts): 可执行代码（可选 scripts 目录）
    """

    # 技能类型
    skill_type: ClassVar[str] = 'base'

    def __init__(self, skill_path: Path):
        """初始化 Skill

        Args:
            skill_path: 技能目录路径
        """
        self.skill_path = skill_path
        self.logger = logging.getLogger(self.__class__.__name__)

        # 解析 meta_info
        self.meta_info = self._parse_meta_info()

        # full_info 缓存（延迟加载）
        self._full_info_cache: str | None = None

    def _parse_meta_info(self) -> SkillMetaInfo:
        """解析 SKILL.md 的 frontmatter 获取 meta_info

        Returns:
            SkillMetaInfo 对象
        """
        skill_md_path = self.skill_path / 'SKILL.md'
        if not skill_md_path.exists():
            raise FileNotFoundError(f"SKILL.md not found in {self.skill_path}")

        content = skill_md_path.read_text(encoding='utf-8')

        # 解析 YAML frontmatter
        frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if not frontmatter_match:
            raise ValueError(
                f"Invalid SKILL.md format: no YAML frontmatter found in {skill_md_path}"
            )

        frontmatter_text = frontmatter_match.group(1)

        # 简单的 YAML 解析（仅支持 key: value 格式）
        frontmatter_data = {}
        for line in frontmatter_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                key, value = line.split(':', 1)
                frontmatter_data[key.strip()] = value.strip()

        return SkillMetaInfo(
            name=frontmatter_data.get('name', self.skill_path.name),
            description=frontmatter_data.get('description', ''),
            license=frontmatter_data.get('license'),
        )

    def get_full_info(self) -> str:
        """获取完整信息（Level 2）

        若存在 job_submit.md 则返回其内容；否则从 SKILL.md 的 body 提取。
        """
        if self._full_info_cache is not None:
            return self._full_info_cache

        job_submit_path = self.skill_path / 'job_submit.md'
        if job_submit_path.exists():
            self._full_info_cache = job_submit_path.read_text(encoding='utf-8').strip()
            return self._full_info_cache

        skill_md_path = self.skill_path / 'SKILL.md'
        content = skill_md_path.read_text(encoding='utf-8')
        body_match = re.search(r'^---\s*\n.*?\n---\s*\n(.*)$', content, re.DOTALL)
        if body_match:
            self._full_info_cache = body_match.group(1).strip()
        else:
            self._full_info_cache = content
        return self._full_info_cache

    def get_reference(self, reference_name: str) -> str:
        """获取参考文档内容

        Args:
            reference_name: 参考文档名称（如 "forms.md", "reference/api.md"）

        Returns:
            参考文档内容
        """
        # 尝试多个可能的路径，最后 fallback 到 _common/reference/
        possible_paths = [
            self.skill_path / reference_name,
            self.skill_path / 'references' / reference_name,
            self.skill_path / 'reference' / reference_name,
            self.skill_path / 'prompts' / reference_name,
            self.skill_path.parent / '_common' / 'reference' / reference_name,
            self.skill_path.parent / '_common' / reference_name,
        ]

        for ref_path in possible_paths:
            if ref_path.exists():
                return ref_path.read_text(encoding='utf-8')

        raise FileNotFoundError(
            f"Reference {reference_name} not found in {self.skill_path}"
        )

    @abstractmethod
    def to_context_string(self) -> str:
        """转换为上下文字符串

        返回应该添加到 Agent 上下文中的字符串。
        """


class Skill(BaseSkill):
    """Skill 具体实现（与上游一致）

    包含可执行脚本的技能：
    - Level 1: meta_info（总在上下文）
    - Level 2: full_info（按需加载）
    - Level 3: scripts（可执行脚本）
    """

    skill_type: ClassVar[str] = 'operator'

    def __init__(self, skill_path: Path):
        super().__init__(skill_path)

        # 扫描 scripts 目录
        self.scripts_dir = self.skill_path / 'scripts'
        self.available_scripts = self._scan_scripts()

    def _scan_scripts(self) -> list[Path]:
        """扫描 scripts 目录，获取所有可执行脚本

        Returns:
            脚本路径列表
        """
        if not self.scripts_dir.exists():
            return []

        scripts = []
        for script_path in self.scripts_dir.iterdir():
            if script_path.is_file() and script_path.suffix in ['.py', '.sh', '.js']:
                scripts.append(script_path)

        return scripts

    def get_script_path(self, script_name: str) -> Path | None:
        """获取脚本路径

        Args:
            script_name: 脚本名称

        Returns:
            脚本路径，如果不存在则返回 None
        """
        for script in self.available_scripts:
            if script.name == script_name:
                return script
        return None

    def to_context_string(self) -> str:
        """转换为上下文字符串（与上游一致：前缀 [Skill: ...]）"""
        scripts_info = (
            ', '.join([s.name for s in self.available_scripts])
            if self.available_scripts
            else 'No scripts'
        )
        return f"[Skill: {self.meta_info.name}] {self.meta_info.description} (Scripts: {scripts_info})"


class SkillRegistry:
    """Skill 注册中心（v0.0.2：单一存储，支持按名称过滤与 create_subset）

    管理所有可用的 Skills，支持：
    - 自动发现和加载 skills
    - 构造时 skills: list[str] | None 按名称过滤加载
    - create_subset(skill_names) 返回仅含指定名称的子集（与上游一致，不重新加载）
    - 按需检索 skill、提供 meta_info 供 Agent 选择
    """

    def __init__(
        self,
        skills_root: Path,
        skills: list[str] | None = None,
        *,
        _initial_skills: dict[str, BaseSkill] | None = None,
    ):
        """初始化 SkillRegistry

        Args:
            skills_root: skills 根目录，其子目录中含 SKILL.md 的将被加载为 Skill
            skills: 若提供，仅加载名称在此列表中的 skill；None 表示加载全部
            _initial_skills: 内部用，预填充的 skill 字典（用于 create_subset），不重新加载
        """
        self.skills_root = Path(skills_root)
        self.logger = logging.getLogger(self.__class__.__name__)

        if _initial_skills is not None:
            self._skills: dict[str, BaseSkill] = dict(_initial_skills)
        else:
            self._skills = {}
            self._load_skills(skills)

    def _load_skills(self, name_filter: list[str] | None = None) -> None:
        """从 skills_root 子目录加载所有含 SKILL.md 的 Skill（与上游 v0.0.2 统一类型）。"""

        def _accept(name: str) -> bool:
            return name_filter is None or name in name_filter

        if not self.skills_root.exists():
            return
        for skill_dir in self.skills_root.iterdir():
            if not skill_dir.is_dir() or not (skill_dir / 'SKILL.md').exists():
                continue
            try:
                skill = Skill(skill_dir)
                if _accept(skill.meta_info.name):
                    self._skills[skill.meta_info.name] = skill
                    self.logger.info('Loaded skill: %s', skill.meta_info.name)
            except Exception as e:
                self.logger.error('Failed to load skill from %s: %s', skill_dir, e)

    def get_skill(self, name: str) -> BaseSkill | None:
        """获取指定名称的 skill"""
        return self._skills.get(name)

    def get_all_skills(self) -> list[BaseSkill]:
        """获取所有 skills"""
        return list(self._skills.values())

    def create_subset(self, skill_names: list[str]) -> SkillRegistry:
        """返回仅包含指定名称的 skill 的子集注册表（与上游参数名一致，不重新加载磁盘）。"""
        subset = {k: v for k, v in self._skills.items() if k in skill_names}
        return SkillRegistry(
            self.skills_root,
            _initial_skills=subset,
        )

    def get_meta_info_context(self) -> str:
        """获取所有 skills 的 meta_info，用于添加到 Agent 上下文（与上游 v0.0.2 一致，不区分类型）。"""
        lines = ['# Available Skills\n']
        for skill in self.get_all_skills():
            lines.append(skill.to_context_string())
            lines.append('')
        return '\n'.join(lines)

    def search_skills(self, query: str) -> list[BaseSkill]:
        """搜索 skills

        Args:
            query: 搜索关键词

        Returns:
            匹配的 skills 列表
        """
        query_lower = query.lower()
        results = []

        for skill in self.get_all_skills():
            if (
                query_lower in skill.meta_info.name.lower()
                or query_lower in skill.meta_info.description.lower()
            ):
                results.append(skill)

        return results
