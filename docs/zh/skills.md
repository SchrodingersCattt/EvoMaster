# Skills 模块

Skills 模块为 EvoMaster 提供技能系统（与上游 v0.0.2 一致：统一为 **Skill** 类型，不再区分 Knowledge/Operator）。

## 概述

```
evomaster/skills/
├── base.py           # BaseSkill, Skill, SkillRegistry
├── rag/              # RAG 技能
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── database.py
│   │   ├── encode.py
│   │   └── search.py
│   └── references/
├── pdf/
│   └── ...
└── {skill_name}/     # 每个含 SKILL.md 的子目录被加载为一个 Skill
    ├── SKILL.md
    ├── scripts/      # 可选；存在则通过 run_script 暴露
    └── references/
```

所有技能均从 **skills_root 的子目录**中加载（含 SKILL.md 即视为一个技能），统一实例化为 **Skill**。

## SkillMetaInfo

从 SKILL.md frontmatter 解析的元数据。无 skill_type 字段（与上游一致）。

```python
class SkillMetaInfo(BaseModel):
    name: str = Field(description="技能名称")
    description: str = Field(description="技能描述，包含使用场景")
    license: str | None = Field(default=None, description="许可证信息")
```

## BaseSkill

所有技能的抽象基类。

```python
class BaseSkill(ABC):
    """技能基类

    - Level 1 (meta_info)：~100 tokens，始终在上下文
    - Level 2 (full_info)：500-2000 tokens，按需加载
    - Level 3 (scripts)：可选；仅 Skill 可有 scripts
    """
    def __init__(self, skill_path: Path): ...
    def get_full_info(self) -> str: ...
    def get_reference(self, reference_name: str) -> str: ...
    @abstractmethod
    def to_context_string(self) -> str: ...
```

## Skill

唯一的具体技能类型（与上游 v0.0.2 一致）。加载出的技能均为 **Skill** 实例。

- **Level 1 (meta_info)**：始终在上下文
- **Level 2 (full_info)**：按需加载（来自 job_submit.md 或 SKILL.md 正文）
- **Level 3 (scripts)**：可选 `scripts/` 目录；存在则列出脚本，可通过 `use_skill` 的 `run_script` 执行

```python
class Skill(BaseSkill):
    def __init__(self, skill_path: Path):
        super().__init__(skill_path)
        self.scripts_dir = self.skill_path / "scripts"
        self.available_scripts = self._scan_scripts()

    def get_script_path(self, script_name: str) -> Path | None: ...
    def to_context_string(self) -> str:
        # 返回如 "[Skill: rag] ... (Scripts: encode.py, search.py)"
```

## SkillRegistry

从 skills_root 下含 SKILL.md 的子目录加载所有技能；支持按名称过滤与 create_subset。

```python
class SkillRegistry:
    def __init__(self, skills_root: Path, skills: list[str] | None = None, *, _initial_skills: dict | None = None):
        """skills_root: 根目录；每个含 SKILL.md 的子目录对应一个 Skill。skills: 可选名称过滤。"""

    def get_skill(self, name: str) -> BaseSkill | None: ...
    def get_all_skills(self) -> list[BaseSkill]: ...
    def create_subset(self, skill_names: list[str]) -> SkillRegistry: ...
    def get_meta_info_context(self) -> str:
        """所有技能的 meta_info，供 Agent 上下文使用（单一区块，不再区分 Knowledge/Operator）。"""
    def search_skills(self, query: str) -> list[BaseSkill]: ...
```

## SKILL.md 格式

### Frontmatter（YAML）

```yaml
---
name: skill-name
description: 简要描述，包含使用场景和触发条件
skill_type: knowledge  # 或 operator
license: MIT
---
```

### Body（Markdown）

body 部分包含 full_info（Level 2）：

```markdown
# 技能名称

## 概述

详细描述此技能的功能。

## 使用场景

何时使用此技能：
- 场景 1
- 场景 2

## 详情

技术细节、参数、示例等。

## 参考

- [参考 1](./references/ref1.md)
- [参考 2](./references/ref2.md)
```

## 目录结构

### Knowledge Skill

```
evomaster/skills/knowledge/
└── my_knowledge_skill/
    ├── SKILL.md           # 技能定义
    └── references/        # 可选的参考文档
        ├── guide.md
        └── examples.md
```

### Operator Skill

```
evomaster/skills/
└── my_operator_skill/
    ├── SKILL.md           # 技能定义
    ├── scripts/           # 可执行脚本
    │   ├── main.py
    │   └── helper.sh
    └── references/        # 可选的参考文档
        └── api.md
```

## 使用示例

### 在 Playground 中加载 Skills

```yaml
# config.yaml
skills:
  enabled: true
  skills_root: "evomaster/skills"
```

```python
from evomaster.skills import SkillRegistry
from pathlib import Path

# 加载技能
registry = SkillRegistry(Path("evomaster/skills"))

# 获取所有技能
all_skills = registry.get_all_skills()

# 获取 Agent 上下文的 meta_info
context = registry.get_meta_info_context()

# 搜索技能
results = registry.search_skills("rag")
```

### 通过 SkillTool 使用 Skills

Agent 可以通过 `use_skill` 工具使用技能：

```python
# 获取技能信息
{"action": "get_info", "skill_name": "rag"}

# 获取参考文档
{"action": "get_reference", "skill_name": "rag", "reference_name": "api.md"}

# 运行脚本（仅 Operator）
{"action": "run_script", "skill_name": "rag", "script_name": "search.py", "script_args": "--query 'search term'"}
```

### 创建新技能

1. 创建技能目录：
```bash
mkdir -p evomaster/skills/knowledge/my_skill
```

2. 创建 SKILL.md：
```markdown
---
name: my-skill
description: 一个帮助完成 XYZ 任务的技能。当需要做 ABC 时使用。
skill_type: knowledge
---

# 我的技能

## 概述

此技能提供关于 XYZ 的知识...

## 使用场景

- 当需要理解 ABC 时
- 当处理 DEF 概念时

## 详情

详细信息在这里...
```

3. 添加参考文档（可选）：
```bash
mkdir -p evomaster/skills/knowledge/my_skill/references
echo "# 参考文档" > evomaster/skills/knowledge/my_skill/references/guide.md
```

## 相关文档

- [架构概述](./architecture.md)
- [Tools 模块](./tools.md)
- [Agent 模块](./agent.md)
