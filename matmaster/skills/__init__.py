"""MatMaster Skills 模块

matmaster 核心包级别的技能集合。

层级定位：
- matmaster/skills/<skill>/  — 扁平轨独立技能（由技能注册表扫描 SKILL.md 加载）
- matmaster/plugins/<plugin>/skills/<skill>/ — plugin 轨强关联技能簇（兄弟根，同机制扫描）
- .archive/evomaster-skills/ — 归档的框架通用技能（pdf、rag、mcp-builder 等，仅参考用途）

技能发现：技能注册表扫描 skills_root 配置指定的目录，加载含 SKILL.md 的子目录为 Skill。
"""

from __future__ import annotations
