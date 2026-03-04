"""EvoMaster Skills 模块

与上游 sjtu-sai-agents/EvoMaster 一致：
- 仅导出 BaseSkill、Skill、SkillMetaInfo、SkillRegistry
- 类名 Skill（可执行技能）、SkillMetaInfo 无 skill_type 字段、create_subset(skill_names)

技能层级：
1. 第一层级 meta_info: 技能元信息
2. 第二层级 full_info: 完整信息
3. 第三层级 scripts: 可执行脚本
"""

from .base import (
    BaseSkill,
    Skill,
    SkillMetaInfo,
    SkillRegistry,
)

__all__ = [
    'BaseSkill',
    'Skill',
    'SkillMetaInfo',
    'SkillRegistry',
]
