# Skill Prompt Expansion Design

## Summary

Refactor the matmaster skill system from a tool-dispatch model (LLM calls `use_skill` with
`get_info`/`get_reference`/`run_script` actions) to a prompt-expansion model (LLM calls
`use_skill(skill_name)` and the skill's SKILL.md body is injected directly into the
conversation as instructions).

Two changes in one PR:

1. **Structured frontmatter** -- promote `skill_type`, `mcp_server`, `depends_on` from
   the catch-all `extras` dict to first-class `SkillMetaInfo` fields with Pydantic
   validation.
2. **Prompt expansion** -- simplify `use_skill` to a single-entry tool that returns the
   expanded skill content, removing the three-action dispatch layer.

## Motivation

Current flow requires two round-trips:

```
LLM -> use_skill(get_info) -> read docs -> LLM understands -> LLM calls tools
```

Prompt expansion reduces this to one:

```
LLM -> use_skill(name) -> skill content injected -> LLM follows instructions
```

This aligns with the claude-code skill architecture where skills are prompt injections,
not tool endpoints.

## Design

### 1. Structured Frontmatter

#### SkillMetaInfo changes (`matmaster/skills/registry.py`)

```python
class SkillMetaInfo(BaseModel):
    name: str = Field(description="Skill name (kebab-case)")
    description: str = Field(description="Short description")
    skill_type: Literal["operator", "mcp-loader"] | None = Field(
        default=None, description="Skill category"
    )
    mcp_server: str | None = Field(
        default=None, description="MCP server name for lazy injection"
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Dependent skill names (parsed from comma-separated string)",
    )
    extras: dict[str, Any] = Field(
        default_factory=dict, description="Remaining unknown frontmatter fields"
    )
```

#### Parsing changes (`Skill._parse_meta_info`)

Known keys expand from `{"name", "description"}` to include `skill_type`, `mcp_server`,
`depends_on`. These are extracted into their typed fields. `depends_on` is parsed from a
comma-separated string (`"a, b"` -> `["a", "b"]`). Remaining unknown keys still go to
`extras`.

#### Existing SKILL.md files

No changes needed. The frontmatter format is unchanged -- only the parsing side changes.

### 2. Prompt Expansion

#### use_skill schema (`matmaster/tools/skill_tool.py`)

Old schema (6 parameters, 2 required):

```python
{
    'skill_name': str,        # required
    'action': enum,           # required: get_info | get_reference | run_script
    'reference_name': str,
    'script_name': str,
    'script_args': str,
    'script_timeout': int,
}
```

New schema (2 parameters, 1 required):

```python
{
    'skill_name': str,   # required -- kebab-case skill name
    'args': str,         # optional -- arguments passed to the skill
}
```

#### SkillTool.execute() new logic

```python
async def execute(self, arguments: dict[str, Any]) -> str:
    skill_name = arguments['skill_name']
    skill = self._registry.get_skill(skill_name)
    if skill is None:
        return f"Error: Skill '{skill_name}' not found"

    # 1. Get body
    body = skill.get_full_info()

    # 2. Prepend base directory header
    skill_dir = str(skill.skill_path.resolve())
    header = f"Base directory for this skill: {skill_dir}"

    # 3. ${SKILL_DIR} substitution
    body = body.replace("${SKILL_DIR}", skill_dir)

    # 4. Trigger on_skill_hit (MCP lazy injection) -- unchanged
    if skill.meta_info.mcp_server and self._on_skill_hit:
        self._on_skill_hit(skill.meta_info.mcp_server)
    for dep_name in skill.meta_info.depends_on:
        dep_skill = self._registry.get_skill(dep_name)
        if dep_skill and dep_skill.meta_info.mcp_server and self._on_skill_hit:
            self._on_skill_hit(dep_skill.meta_info.mcp_server)

    # 5. Return expanded content
    return f"{header}\n\n{body}"
```

#### Removed code

- `_get_info()`, `_get_reference()`, `_run_script()` methods -- deleted
- `_build_command()`, `_find_project_root()` helpers -- deleted
- `_get_co_template_hint()` -- deleted
- `script_env.inject()` import -- removed from SkillTool (stays in codebase for
  future BashTool integration)

#### SkillTool constructor

```python
def __init__(
    self,
    skill_registry: SkillRegistry,
    session: Any,  # retained for backward compat, unused after run_script removal
    on_skill_hit: Callable[[str], None] | None = None,
) -> None:
```

`session` parameter is kept for now to avoid changing the call site in `Exp._init_skill_tools`.
Can be removed in a follow-up cleanup.

### 3. Unchanged Components

- **ContextBuilder** (`context_builder.py`): No changes. `get_meta_info_context()` output
  format unchanged.
- **Exp._init_skill_tools** (`exp.py`): `on_skill_hit` callback logic unchanged.
  SkillTool construction unchanged.
- **SkillRegistry** (`registry.py`): Discovery, filtering, override logic unchanged.
- **script_env.py**: Untouched. Will be integrated into BashTool/session layer in a
  separate PR.
- **SKILL.md files**: No content changes required. LLM interprets existing instructions
  with the base directory context.

### 4. Test Impact

| Test file | Change |
|-----------|--------|
| `tests/test_skill_registry.py` | Update `extras.get('mcp_server')` assertions to `meta_info.mcp_server` |
| `tests/test_skill_tool.py` | Rewrite: test single-entry expansion, `${SKILL_DIR}` substitution, base directory header, on_skill_hit triggering |
| `tests/matmaster/tools/test_skill_meta_extras.py` | Update: `mcp_server` no longer in extras |
| `tests/matmaster/tools/test_skill_tool_callback.py` | Minor: `on_skill_hit` trigger path uses `meta_info.mcp_server` instead of `extras.get()` |

## Files Modified

| File | Nature of change |
|------|-----------------|
| `matmaster/skills/registry.py` | SkillMetaInfo fields + `_parse_meta_info` parsing |
| `matmaster/tools/skill_tool.py` | Rewrite execute logic, remove 3-action dispatch |
| `tests/test_skill_registry.py` | Adapt assertions |
| `tests/test_skill_tool.py` | Rewrite |
| `tests/matmaster/tools/test_skill_meta_extras.py` | Adapt assertions |
| `tests/matmaster/tools/test_skill_tool_callback.py` | Minor adaptation |

## Out of Scope

- Credential injection migration to BashTool/session layer (separate PR)
- Conditional activation via `paths` field (P1)
- Fork execution context (P2)
- Context budget management for skill listing (P1)
- Namespace system (P3)
