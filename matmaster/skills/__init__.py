"""MatMaster Skills 模块

matmaster 核心包级别的技能集合。

层级定位：
- evomaster/skills/     — 框架通用技能（pdf、rag、mcp-builder 等）
- matmaster/skills/     — matmaster 核心技能（本目录）
- playground/*/skills/  — 应用场景专用技能

技能发现：SkillRegistry 扫描本目录子目录，加载含 SKILL.md 的目录为 Skill。
"""

from __future__ import annotations
