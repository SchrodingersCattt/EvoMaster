"""MatMaster Skills 模块

matmaster 核心包级别的技能集合。

层级定位：
- matmaster/skills/lazymcp/  — LazyMCP 技能（活跃路径，由 SkillRegistry 扫描 SKILL.md 加载）
- .archive/evomaster-skills/ — 归档的框架通用技能（pdf、rag、mcp-builder 等，仅参考用途）

技能发现：SkillRegistry 扫描 skills_root 配置指定的目录，加载含 SKILL.md 的子目录为 Skill。
"""

from __future__ import annotations
