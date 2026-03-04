# Skills Module

The Skills module provides the skill system for EvoMaster (aligned with upstream v0.0.2: single **Skill** type, no Knowledge/Operator split).

## Overview

```
evomaster/skills/
├── base.py           # BaseSkill, Skill, SkillRegistry
├── rag/              # RAG skill
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── database.py
│   │   ├── encode.py
│   │   └── search.py
│   └── references/
├── pdf/
│   └── ...
└── {skill_name}/     # Each subdir with SKILL.md is loaded as one Skill
    ├── SKILL.md
    ├── scripts/      # Optional; if present, scripts are exposed for run_script
    └── references/
```

Skills are loaded from **subdirectories of `skills_root`** that contain a `SKILL.md` file. Each is instantiated as a **Skill** (same type for all).

## SkillMetaInfo

Metadata parsed from SKILL.md frontmatter. No `skill_type` field (aligned with upstream).

```python
class SkillMetaInfo(BaseModel):
    name: str = Field(description="Skill name")
    description: str = Field(description="Skill description with usage scenarios")
    license: str | None = Field(default=None, description="License info")
```

## BaseSkill

Abstract base class for skills.

```python
class BaseSkill(ABC):
    """Skill base class

    - Level 1 (meta_info): ~100 tokens, always in context
    - Level 2 (full_info): 500-2000 tokens, loaded on demand
    - Level 3 (scripts): Optional; only Skill has scripts
    """

    def __init__(self, skill_path: Path): ...
    def get_full_info(self) -> str: ...
    def get_reference(self, reference_name: str) -> str: ...
    @abstractmethod
    def to_context_string(self) -> str: ...
```

## Skill

Single concrete skill type (aligned with upstream v0.0.2). All loaded skills are **Skill** instances.

- **Level 1 (meta_info)**: Always in context
- **Level 2 (full_info)**: Loaded on demand (from job_submit.md or SKILL.md body)
- **Level 3 (scripts)**: Optional `scripts/` directory; if present, scripts are listed and runnable via `use_skill` action `run_script`

```python
class Skill(BaseSkill):
    def __init__(self, skill_path: Path):
        super().__init__(skill_path)
        self.scripts_dir = self.skill_path / "scripts"
        self.available_scripts = self._scan_scripts()

    def get_script_path(self, script_name: str) -> Path | None: ...
    def to_context_string(self) -> str:
        # Returns e.g. "[Skill: rag] ... (Scripts: encode.py, search.py)"
```

## SkillRegistry

Loads all skills from `skills_root` subdirs that have SKILL.md; supports name filter and `create_subset`.

```python
class SkillRegistry:
    def __init__(self, skills_root: Path, skills: list[str] | None = None, *, _initial_skills: dict | None = None):
        """skills_root: root dir; each subdir with SKILL.md becomes one Skill. skills: optional name filter."""

    def get_skill(self, name: str) -> BaseSkill | None: ...
    def get_all_skills(self) -> list[BaseSkill]: ...
    def create_subset(self, skill_names: list[str]) -> SkillRegistry: ...
    def get_meta_info_context(self) -> str:
        """All skills' meta_info for Agent context (single section, no Knowledge/Operator split)."""
    def search_skills(self, query: str) -> list[BaseSkill]: ...
```

## SKILL.md Format

### Frontmatter (YAML)

```yaml
---
name: skill-name
description: Brief description with usage scenarios and trigger conditions
skill_type: knowledge  # or operator
license: MIT
---
```

### Body (Markdown)

The body contains the full_info (Level 2):

```markdown
# Skill Name

## Overview

Detailed description of what this skill does.

## Usage

When to use this skill:
- Scenario 1
- Scenario 2

## Details

Technical details, parameters, examples, etc.

## References

- [Reference 1](./references/ref1.md)
- [Reference 2](./references/ref2.md)
```

## Directory Structure

### Knowledge Skill

```
evomaster/skills/knowledge/
└── my_knowledge_skill/
    ├── SKILL.md           # Skill definition
    └── references/        # Optional reference docs
        ├── guide.md
        └── examples.md
```

### Operator Skill

```
evomaster/skills/
└── my_operator_skill/
    ├── SKILL.md           # Skill definition
    ├── scripts/           # Executable scripts
    │   ├── main.py
    │   └── helper.sh
    └── references/        # Optional reference docs
        └── api.md
```

## Usage Examples

### Loading Skills in Playground

```yaml
# config.yaml
skills:
  enabled: true
  skills_root: "evomaster/skills"
```

```python
from evomaster.skills import SkillRegistry
from pathlib import Path

# Load skills
registry = SkillRegistry(Path("evomaster/skills"))

# Get all skills
all_skills = registry.get_all_skills()

# Get meta_info for Agent context
context = registry.get_meta_info_context()

# Search skills
results = registry.search_skills("rag")
```

### Using Skills via SkillTool

Agent can use skills through the `use_skill` tool:

```python
# Get skill info
{"action": "get_info", "skill_name": "rag"}

# Get reference doc
{"action": "get_reference", "skill_name": "rag", "reference_name": "api.md"}

# Run script (Operator only)
{"action": "run_script", "skill_name": "rag", "script_name": "search.py", "script_args": "--query 'search term'"}
```

### Creating a New Skill

1. Create skill directory:
```bash
mkdir -p evomaster/skills/knowledge/my_skill
```

2. Create SKILL.md:
```markdown
---
name: my-skill
description: A skill that helps with XYZ tasks. Use when you need to do ABC.
skill_type: knowledge
---

# My Skill

## Overview

This skill provides knowledge about XYZ...

## When to Use

- When you need to understand ABC
- When working with DEF concepts

## Details

Detailed information here...
```

3. Add references (optional):
```bash
mkdir -p evomaster/skills/knowledge/my_skill/references
echo "# Reference Doc" > evomaster/skills/knowledge/my_skill/references/guide.md
```

## Related Documentation

- [Architecture Overview](./architecture.md)
- [Tools Module](./tools.md)
- [Agent Module](./agent.md)
