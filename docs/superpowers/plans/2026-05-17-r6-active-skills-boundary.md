# R6 Active Skills Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `SkillRegistry` 从 `matmaster/context/*` 的接口中彻底剥离。引入 matmaster-owned 的 `ActiveSkill` DTO 和 `SkillResolver` callable 抽象，让 `SessionSkillsSource` 与 `SessionToolsSource` 共用同一份 active skill 数据形态。service 层只构造一次 `SkillRegistry`，封装在 resolver 内注入，prompt 渲染路径与 runtime 工具注册路径职责彻底分开。

**Architecture:** matmaster 不再消费 `SkillRegistry`，改为接收 `tuple[ActiveSkill, ...]`。service 层提供 `SkillRegistryResolver`（接收 typed events，返回 ActiveSkill 元组），封装 registry 解析、disabled 规则、local/remote skill root 处理。`SessionContextBuilder` 持有 `active_skills` 字段（而非 `skill_registry`）；`SessionSkillsSource` 改为 `from_skills` 与 `SessionToolsSource` 对称。

**Tech Stack:** Python 3.11+ via `uv run`, dataclasses, Protocol/Callable type aliases, pytest, existing MatMaster context assembly ports.

**Prerequisite:** R7 (SessionEvent decoding unification) **必须先完成**。本 plan 假设 `src/services/session_event_codec.py` 已就位且 `matmaster/context/scanner.py` 只接收 typed events。

---

## Scope And Non-Negotiables

- 本计划只做 R6 active skills 边界收紧。不要顺手处理 R1、E3、E6 等其它 deferred simplification。
- 只在全部任务完成、验证通过后发起一个最终 PR。任务内可以分阶段 commit/checkpoint，但 PR 不是阶段边界。
- `matmaster/` 不得依赖 `src/`。`matmaster/context/*` 任何文件不应出现 `SkillRegistry` 类型或 `skill_registry` 参数名，**`matmaster/context/system_prompt.py` 的 registry-wide prompt prefix 路径除外**（见下一条与 Out Of Scope）。boundary test 与 final grep 都需把 `system_prompt.py` 显式列入白名单。
- `SystemPromptBuilder._build_skills(skill_registry)` 的 registry 全集渲染路径**不在本 plan scope**——那是 prompt prefix（"可用 skill 目录"），与本 plan 的 active skill rehydrate（"本会话已加载"）是不同语义，作为 follow-up 处理。
- core 层 `Exp._init_skill_tools` 内部仍然构造一份 `SkillRegistry`（用于 SkillTool 注册到 ToolCatalog）。这个 registry 与 service 层 resolver 内部的 registry 是**两套独立实例**，职责不同，本 plan 不做合并。
- **`SkillResolver` 不通过 `run_meta` 传递**。`run_meta` 只承载被动数据（如 `active_skills: frozenset[str]`，见 [`exp.py:906`](matmaster/core/exp.py:906)）；`SkillResolver` 闭包持有 service 层构造的 `SkillRegistry`，是服务能力对象，必须走**显式参数链路**：`AgentRunService.run_agent` → `Exp.run_stream(skill_resolver=...)` → `Exp.build_runtime(skill_resolver=...)` → `build_runtime_context_assembly(skill_resolver=...)`。Exp 实例可缓存 `self._skill_resolver`（与现有 `self._skill_registry` 字段平行）。
- service 层 `_active_skills` hot cache 形态保持 `dict[str, frozenset[str]]`，**仅服务 LazyMCP replay**（[`exp.py:906-910`](matmaster/core/exp.py:906) 读 `run_meta["active_skills"]` 时期望 iterable of str）。Prompt/context 渲染**永远通过 `SkillResolver(events)` 现算**，不复用 hot cache。两条路径数据形态不同：前者只要名字，后者要完整 DTO。
- `build_skill_registry()` 保持工厂无状态——不接 `enabled` 参数。**caller 在调用前 guard**：`if exp_config.skills.enabled: registry = build_skill_registry(...) else: registry = None`。`SkillRegistryResolver(None)` 已对 None 安全，返回空 tuple。
- `SkillRegistryResolver.__call__` 必须保留 `resolve_active_skills` 当前 [`skills.py:27-30`](matmaster/context/sources/skills.py:27) 的吞异常语义：`registry.get_skill(name)` 抛异常时 `continue`，不让 active skill rehydrate 影响主流程（与 [`exp.py:913-919`](matmaster/core/exp.py:913) LazyMCP replay 的异常处理风格一致）。
- **`empty_skill_resolver` 兜底必须存在**。`Exp.run_stream()` / `Exp.build_runtime()` 的 `skill_resolver: SkillResolver | None = None` 默认值在 `Exp.build_runtime` 入口统一归一化为 `self._skill_resolver = skill_resolver or empty_skill_resolver`。`build_runtime_context_assembly` 与 `build_session_context_factory` 内部签名保持 non-optional `SkillResolver`——把 None-check 集中在唯一一个边界点，下游不再做。`empty_skill_resolver` 定义在 `matmaster/core/runtime_context_assembly.py` 模块级。devshell / 旧测试 `exp.run_stream(ctx, "task")` 不传 resolver 时，必须能完整跑通一次 session assembly 不抛异常。
- **subagent 路径必须显式传递 resolver**。`_make_spawn_fn` 当前 [`exp.py:211`](matmaster/core/exp.py:211) 创建闭包，闭包内 [`exp.py:232`](matmaster/core/exp.py:232) 实例化 `child_exp = Exp(child_config, allow_spawn=False)` 并在 [`exp.py:271`](matmaster/core/exp.py:271) 调 `child_exp.run_stream(ctx, task, cancel_token=..., spawn_id=...)`，**没有 resolver 参数**。如果只改 root path，child Exp 会拿默认 `None` → empty_skill_resolver 兜底虽然保命，但 child 渲染就**永远是空 active skills**，与 parent 不一致。修复要求 `_make_spawn_fn` 显式接收 `skill_resolver: SkillResolver` 并在 `child_exp.run_stream(..., skill_resolver=skill_resolver)` 透传。
- 任何被删除的公共函数（`resolve_active_skills`、`skill_name`、`SessionSkillsSource.from_events`）必须确认没有第三方代码引用（仅 service 层 + tests）后再删。

## Architecture Decisions Locked

| 决策 | 选择 | 理由 |
|---|---|---|
| matmaster ↔ service 边界形态 | `Callable[[events], tuple[ActiveSkill, ...]]`（SkillResolver type alias），非 `Protocol` | resolver 是纯同步函数式 transform，无 IO（registry 已在 service 层启动时构造），Callable 比 Protocol 更轻 |
| ActiveSkill DTO 形态 | 扁平字段 `(name, description, mcp_server)`，无 `meta_info` 嵌套 | 与 ports.py 现有 DTO（`SessionEvent`、`UserInstructions`）命名规范一致；避免把 `Skill.meta_info` 形状凝固成跨层契约 |
| disabled 规则归属 | service 层 `SkillRegistryResolver` 内部决定（service rehydrate 出的 active skills 自动排除 disabled） | service 层视图 ⊆ core 层视图：prompt 显示的 skill 必然是 runtime 可调用的；消除文档诊断的 Bug B |
| `local_user_skills_root` 归属 | resolver 内部读 session（与 core 层 `_init_skill_tools` 行为一致） | service 层 registry 看到 session 私有 skill root，消除 Bug A |
| `scan_skill_hits` 位置 | 保留在 `matmaster/context/scanner.py`，service 层调用 | scanner 是对 typed events 的纯函数操作，与 `SessionEvent` 类型紧耦合，留在 matmaster 符合"操作和类型放一起"；service → matmaster 调用合规 |
| service hot cache 语义 | 仅缓存 `frozenset[str]`（skill names），按 session_id；resolver 输出不缓存 | LazyMCP replay 期望 iterable of str（[`exp.py:906-910`](matmaster/core/exp.py:906)）；prompt 渲染每次现算，避免 DTO 缓存与替换语义冲突 |
| resolver 异常语义 | `get_skill()` 抛异常时 `continue`，不上抛 | 与现有 [`skills.py:27-30`](matmaster/context/sources/skills.py:27) 与 [`exp.py:913-919`](matmaster/core/exp.py:913) 风格一致；active skill rehydrate 失败不影响主流程 |
| resolver 注入方式 | 显式参数链路（`Exp.build_runtime(skill_resolver=...)`），非 `run_meta` | `run_meta` 是被动数据；resolver 是服务能力对象，闭包持有 `SkillRegistry` |
| `until_event_id` 防御 | `ContextAssembler._build_via_factory` 在调用 factory 前先按 `until_event_id` 裁剪 events | 把 active_skills 的时间边界变成 ContextAssembler 的不变量，与 SessionContextBuilder 当前的 attachments 二次裁剪对称，防 fake port 返回未裁剪 events 导致未来 skill 泄漏 |
| optional resolver 归一化 | `Exp.build_runtime` 内 `self._skill_resolver = skill_resolver or empty_skill_resolver`，下游 signature 保持 non-optional | None-check 集中在唯一一个边界点，避免 `None(events)` 散落到 `build_session_context_factory` / `_build_via_factory` / `build_runtime_context_assembly` 任一处 |
| subagent resolver 传递 | `_make_spawn_fn(skill_resolver=...)` 闭包捕获 parent resolver，`child_exp.run_stream(skill_resolver=...)` 透传 | child Exp 直接接 `Exp(child_config, allow_spawn=False).run_stream(ctx, ...)`，不走 `AgentRunService.run_agent`，必须独立链路 |

## File Structure

- Modify: `matmaster/context/ports.py`
  新增 `ActiveSkill` dataclass 和 `SkillResolver` type alias。

- Modify: `matmaster/context/sources/tools.py`
  字段访问扁平化（`skill.mcp_server` 替代 `getattr(skill.meta_info, "mcp_server", ...)`），删除 `_skill_mcp_server` helper，类型注解 `Iterable[Any]` → `Iterable[ActiveSkill]`。

- Modify: `matmaster/context/sources/skills.py`
  删除 `skill_name()` / `resolve_active_skills()` / `SessionSkillsSource.from_events()`，新增 `SessionSkillsSource.from_skills()` 与 tools 对称，`format_loaded_skills` 改为扁平字段访问。

- Modify: `matmaster/context/session.py`
  `SessionContextBuilder` 字段 `skill_registry: Any` → `active_skills: tuple[ActiveSkill, ...]`，`build_sections` 内部不再调 `from_events`。

- Modify: `matmaster/core/runtime_context_assembly.py`
  `build_session_context_factory` 参数 `skill_registry: Any | None` → `skill_resolver: SkillResolver`，factory closure 内部调 resolver 计算 active_skills。
  `build_runtime_context_assembly` 参数 `skill_registry: Any` → `skill_resolver: SkillResolver`。

- Create: `src/services/skill_resolver.py`
  新建 `SkillRegistryResolver` class（实现 `SkillResolver` 协议），封装 events scan + registry lookup + ActiveSkill 构造。

- Create: `src/services/skill_registry_factory.py`
  新建 `build_skill_registry(...)` 工厂函数，集中处理 roots 解析、`local_user_skills_root` 追加、`remote_roots` 提取、`disabled` 规则应用。`SkillRegistryResolver` 与外部测试都用它构造 registry。

- Modify: `src/services/context_assembly_factory.py`
  `build_context_assembler` 参数 `skill_registry: Any | None` → `skill_resolver: SkillResolver`。

- Modify: `src/services/agent_run_service.py`
  删除 `_build_skill_registry` / `_remote_skill_roots_from_session`。改写 `_resolve_active_skill_names` 直接从 events 算 skill name set（不再需要 registry）；保留 hot cache。`run_agent` 入口构造一次 `SkillRegistryResolver`，传给 `build_context_assembler` 和 active skill rehydrate。

- Modify: `matmaster/core/exp.py`
  `_init_skill_tools` 与 `build_runtime_context_assembly` 调用链改为接收 resolver 而非 registry。注意：core 层 `_init_skill_tools` 内部 SkillRegistry 实例不删（用于 SkillTool 注册）。

- Modify: `matmaster/core/playground.py`
  如果 `run_meta["skill_registry"]` 有传递，改为传 resolver；review 调用方。

- Modify tests:
  - Modify: `tests/matmaster/context/test_session.py`
  - Modify: `tests/matmaster/context/sources/test_skills.py`
  - Modify: `tests/matmaster/context/sources/test_tools.py`
  - Modify: `tests/matmaster/context/test_phase4_static_boundaries.py`
  - Modify: `tests/matmaster/services/test_active_mcp_replay.py`
  - Modify: `tests/matmaster/services/test_lazy_mcp_replay.py`
  - Modify: `tests/services/test_context_assembly_factory.py`
  - Modify: `tests/matmaster/core/test_exp_runtime_v2.py`
  - Create: `tests/matmaster/services/test_skill_registry_factory.py`
  - Create: `tests/matmaster/services/test_skill_resolver.py`
  - Modify every fixture found by `rg -n "skill_registry=" tests`

---

### Task 1: Add `ActiveSkill` DTO And `SkillResolver` Type Alias

**Files:**
- Modify: `matmaster/context/ports.py`
- Test: `tests/matmaster/context/test_ports.py`

- [ ] **Step 1: Add failing test for ActiveSkill**

Append to `tests/matmaster/context/test_ports.py`:

```python
from matmaster.context.ports import ActiveSkill


def test_active_skill_defaults_to_empty_description_and_no_mcp_server() -> None:
    skill = ActiveSkill(name="pxrd")
    assert skill.name == "pxrd"
    assert skill.description == ""
    assert skill.mcp_server is None


def test_active_skill_is_frozen_and_hashable() -> None:
    skill = ActiveSkill(name="pxrd", description="x-ray", mcp_server="xrd_server")
    assert hash(skill) == hash(
        ActiveSkill(name="pxrd", description="x-ray", mcp_server="xrd_server")
    )
```

Run:

```bash
uv run pytest tests/matmaster/context/test_ports.py -q
```

Expected: FAIL with `ImportError: cannot import name 'ActiveSkill'`.

- [ ] **Step 2: Add DTO and type alias**

Modify `matmaster/context/ports.py`:

```python
from collections.abc import Callable

# 既有 imports + SessionEvent / SessionEventQuery 等保持不变

@dataclass(frozen=True)
class ActiveSkill:
    """matmaster-owned DTO for prompt-side skill rendering.

    Service layer is responsible for resolving skill_hit events into this
    structure (registry lookup + disabled rules + local/remote roots).
    matmaster/context/* never sees SkillRegistry.
    """

    name: str
    description: str = ""
    mcp_server: str | None = None


SkillResolver: TypeAlias = Callable[
    [tuple["SessionEvent", ...]], tuple[ActiveSkill, ...]
]
```

补 `TypeAlias` import 与 `Callable` import。

- [ ] **Step 3: Run port tests**

```bash
uv run pytest tests/matmaster/context/test_ports.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit checkpoint**

```bash
git add matmaster/context/ports.py tests/matmaster/context/test_ports.py
git commit -m "refactor(context): add active skill DTO and resolver alias"
```

---

### Task 2: Flatten `SessionToolsSource` Field Access To `ActiveSkill`

**Files:**
- Modify: `matmaster/context/sources/tools.py`
- Test: `tests/matmaster/context/sources/test_tools.py`

- [ ] **Step 1: Update tests to construct ActiveSkill directly**

In `tests/matmaster/context/sources/test_tools.py`, replace all ad-hoc skill mocks (current tests likely use `SimpleNamespace` or `Skill` instances with `meta_info`) with `ActiveSkill` fixtures:

```python
from matmaster.context.ports import ActiveSkill
from matmaster.context.sources.tools import (
    SessionToolsSource,
    format_active_mcp,
    resolve_declared_servers,
)


def _make_skill(name: str, mcp_server: str | None = None) -> ActiveSkill:
    return ActiveSkill(name=name, description="", mcp_server=mcp_server)


def test_resolve_declared_servers_collects_unique_non_empty_servers() -> None:
    skills = (
        _make_skill("a", "srv1"),
        _make_skill("b", "srv1"),
        _make_skill("c", None),
        _make_skill("d", "srv2"),
    )
    assert resolve_declared_servers(skills) == {"srv1", "srv2"}


def test_format_active_mcp_marks_servers_without_schemas_as_unavailable() -> None:
    skills = (_make_skill("a", "srv1"), _make_skill("b", "srv2"))
    rendered = format_active_mcp(
        skills,
        legal_servers={"srv1", "srv2"},
        schemas_by_server={"srv1": [{"name": "tool_x"}], "srv2": []},
    )
    assert "srv1: available" in rendered
    assert "srv2: unavailable" in rendered
    assert "srv1_tool_x" in rendered
```

- [ ] **Step 2: Run tests to verify fail before refactor**

```bash
uv run pytest tests/matmaster/context/sources/test_tools.py -q
```

Expected: FAIL because tests import `ActiveSkill` but `tools.py` still uses generic `Any`.

- [ ] **Step 3: Flatten field access and tighten types**

Modify `matmaster/context/sources/tools.py`:

- Delete `_skill_mcp_server` helper.
- Change `Iterable[Any]` annotations to `Iterable[ActiveSkill]`.
- Replace `getattr(skill.meta_info, "mcp_server", None)` with `skill.mcp_server`.

The file becomes:

```python
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import ActiveSkill
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


def resolve_declared_servers(skills: Iterable[ActiveSkill]) -> set[str]:
    return {skill.mcp_server for skill in skills if skill.mcp_server}


def resolve_runnable_servers(
    skills: Iterable[ActiveSkill],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> set[str]:
    declared = resolve_declared_servers(skills)
    runnable = set(declared)
    if legal_servers is not None:
        runnable &= set(legal_servers)
    if schemas_by_server is not None:
        runnable = {
            server
            for server in runnable
            if isinstance((schemas := schemas_by_server.get(server)), list) and schemas
        }
    return runnable


def format_active_mcp(
    skills: Iterable[ActiveSkill],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> str:
    declared = sorted(resolve_declared_servers(skills))
    if not declared:
        return ""
    runnable = resolve_runnable_servers(
        skills,
        legal_servers=legal_servers,
        schemas_by_server=schemas_by_server,
    )
    lines = ["[Active MCP servers]"]
    for server in declared:
        if server not in runnable:
            lines.append(f"- {server}: unavailable")
            continue
        schemas = (schemas_by_server or {}).get(server) or []
        lines.append(f"- {server}: available")
        for schema in schemas:
            name = schema.get("name") if isinstance(schema, Mapping) else None
            if isinstance(name, str) and name:
                lines.append(f"  - {server}_{name}")
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionToolsSource:
    skills: tuple[ActiveSkill, ...] = ()
    legal_servers: frozenset[str] | None = None
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None

    @classmethod
    def from_skills(
        cls,
        skills: Iterable[ActiveSkill],
        *,
        legal_servers: set[str] | None,
        schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
    ) -> SessionToolsSource:
        return cls(
            skills=tuple(skills),
            legal_servers=(
                frozenset(legal_servers) if legal_servers is not None else None
            ),
            schemas_by_server=schemas_by_server,
        )

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = format_active_mcp(
            self.skills,
            legal_servers=(
                set(self.legal_servers) if self.legal_servers is not None else None
            ),
            schemas_by_server=self.schemas_by_server,
        )
        if not text:
            return ()
        return (
            ContextSection(
                key="session_tools",
                tag="active_tools",
                content=text,
                order=SectionOrder.SESSION_TOOLS,
                views=ALL_VIEWS,
            ),
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/matmaster/context/sources/test_tools.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

```bash
git add matmaster/context/sources/tools.py tests/matmaster/context/sources/test_tools.py
git commit -m "refactor(context): adopt active skill dto in session tools source"
```

---

### Task 3: Rewrite `SessionSkillsSource` To Consume `ActiveSkill` Directly

**Files:**
- Modify: `matmaster/context/sources/skills.py`
- Test: `tests/matmaster/context/sources/test_skills.py`

- [ ] **Step 1: Update tests to drop registry-driven fixtures**

In `tests/matmaster/context/sources/test_skills.py`, replace any `from_events(events, skill_registry=...)` setup with `from_skills(...)`:

```python
from matmaster.context.ports import ActiveSkill
from matmaster.context.sources.skills import (
    SessionSkillsSource,
    format_loaded_skills,
)


def test_format_loaded_skills_omits_section_when_empty() -> None:
    assert format_loaded_skills(()) == ""


def test_format_loaded_skills_renders_name_description_and_mcp_server() -> None:
    skills = (
        ActiveSkill(name="pxrd", description="X-ray powder", mcp_server="xrd_srv"),
        ActiveSkill(name="mlip", description=""),
    )
    rendered = format_loaded_skills(skills)
    assert "[Loaded skills]" in rendered
    assert "- pxrd: X-ray powder (mcp_server=xrd_srv)" in rendered
    assert "- mlip" in rendered
    assert "mcp_server=" not in rendered.splitlines()[-1]


def test_session_skills_source_from_skills_round_trips() -> None:
    skills = (ActiveSkill(name="pxrd"),)
    source = SessionSkillsSource.from_skills(skills)
    sections = source.to_sections()
    assert len(sections) == 1
    assert sections[0].tag == "loaded_skills"
```

Also delete tests that exercise `resolve_active_skills` or `skill_name` helpers — those functions are being removed.

- [ ] **Step 2: Run tests to verify fail**

```bash
uv run pytest tests/matmaster/context/sources/test_skills.py -q
```

Expected: FAIL — `from_skills` and updated rendering not yet implemented.

- [ ] **Step 3: Rewrite source module**

Replace the body of `matmaster/context/sources/skills.py` with:

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from matmaster.context.ports import ActiveSkill
from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


def format_loaded_skills(skills: Iterable[ActiveSkill]) -> str:
    skill_tuple = tuple(skills)
    if not skill_tuple:
        return ""
    lines = ["[Loaded skills]"]
    for skill in skill_tuple:
        suffix = f" (mcp_server={skill.mcp_server})" if skill.mcp_server else ""
        if skill.description:
            lines.append(f"- {skill.name}: {skill.description}{suffix}")
        else:
            lines.append(f"- {skill.name}{suffix}")
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionSkillsSource:
    skills: tuple[ActiveSkill, ...] = ()

    @classmethod
    def from_skills(cls, skills: Iterable[ActiveSkill]) -> SessionSkillsSource:
        return cls(skills=tuple(skills))

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = format_loaded_skills(self.skills)
        if not text:
            return ()
        return (
            ContextSection(
                key="session_skills",
                tag="loaded_skills",
                content=text,
                order=SectionOrder.SESSION_SKILLS,
                views=ALL_VIEWS,
            ),
        )
```

Removed exports: `skill_name`, `resolve_active_skills`, `SessionSkillsSource.from_events`. **不要**保留向后兼容 shim——直接删，让 import 错误暴露所有调用点。

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/matmaster/context/sources/test_skills.py -q
```

Expected: PASS.

- [ ] **Step 5: Find remaining call sites and stage their failures**

```bash
rg -n "resolve_active_skills|from_events|\\bskill_name\\b" matmaster src tests | rg -v "tests/matmaster/services/test_skill_resolver\\.py"
```

Expected matches will be in `src/services/agent_run_service.py` and `matmaster/context/session.py`. They will be fixed in Tasks 4 / 8.

- [ ] **Step 6: Commit checkpoint**

```bash
git add matmaster/context/sources/skills.py tests/matmaster/context/sources/test_skills.py
git commit -m "refactor(context): rewrite session skills source for active skill dto"
```

---

### Task 4: Replace `skill_registry` With `active_skills` In `SessionContextBuilder`

**Files:**
- Modify: `matmaster/context/session.py`
- Test: `tests/matmaster/context/test_session.py`

- [ ] **Step 1: Update tests**

In `tests/matmaster/context/test_session.py`:

```python
from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.context.session import SessionContextBuilder


def test_session_context_builder_renders_skills_section() -> None:
    builder = SessionContextBuilder(
        events=(),
        active_skills=(ActiveSkill(name="pxrd"),),
        legal_mcp_servers=None,
        schemas_by_server=None,
    )
    sections = builder.build_sections(until_event_id=None, include_attachments=False)
    assert any(section.tag == "loaded_skills" for section in sections)


def test_session_context_builder_attachments_still_use_events() -> None:
    # Verify events field is preserved for SessionAttachmentsSource.
    builder = SessionContextBuilder(
        events=(SessionEvent(id=1, event_type="query", source="User", content={}),),
        active_skills=(),
    )
    sections = builder.build_sections(until_event_id=10, include_attachments=True)
    # Existing attachment behavior unchanged — just confirms no regression.
    assert isinstance(sections, tuple)
```

Remove any test that constructs `SessionContextBuilder(skill_registry=...)`.

- [ ] **Step 2: Run tests to verify fail**

```bash
uv run pytest tests/matmaster/context/test_session.py -q
```

Expected: FAIL — `active_skills` field not yet exists.

- [ ] **Step 3: Rewrite builder**

Modify `matmaster/context/session.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.context.sections import ContextSection
from matmaster.context.sources.attachments import SessionAttachmentsSource
from matmaster.context.sources.skills import SessionSkillsSource
from matmaster.context.sources.tools import SessionToolsSource


@dataclass(frozen=True)
class SessionContextBuilder:
    """Compose session-level sections from typed inputs.

    Service layer is responsible for resolving active skills before
    constructing the builder. matmaster/context/* never sees SkillRegistry.
    """

    events: tuple[SessionEvent, ...]
    active_skills: tuple[ActiveSkill, ...] = ()
    legal_mcp_servers: set[str] | None = None
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple):
            raise TypeError(
                "SessionContextBuilder.events must be a tuple of SessionEvent; "
                "service-layer callers should decode raw rows before constructing it"
            )
        if not isinstance(self.active_skills, tuple):
            raise TypeError(
                "SessionContextBuilder.active_skills must be a tuple of ActiveSkill; "
                "service-layer callers should resolve skill_hit events before constructing it"
            )

    def build_sections(
        self,
        *,
        until_event_id: int | None,
        include_attachments: bool,
    ) -> tuple[ContextSection, ...]:
        if until_event_id is not None:
            scoped_events = tuple(
                event for event in self.events if event.id <= until_event_id
            )
        else:
            scoped_events = self.events

        skills_source = SessionSkillsSource.from_skills(self.active_skills)
        tools_source = SessionToolsSource.from_skills(
            self.active_skills,
            legal_servers=self.legal_mcp_servers,
            schemas_by_server=self.schemas_by_server,
        )

        sections: list[ContextSection] = []
        sections.extend(skills_source.to_sections())
        sections.extend(tools_source.to_sections())
        if include_attachments:
            attachments_source = SessionAttachmentsSource.from_events(
                scoped_events,
                until_event_id=until_event_id,
            )
            sections.extend(attachments_source.to_sections())
        sections.sort(key=lambda section: section.order)
        return tuple(sections)
```

Note: `active_skills` 是上游（factory）按 `until_event_id` 已经裁剪过的快照（见 Task 5）。本 builder 不再做 skill 维度的二次过滤。

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/matmaster/context/test_session.py tests/matmaster/context/sources/ -q
```

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

```bash
git add matmaster/context/session.py tests/matmaster/context/test_session.py
git commit -m "refactor(context): replace skill registry with active skills in session builder"
```

---

### Task 5: Update Factory Closure To Accept `SkillResolver` And Enforce `until_event_id` Defense

**Files:**
- Modify: `matmaster/core/runtime_context_assembly.py`
- Modify: `matmaster/context/assembly.py` (二次裁剪不变量)
- Test: `tests/matmaster/context/test_assembly.py` (新增 until_event_id 防御测试)
- Test: `tests/matmaster/core/test_exp_runtime_v2.py` (review)

- [ ] **Step 1: Add a unit test for the new factory signature**

Create or extend a focused test in `tests/matmaster/core/test_runtime_context_assembly.py` (create file if missing):

```python
from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.core.runtime_context_assembly import build_session_context_factory


def test_build_session_context_factory_invokes_resolver_per_call() -> None:
    captured: list[tuple[SessionEvent, ...]] = []

    def resolver(events):
        captured.append(events)
        return (ActiveSkill(name="pxrd"),)

    factory = build_session_context_factory(
        skill_resolver=resolver,
        legal_mcp_servers=None,
        schemas_by_server=None,
    )
    events = (SessionEvent(id=1, event_type="query", source="User", content={}),)
    builder = factory(events)

    assert captured == [events]
    assert builder.active_skills == (ActiveSkill(name="pxrd"),)
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/matmaster/core/test_runtime_context_assembly.py -q
```

Expected: FAIL.

- [ ] **Step 3: Rewrite factory**

Modify `matmaster/core/runtime_context_assembly.py`:

Replace `build_session_context_factory`:

```python
from matmaster.context.ports import (
    ActiveSkill,
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    SkillResolver,
    UserInstructions,
)


def build_session_context_factory(
    *,
    skill_resolver: SkillResolver,
    legal_mcp_servers: set[str] | None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
) -> SessionContextFactory:
    def factory(events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=events,
            active_skills=skill_resolver(events),
            legal_mcp_servers=legal_mcp_servers,
            schemas_by_server=schemas_by_server,
        )

    return factory
```

Replace `build_runtime_context_assembly` parameter `skill_registry: Any` with `skill_resolver: SkillResolver`. Update the call site that builds `build_session_context_factory(...)` to pass `skill_resolver=skill_resolver`.

Also add a module-level empty resolver constant for use as a tolerant default:

```python
def empty_skill_resolver(_events: tuple[SessionEvent, ...]) -> tuple[ActiveSkill, ...]:
    """Default SkillResolver for paths that don't wire one (devshell, isolated tests)."""
    return ()
```

Export it from `matmaster.core.runtime_context_assembly` so `Exp.build_runtime` can do `self._skill_resolver = skill_resolver or empty_skill_resolver` (see Task 9).

Add a focused test:

```python
def test_empty_skill_resolver_returns_empty_tuple() -> None:
    assert empty_skill_resolver(()) == ()
    assert empty_skill_resolver(
        (SessionEvent(id=1, event_type="skill_hit", source=None, content={"skill_name": "x"}),)
    ) == ()
```

The non-optional `SkillResolver` annotation on `build_runtime_context_assembly` and `build_session_context_factory` is deliberate — None-check is centralized at the `Exp.build_runtime` boundary.

- [ ] **Step 4: Add `until_event_id` defense test**

Create `tests/matmaster/context/test_assembly.py` (or append to existing one):

```python
import pytest

from matmaster.context.assembly import (
    ContextAssembler,
    ContextAssemblyIntent,
    ContextRenderOptions,
    TurnAssemblyRequest,
)
from matmaster.context.ports import (
    ActiveSkill,
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    UserInstructions,
)
from matmaster.context.session import SessionContextBuilder
from matmaster.context.sources.turn_input import TurnInput


class _UnscopedEventsPort:
    """Test double that intentionally returns events past `until_event_id`."""

    def __init__(self, events):
        self._events = events
        self.captured_query: SessionEventQuery | None = None

    async def load_events(self, query):
        self.captured_query = query
        return self._events


@pytest.mark.asyncio
async def test_assembler_scopes_events_to_until_event_id_before_factory() -> None:
    """Even if a port mistakenly returns events past until_event_id,
    the assembler must crop them before resolving active skills."""
    events = (
        SessionEvent(id=1, event_type="skill_hit", source=None, content={"skill_name": "pxrd"}),
        SessionEvent(id=9, event_type="skill_hit", source=None, content={"skill_name": "mlip"}),
    )

    seen_event_ids: list[tuple[int, ...]] = []

    def resolver(scoped_events):
        seen_event_ids.append(tuple(e.id for e in scoped_events))
        return tuple(
            ActiveSkill(name=e.content["skill_name"])
            for e in scoped_events
            if e.event_type == "skill_hit"
        )

    def factory(scoped_events):
        return SessionContextBuilder(
            events=scoped_events,
            active_skills=resolver(scoped_events),
        )

    assembler = ContextAssembler(
        ports=ContextAssemblyPorts(
            session_events=_UnscopedEventsPort(events),
            session_jobs=None,
        ),
        session_context_factory=factory,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput.from_values(
                user_text="",
                pre_turn_history_event_id=1,
            ),
            user_instructions=UserInstructions(text="", hash=""),
        ),
    )

    # Resolver should have seen only the event with id <= 1.
    assert seen_event_ids == [(1,)]
    # Rendered prompt must not mention mlip.
    # NOTE: UserTurnContext exposes render(view), not as_text() — see
    # matmaster/context/turn_context.py:41.
    from matmaster.context.sections import ContextView

    rendered = result.user_turn_context.render(ContextView.RUNTIME)
    assert "pxrd" in rendered
    assert "mlip" not in rendered
```

- [ ] **Step 5: Enforce twostage scoping inside `ContextAssembler._build_via_factory`**

Modify `matmaster/context/assembly.py`:

```python
def _build_via_factory(
    self,
    events: tuple[SessionEvent, ...],
    until_event_id: int,
    include_attachments: bool,
) -> tuple[ContextSection, ...]:
    assert self._session_context_factory is not None
    # Defense in depth: even if a port returned events past the boundary,
    # the factory (and its skill_resolver) must only see in-scope events.
    # SessionContextBuilder no longer re-crops for skill rendering — that
    # invariant lives here.
    if until_event_id is not None:
        scoped_events = tuple(e for e in events if e.id <= until_event_id)
    else:
        scoped_events = events
    builder = self._session_context_factory(scoped_events)
    return builder.build_sections(
        until_event_id=until_event_id,
        include_attachments=include_attachments,
    )
```

`SessionContextBuilder.build_sections` 的二次裁剪仍保留（attachments 路径用），但 skills/tools 路径自此依赖 `_build_via_factory` 的前置裁剪——`active_skills` 在 factory 调用瞬间已是 scope-correct。

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/matmaster/core/test_runtime_context_assembly.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/context/test_assembly.py -q
```

Expected: PASS.

Note: `test_exp_runtime_v2.py` may need updates if it constructs runtime context with `skill_registry=`. Apply the mechanical rename now; full sweep happens in Task 9.

- [ ] **Step 7: Commit checkpoint**

```bash
git add matmaster/core/runtime_context_assembly.py matmaster/context/assembly.py tests/matmaster/core/test_runtime_context_assembly.py tests/matmaster/context/test_assembly.py
git commit -m "refactor(context): factory closure takes skill resolver and enforces event scope"
```

---

### Task 6: Implement Service-Layer `SkillRegistryResolver`

**Files:**
- Create: `src/services/skill_registry_factory.py`
- Create: `src/services/skill_resolver.py`
- Test: `tests/matmaster/services/test_skill_registry_factory.py`
- Test: `tests/matmaster/services/test_skill_resolver.py`

- [ ] **Step 1: Write tests for the registry factory**

Create `tests/matmaster/services/test_skill_registry_factory.py`:

```python
import json
from pathlib import Path

from matmaster.skills.registry import SkillRegistry
from src.services.skill_registry_factory import build_skill_registry


def _write_skill(root: Path, name: str, mcp_server: str | None = None) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = f"name: {name}\ndescription: {name} description"
    if mcp_server:
        fm += f"\nmcp_server: {mcp_server}"
    (skill_dir / "SKILL.md").write_text(f"---\n{fm}\n---\nbody")


class _Session:
    def __init__(
        self,
        local_user_skills_root=None,
        remote_skill_roots=(),
        remote_user_skills_root=None,
    ):
        self.local_user_skills_root = local_user_skills_root
        self.remote_skill_roots = list(remote_skill_roots)
        self.remote_user_skills_root = remote_user_skills_root


def test_build_skill_registry_returns_none_when_no_roots(tmp_path: Path) -> None:
    assert build_skill_registry(
        config_roots=(),
        session=_Session(),
        config_disabled=(),
    ) is None


def test_build_skill_registry_appends_local_user_skills_root(tmp_path: Path) -> None:
    config_root = tmp_path / "config_skills"
    user_root = tmp_path / "user_skills"
    _write_skill(config_root, "alpha")
    _write_skill(user_root, "beta")

    registry = build_skill_registry(
        config_roots=(config_root,),
        session=_Session(local_user_skills_root=str(user_root)),
        config_disabled=(),
    )

    assert registry is not None
    assert {s.meta_info.name for s in registry.get_all_skills()} == {"alpha", "beta"}


def test_build_skill_registry_applies_config_and_settings_disable(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "alpha")
    _write_skill(root, "beta")
    _write_skill(root, "gamma")
    (root / ".settings.json").write_text(json.dumps({"disabled": ["gamma"]}))

    registry = build_skill_registry(
        config_roots=(root,),
        session=_Session(),
        config_disabled=("beta",),
    )

    assert registry is not None
    assert {s.meta_info.name for s in registry.get_all_skills()} == {"alpha"}
```

- [ ] **Step 2: Implement factory**

Create `src/services/skill_registry_factory.py`:

```python
"""Single source of truth for service-layer SkillRegistry construction.

Centralizes roots resolution (config + session-local + remote), disabled-name
collection (config + per-root .settings.json), and removal. Used by service
layer to build a registry used inside SkillRegistryResolver and any tests
that need a fully-configured registry.

Core-layer SkillRegistry construction (Exp._init_skill_tools) is intentionally
NOT routed through this factory: core builds its own registry instance whose
purpose is SkillTool registration, not active-skill rehydration. The two
registries have different responsibilities and should remain independent.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from matmaster.skills.registry import SkillRegistry

_LOGGER = logging.getLogger(__name__)


def _local_user_skills_root(session: Any | None) -> Path | None:
    if session is None:
        return None
    raw = getattr(session, "local_user_skills_root", None)
    if not isinstance(raw, str):
        return None
    root = raw.strip()
    return Path(root) if root else None


def _remote_skill_roots(session: Any | None) -> list[str]:
    if session is None:
        return []
    roots: list[str] = []
    raw_roots = getattr(session, "remote_skill_roots", None)
    if isinstance(raw_roots, (list, tuple, set)):
        roots.extend(
            root.strip() for root in raw_roots if isinstance(root, str) and root.strip()
        )
    raw_user_root = getattr(session, "remote_user_skills_root", None)
    if isinstance(raw_user_root, str) and raw_user_root.strip():
        roots.append(raw_user_root.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def _disabled_skill_names_from_settings(root: Path) -> set[str]:
    settings_path = root / ".settings.json"
    if not settings_path.is_file():
        return set()
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _LOGGER.warning(
            "Failed to read skill settings: %s", settings_path, exc_info=True
        )
        return set()
    disabled = payload.get("disabled") if isinstance(payload, dict) else None
    if not isinstance(disabled, list):
        return set()
    return {name.strip() for name in disabled if isinstance(name, str) and name.strip()}


def build_skill_registry(
    *,
    config_roots: Iterable[str | Path],
    session: Any | None,
    config_disabled: Iterable[str] = (),
) -> SkillRegistry | None:
    """Build a SkillRegistry covering config, session-local, and remote roots.

    Returns None when no roots are configured. Applies config-level and
    per-root .settings.json disable lists before returning.

    `config_roots` accepts str or Path elements — `exp_config.skills.skills_root`
    can be either depending on TOML parsing; Path() normalizes both.
    """
    roots = [Path(root) for root in config_roots if root]
    local = _local_user_skills_root(session)
    if local is not None:
        roots.append(local)
    remote_roots = _remote_skill_roots(session)
    if not roots and not remote_roots:
        return None
    registry = SkillRegistry(
        roots,
        remote_session=session if remote_roots else None,
        remote_roots=remote_roots,
    )
    disabled: set[str] = set(config_disabled)
    for root in roots:
        disabled.update(_disabled_skill_names_from_settings(root))
    if disabled:
        registry.remove_skills(disabled)
    return registry
```

- [ ] **Step 3: Write tests for the resolver**

Create `tests/matmaster/services/test_skill_resolver.py`:

```python
from pathlib import Path

from matmaster.context.ports import ActiveSkill, SessionEvent
from src.services.skill_registry_factory import build_skill_registry
from src.services.skill_resolver import SkillRegistryResolver


def _write_skill(root: Path, name: str, mcp_server: str | None = None) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = f"name: {name}\ndescription: {name} desc"
    if mcp_server:
        fm += f"\nmcp_server: {mcp_server}"
    (skill_dir / "SKILL.md").write_text(f"---\n{fm}\n---\nbody")


def _skill_hit(event_id: int, name: str) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        event_type="skill_hit",
        source=None,
        content={"skill_name": name},
    )


def test_resolver_returns_active_skills_for_recorded_hits(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "pxrd", mcp_server="xrd_srv")
    _write_skill(root, "mlip")
    registry = build_skill_registry(
        config_roots=(root,), session=None, config_disabled=()
    )

    resolver = SkillRegistryResolver(registry)
    events = (_skill_hit(1, "pxrd"), _skill_hit(2, "mlip"))

    assert resolver(events) == (
        ActiveSkill(name="pxrd", description="pxrd desc", mcp_server="xrd_srv"),
        ActiveSkill(name="mlip", description="mlip desc", mcp_server=None),
    )


def test_resolver_silently_drops_unknown_skill_hits(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "alpha")
    registry = build_skill_registry(config_roots=(root,), session=None)

    resolver = SkillRegistryResolver(registry)
    events = (_skill_hit(1, "alpha"), _skill_hit(2, "ghost"))

    assert resolver(events) == (
        ActiveSkill(name="alpha", description="alpha desc", mcp_server=None),
    )


def test_resolver_handles_none_registry() -> None:
    resolver = SkillRegistryResolver(None)
    assert resolver((_skill_hit(1, "x"),)) == ()


def test_resolver_skips_skill_when_registry_lookup_raises(tmp_path: Path) -> None:
    """Resolver must mirror the existing tolerant behavior of
    resolve_active_skills (skills.py:27-30) and exp.py:913-919:
    active skill rehydrate failures cannot break the main flow.
    """

    class _BrokenRegistry:
        def get_skill(self, name: str):
            if name == "broken":
                raise RuntimeError("simulated lookup failure")
            return None  # other names just resolve to None silently

    resolver = SkillRegistryResolver(_BrokenRegistry())
    events = (_skill_hit(1, "broken"), _skill_hit(2, "ghost"))

    assert resolver(events) == ()  # both skipped, no exception propagates
```

- [ ] **Step 4: Implement resolver**

Create `src/services/skill_resolver.py`:

```python
"""SkillRegistryResolver — service-layer SkillResolver implementation.

Provides the matmaster-side SkillResolver contract by scanning typed events
for skill_hit records and looking each name up in a SkillRegistry.
Disabled skills are already absent from the registry (handled by
build_skill_registry), so resolver does not need a separate disable list.
"""

from __future__ import annotations

from typing import Any

from matmaster.context.ports import ActiveSkill, SessionEvent
from matmaster.context.scanner import scan_skill_hits


class SkillRegistryResolver:
    """Callable that resolves typed events into ActiveSkill tuples."""

    def __init__(self, skill_registry: Any | None) -> None:
        self._registry = skill_registry

    def __call__(
        self, events: tuple[SessionEvent, ...]
    ) -> tuple[ActiveSkill, ...]:
        if self._registry is None:
            return ()
        active: list[ActiveSkill] = []
        for record in scan_skill_hits(events):
            # Mirror tolerant behavior of resolve_active_skills (now removed)
            # and LazyMCP replay in exp.py:913-919. Active skill rehydrate
            # failures must not propagate into the main assembly flow.
            try:
                skill = self._registry.get_skill(record.skill_name)
            except Exception:
                logger.warning(
                    "active skill resolver: get_skill(%r) raised, skipping",
                    record.skill_name,
                    exc_info=True,
                )
                continue
            if skill is None:
                continue
            meta = skill.meta_info
            active.append(
                ActiveSkill(
                    name=meta.name,
                    description=meta.description or "",
                    mcp_server=meta.mcp_server,
                )
            )
        return tuple(active)
```

Module top:

```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 5: Run resolver and factory tests**

```bash
uv run pytest tests/matmaster/services/test_skill_registry_factory.py tests/matmaster/services/test_skill_resolver.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit checkpoint**

```bash
git add src/services/skill_registry_factory.py src/services/skill_resolver.py tests/matmaster/services/test_skill_registry_factory.py tests/matmaster/services/test_skill_resolver.py
git commit -m "feat(services): add skill registry factory and resolver"
```

---

### Task 7: Wire `build_context_assembler` With `SkillResolver`

**Files:**
- Modify: `src/services/context_assembly_factory.py`
- Test: `tests/services/test_context_assembly_factory.py`

- [ ] **Step 1: Update factory tests**

In `tests/services/test_context_assembly_factory.py`, replace `skill_registry=` arguments with `skill_resolver=` and pass either a `SkillRegistryResolver` or a stub callable returning `()`.

```python
from matmaster.context.ports import ActiveSkill
from src.services.context_assembly_factory import build_context_assembler


def _empty_resolver(events):
    return ()


def test_build_context_assembler_returns_ports_and_assembler(...) -> None:
    assembler, ports = build_context_assembler(
        events_table=fake_table,
        skill_resolver=_empty_resolver,
        legal_mcp_servers=None,
        schemas_by_server=None,
    )
    ...
```

- [ ] **Step 2: Run tests to verify fail**

```bash
uv run pytest tests/services/test_context_assembly_factory.py -q
```

Expected: FAIL — `skill_resolver` argument not yet accepted.

- [ ] **Step 3: Update factory signature**

Modify `src/services/context_assembly_factory.py`:

```python
from matmaster.context.ports import ContextAssemblyPorts, SkillResolver


def build_context_assembler(
    *,
    events_table: object,
    skill_resolver: SkillResolver,
    legal_mcp_servers: set[str] | None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
    split_turn_attachments: bool = False,
) -> tuple[ContextAssembler, ContextAssemblyPorts]:
    ports = ContextAssemblyPorts(
        session_events=AppSessionEventsPort(events_table=events_table),
        session_jobs=AppSessionJobsPort(),
    )
    assembler = ContextAssembler(
        ports=ports,
        session_context_factory=build_session_context_factory(
            skill_resolver=skill_resolver,
            legal_mcp_servers=legal_mcp_servers,
            schemas_by_server=schemas_by_server,
        ),
        render_options=ContextRenderOptions(
            split_turn_attachments=split_turn_attachments,
        ),
    )
    return assembler, ports
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/services/test_context_assembly_factory.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

```bash
git add src/services/context_assembly_factory.py tests/services/test_context_assembly_factory.py
git commit -m "refactor(services): wire skill resolver into context assembler factory"
```

---

### Task 8: Refactor `agent_run_service` To Construct Registry Once

**Files:**
- Modify: `src/services/agent_run_service.py`
- Test: `tests/matmaster/services/test_active_mcp_replay.py`
- Test: `tests/matmaster/services/test_lazy_mcp_replay.py`

- [ ] **Step 1: Identify all behavioural assertions for active skill rehydrate**

```bash
rg -n "_active_skills|_resolve_active_skill_names|_build_skill_registry|_remote_skill_roots_from_session" src tests
```

Document every call site in a scratch note (do not commit).

- [ ] **Step 2: Update replay tests to use resolver**

In `tests/matmaster/services/test_active_mcp_replay.py` and `test_lazy_mcp_replay.py`:

- Remove `from matmaster.context.sources.skills import resolve_active_skills` if present.
- Replace assertions that depend on `_build_skill_registry` returning a registry with assertions on `SkillRegistryResolver(...)(events)` returning ActiveSkill tuples.

Example:

```python
from src.services.skill_registry_factory import build_skill_registry
from src.services.skill_resolver import SkillRegistryResolver
from src.services.session_event_codec import decode_session_events


def test_value_wrapped_skill_hit_resolves_via_resolver(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "test-skill", "mat_sg")
    registry = build_skill_registry(config_roots=(root,), session=None)
    resolver = SkillRegistryResolver(registry)
    events = decode_session_events(
        [{"id": 1, "type": "skill_hit", "content": "test-skill"}]
    )

    skills = resolver(events)

    assert [s.name for s in skills] == ["test-skill"]
    assert skills[0].mcp_server == "mat_sg"
```

- [ ] **Step 3: Run replay tests to verify fail**

```bash
uv run pytest tests/matmaster/services/test_active_mcp_replay.py tests/matmaster/services/test_lazy_mcp_replay.py -q
```

Expected: FAIL — new resolver import not yet usable from production code.

- [ ] **Step 4: Strip private skill registry helpers from agent_run_service**

In `src/services/agent_run_service.py`:

- Remove imports: `from matmaster.context.sources.skills import resolve_active_skills, skill_name`. Replace with import of `scan_skill_hits` from `matmaster.context.scanner` if needed for name-set computation.
- Remove module-level `_remote_skill_roots_from_session()` function.
- Remove `AgentRunService._build_skill_registry()` method entirely.
- Replace `AgentRunService._active_skills: dict[str, set[str]]` with `dict[str, frozenset[str]]` — only names are cached for LazyMCP replay.
- Rewrite `_resolve_active_skill_names` to compute name set directly from events without registry:

```python
def _resolve_active_skill_names(
    self,
    session_id: str,
    events_table: Any,
    *,
    until_event_id: int | None = None,
) -> frozenset[str]:
    cached = self._active_skills.get(session_id)
    if cached is not None:
        return cached

    raw_events: list[dict] = []
    if events_table is not None:
        try:
            raw_events = events_table.get_session_events(
                session_id, limit=_DIALOG_HISTORY_MAX_EVENTS
            )
        except Exception:
            logger.warning(
                "active skill rehydrate: get_session_events failed for session_id=%s",
                session_id,
                exc_info=True,
            )

    events = decode_session_events(raw_events)
    names = frozenset(
        record.skill_name for record in scan_skill_hits(events) if record.skill_name
    )
    self._active_skills[session_id] = names
    return names
```

- [ ] **Step 5: Extract caller-side guard into a testable helper, build resolver once**

In `src/services/agent_run_service.py`, add a private method on `AgentRunService`:

```python
def _build_skill_resolver(
    self, exp_config: Any, session: Any | None
) -> SkillResolver:
    """Caller-side guard for skills.enabled. Returns empty_skill_resolver
    when skills are disabled — never invokes build_skill_registry in that
    branch. This is the only point in run_agent that decides whether a
    SkillRegistry is constructed for the current request.
    """
    if exp_config.skills is None or not exp_config.skills.enabled:
        return empty_skill_resolver
    roots_raw = exp_config.skills.skills_root
    if isinstance(roots_raw, (list, tuple)):
        config_roots: tuple[str | Path, ...] = tuple(roots_raw)
    elif roots_raw:
        config_roots = (roots_raw,)
    else:
        config_roots = ()
    registry = build_skill_registry(
        config_roots=config_roots,
        session=session,
        config_disabled=exp_config.skills.disabled_skill_names or (),
    )
    return SkillRegistryResolver(registry)
```

Then in `run_agent()`:

```python
from matmaster.core.runtime_context_assembly import empty_skill_resolver
from src.services.skill_registry_factory import build_skill_registry
from src.services.skill_resolver import SkillRegistryResolver

skill_resolver = self._build_skill_resolver(exp_config, pg_ctx.session)
# SkillRegistryResolver(None) returns () for any events; empty_skill_resolver
# also returns (); downstream context_assembler / Exp.build_runtime work
# uniformly regardless of whether skills are enabled.

context_assembler, assembly_ports = build_context_assembler(
    events_table=events_table,
    skill_resolver=skill_resolver,
    legal_mcp_servers=(pg_ctx.run_meta or {}).get("legal_mcp_servers"),
    schemas_by_server=(pg_ctx.run_meta or {}).get("schemas_by_server"),
    split_turn_attachments=bool(
        (pg_ctx.run_meta or {}).get("split_turn_attachments", False)
    ),
)
```

Replace later call `self._resolve_active_skill_names(...)` to only request the name set (no registry arg). **DO NOT put `skill_resolver` into `pg_ctx.run_meta`** — `run_meta` is for passive data; resolver flows via explicit kwarg through `Exp.run_stream(skill_resolver=...)` (see Task 9).

- [ ] **Step 5b: Pass resolver to Exp via explicit kwarg**

At the `exp.run_stream(...)` call site (currently around [`agent_run_service.py:649`](src/services/agent_run_service.py:649)), add `skill_resolver=skill_resolver` to the kwargs. The full chain becomes:

```python
async with aclosing(
    exp.run_stream(
        pg_ctx,
        user_prompt,
        history=history,
        cancel_token=cancel_token,
        skills=pg_ctx.run_meta.get("skill_config"),
        skill_resolver=skill_resolver,
    )
) as stream:
    ...
```

- [ ] **Step 5c: Add enabled=False guard regression test (sentinel-style)**

The previous draft only verified `SkillRegistryResolver(None)(events) == ()`, which is already covered by `test_resolver_handles_none_registry` in Task 6 and would pass even if `run_agent` mistakenly called `build_skill_registry(...)` in the disabled branch. The test must hit the guard at the **caller boundary** — `AgentRunService._build_skill_resolver`.

Add to `tests/matmaster/services/test_agent_run_service.py` (create if missing):

```python
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.agent_run_service import AgentRunService


def _skills_config(enabled: bool, root: str | None = "/tmp/skills") -> Any:
    return SimpleNamespace(
        enabled=enabled,
        skills_root=root,
        disabled_skill_names=(),
    )


def test_build_skill_resolver_skips_registry_when_skills_disabled() -> None:
    """If skills.enabled=False, _build_skill_resolver must NOT invoke
    build_skill_registry — even if a skills_root is configured.

    Uses a patched factory that raises on any invocation, so the test fails
    loudly if a future refactor removes the guard.
    """
    svc = AgentRunService()
    exp_config = SimpleNamespace(skills=_skills_config(enabled=False))

    with patch(
        "src.services.agent_run_service.build_skill_registry",
        side_effect=AssertionError(
            "build_skill_registry must not be called when skills.enabled=False"
        ),
    ):
        resolver = svc._build_skill_resolver(exp_config, session=None)

    # And the returned resolver is the empty constant, not just None-wrapped.
    from matmaster.core.runtime_context_assembly import empty_skill_resolver

    assert resolver is empty_skill_resolver


def test_build_skill_resolver_skips_registry_when_skills_config_missing() -> None:
    """exp_config.skills is None — disabled by absence."""
    svc = AgentRunService()
    exp_config = SimpleNamespace(skills=None)

    with patch(
        "src.services.agent_run_service.build_skill_registry",
        side_effect=AssertionError("must not be called when skills config is None"),
    ):
        resolver = svc._build_skill_resolver(exp_config, session=None)

    from matmaster.core.runtime_context_assembly import empty_skill_resolver

    assert resolver is empty_skill_resolver


def test_build_skill_resolver_constructs_registry_when_enabled(tmp_path) -> None:
    """Positive path: enabled=True triggers registry construction exactly once."""
    root = tmp_path / "skills"
    root.mkdir()
    (root / "alpha").mkdir()
    (root / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: a\n---\nbody"
    )

    svc = AgentRunService()
    exp_config = SimpleNamespace(skills=_skills_config(enabled=True, root=str(root)))

    resolver = svc._build_skill_resolver(exp_config, session=None)

    # Resolver should now reflect on-disk skill alpha.
    from src.services.session_event_codec import decode_session_events

    events = decode_session_events(
        [{"id": 1, "type": "skill_hit", "content": "alpha"}]
    )
    skills = resolver(events)
    assert [s.name for s in skills] == ["alpha"]
```

- [ ] **Step 6: Run service tests**

```bash
uv run pytest tests/matmaster/services -q
```

Expected: PASS.

- [ ] **Step 7: Commit checkpoint**

```bash
git add src/services/agent_run_service.py tests/matmaster/services/test_active_mcp_replay.py tests/matmaster/services/test_lazy_mcp_replay.py
git commit -m "refactor(services): centralize skill registry construction in active skills path"
```

---

### Task 9: Update Runtime Callers In `Exp` And Replay Hook

**Files:**
- Modify: `matmaster/core/exp.py`
- Modify: `matmaster/core/playground.py` (review only — verify no `skill_registry` parameter remains)
- Test: `tests/matmaster/core/test_exp_runtime_v2.py`
- Test: every test that constructs `build_runtime_context_assembly` with `skill_registry=`

- [ ] **Step 1: Survey all `skill_registry=` references in runtime call chain**

```bash
rg -n "skill_registry\\s*=" matmaster src tests
```

The valid remaining references after Task 9 should be limited to:
- `matmaster/skills/*` (SkillRegistry class internals and its tests)
- `matmaster/tools/builtin/skill_tool.py` (SkillTool itself takes a registry — unchanged)
- `matmaster/core/exp.py:_init_skill_tools` internal registry (the core-layer one — unchanged)
- Tests for `SkillRegistry` and `SkillTool` directly

Anything else passing `skill_registry=` into `build_runtime_context_assembly` / `build_session_context_factory` is stale.

- [ ] **Step 2: Add explicit `skill_resolver` parameter to `Exp.run_stream` and `Exp.build_runtime` with empty-resolver default**

In `matmaster/core/exp.py`:

1. Add `skill_resolver: SkillResolver | None = None` kwarg to `Exp.run_stream()` and `Exp.build_runtime()` signatures. `Exp.run_stream()` forwards its kwarg to `Exp.build_runtime()`.
2. **Normalize `None` to `empty_skill_resolver` at the `Exp.build_runtime` entry**:

```python
from matmaster.core.runtime_context_assembly import empty_skill_resolver

# At the top of build_runtime(), parallel to other instance-field assignment:
self._skill_resolver = skill_resolver or empty_skill_resolver
```

This is the **only** point in the system that handles a `None` resolver. Downstream consumers (`build_runtime_context_assembly`, `build_session_context_factory`, factory closure) all see a non-optional `SkillResolver`. Devshell-style callers and existing tests that do `exp.run_stream(ctx, "task")` without a resolver kwarg will keep working — they get the empty-tuple semantics, which preserves prior behavior (no active skills rendered when nothing is wired).

3. At the `build_runtime_context_assembly(...)` call site (currently around [`exp.py:467`](matmaster/core/exp.py:467)), replace:

```python
runtime_context = build_runtime_context_assembly(
    spec=spec,
    ctx=ctx,
    skill_registry=self._skill_registry,   # OLD
    spawn_id=spawn_id,
    logger=self.logger,
)
```

with:

```python
runtime_context = build_runtime_context_assembly(
    spec=spec,
    ctx=ctx,
    skill_resolver=self._skill_resolver,
    spawn_id=spawn_id,
    logger=self.logger,
)
```

**Do not read `skill_resolver` from `run_meta`**. `run_meta` continues to carry only the passive `active_skills: frozenset[str]` (used by `_init_skill_tools` LazyMCP replay at [`exp.py:906`](matmaster/core/exp.py:906)).

4. `self._skill_registry` field at [`exp.py:168`](matmaster/core/exp.py:168) / [`exp.py:371`](matmaster/core/exp.py:371) / [`exp.py:934`](matmaster/core/exp.py:934) is unchanged — that field still holds the **core-layer** registry constructed inside `_init_skill_tools` for SkillTool registration. It is distinct from `self._skill_resolver`. Add a brief comment at the field declaration making this distinction explicit.

5. [`exp.py:455`](matmaster/core/exp.py:455) `SystemPromptBuilder.build_system_prompt(skill_registry=self._skill_registry)` is unchanged — that is the registry-wide prompt prefix path, out of scope (see Scope section).

- [ ] **Step 2b: Propagate resolver through `_make_spawn_fn` to child Exp**

Subagent path is **separate from `AgentRunService.run_agent`**: [`exp.py:211`](matmaster/core/exp.py:211) `_make_spawn_fn` creates a closure that calls [`exp.py:271`](matmaster/core/exp.py:271) `child_exp.run_stream(ctx, task, cancel_token=..., spawn_id=...)` — currently no `skill_resolver` kwarg. Without this step, every subagent gets the empty-resolver fallback and renders zero active skills.

Changes in `matmaster/core/exp.py`:

1. Add `skill_resolver: SkillResolver` parameter to `_make_spawn_fn`:

```python
def _make_spawn_fn(
    self,
    ctx: PlaygroundContext,
    source_prefix: str,
    hook_executor: HookExecutor | None,
    skill_resolver: SkillResolver,   # ← NEW
) -> Callable[..., Awaitable[str]]:
```

2. Inside the `spawn_fn` closure, forward to the child's `run_stream`:

```python
drain = await drain_run_stream(
    child_exp.run_stream(
        ctx,
        task,
        cancel_token=cancel_token,
        spawn_id=child_spawn_id,
        skill_resolver=skill_resolver,     # ← NEW
    ),
    on_event=_forward_child_event,
)
```

3. At the call site that creates `spawn_fn` (currently around [`exp.py:431`](matmaster/core/exp.py:431)), pass `self._skill_resolver` — which is already normalized to either a real resolver or `empty_skill_resolver`:

```python
spawn_fn = self._make_spawn_fn(
    ctx,
    source_prefix=...,
    hook_executor=...,
    skill_resolver=self._skill_resolver,   # ← NEW
)
```

Add a focused regression test in `tests/matmaster/core/test_exp_runtime_v2.py` (or `test_hook_wiring.py`, wherever spawn_fn is currently tested):

```python
@pytest.mark.asyncio
async def test_spawn_fn_propagates_skill_resolver_to_child_exp() -> None:
    """Subagent path must forward parent's resolver, not fall back to empty."""
    captured_kwargs: dict[str, Any] = {}

    class _FakeChildExp:
        def run_stream(self, ctx, task, **kwargs):
            captured_kwargs.update(kwargs)
            async def _gen():
                if False:
                    yield None
            return _gen()

    sentinel_resolver = lambda events: ()

    with patch("matmaster.core.exp.Exp", return_value=_FakeChildExp()), \
         patch("matmaster.core.exp.load_exp_config", return_value=SimpleNamespace()):
        # construct an Exp, set its _skill_resolver, invoke _make_spawn_fn,
        # then await the resulting spawn_fn(exp_name="child", task="t")
        ...

    assert "skill_resolver" in captured_kwargs
    assert captured_kwargs["skill_resolver"] is sentinel_resolver
```

(实际测试设置依赖 `_make_spawn_fn` 现有签名 + `drain_run_stream` mock 形态，按执行时实际接口调整。)

- [ ] **Step 3: Keep core-layer SkillRegistry independent**

Verify that `_init_skill_tools` still constructs its own SkillRegistry (different responsibility — SkillTool registration). Add a comment near that construction:

```python
# Core-layer registry is independent of the service-layer resolver registry.
# Service registry serves prompt rendering (ActiveSkill DTO); this registry
# serves SkillTool registration into ToolCatalog. The two MUST NOT share
# state — they have different disabled-rule semantics by design.
```

- [ ] **Step 4: Update test fakes**

In every test that constructs `build_runtime_context_assembly(..., skill_registry=...)` or `RuntimeContextAssembly(...)` mocks, replace `skill_registry` with `skill_resolver`. For tests that only need an empty resolver:

```python
def _empty_resolver(events):
    return ()
```

- [ ] **Step 5: Run runtime tests**

```bash
uv run pytest tests/matmaster/core -q
```

Expected: PASS.

- [ ] **Step 6: Commit checkpoint**

```bash
git add matmaster/core/exp.py matmaster/core/playground.py tests/matmaster/core
git commit -m "refactor(core): pass skill resolver through runtime context assembly"
```

---

### Task 10: Boundary Tests And Final Regression

**Files:**
- Modify: `tests/matmaster/context/test_phase4_static_boundaries.py`
- Verify: full repo regression

- [ ] **Step 1: Add boundary tests**

Append to `tests/matmaster/context/test_phase4_static_boundaries.py`:

```python
def test_matmaster_context_does_not_reference_skill_registry() -> None:
    context_root = ROOT / "matmaster" / "context"
    offenders: list[str] = []
    for path in context_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "skill_registry" in text or "SkillRegistry" in text:
            offenders.append(path.relative_to(ROOT).as_posix())

    # system_prompt.py is intentionally out of R6 scope and may still reference
    # skill_registry for the registry-wide prompt prefix path.
    offenders = [p for p in offenders if "system_prompt.py" not in p]
    assert offenders == [], (
        "matmaster/context/* must not depend on SkillRegistry; "
        f"violations: {offenders}"
    )


def test_session_skills_source_does_not_export_legacy_helpers() -> None:
    from matmaster.context.sources import skills

    assert not hasattr(skills, "resolve_active_skills")
    assert not hasattr(skills, "skill_name")
    assert not hasattr(skills.SessionSkillsSource, "from_events")
```

- [ ] **Step 2: Run boundary tests**

```bash
uv run pytest tests/matmaster/context/test_phase4_static_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 3: Run scoped regression suites**

```bash
uv run pytest tests/matmaster/context tests/matmaster/services tests/matmaster/core tests/services/test_context_assembly_factory.py -q
```

Expected: PASS.

- [ ] **Step 4: Run full repo test suite**

```bash
uv run pytest tests -q
```

Expected: PASS.

- [ ] **Step 5a: Boundary check — `matmaster/context/*` is registry-free**

The pytest boundary test from Step 1 already enforces this. Re-run for safety:

```bash
uv run pytest tests/matmaster/context/test_phase4_static_boundaries.py::test_matmaster_context_does_not_reference_skill_registry -q
```

Expected: PASS. **This is the canonical R6 boundary check** — do not chase grep noise; trust the test.

- [ ] **Step 5b: Manual audit — list remaining `skill_registry` references and verify each against whitelist**

```bash
rg -n "skill_registry|SkillRegistry" matmaster src tests
```

Compare each match against this **explicit whitelist** (file path + intent). Anything NOT on the whitelist is a defect; do not delete legitimate references just to "clean up" the grep.

| Path | Intent | Reason |
|---|---|---|
| `matmaster/skills/registry.py` | Class definition | SkillRegistry itself lives here |
| `matmaster/tools/builtin/skill_tool.py` | SkillTool constructor takes `skill_registry=` | Tool needs registry to look up skill |
| `matmaster/core/exp.py` (multiple lines in `_init_skill_tools` body, e.g. 743, 752, 887, 896, 912, 934) | Core-layer registry for SkillTool registration | Different responsibility from service resolver |
| `matmaster/core/exp.py:168, 371, 934` (`self._skill_registry` field) | Core-layer registry instance field | Held on Exp for SystemPromptBuilder + replay loop |
| `matmaster/core/exp.py:455, 470` (`SystemPromptBuilder.build_system_prompt(skill_registry=...)`) | Registry-wide prompt prefix | Out of scope for R6; follow-up |
| `matmaster/context/system_prompt.py` | `_build_skills(skill_registry)` | Out of scope for R6; follow-up |
| `src/services/skill_registry_factory.py` | Factory that returns `SkillRegistry` | R6 new code |
| `src/services/skill_resolver.py` | Resolver wraps registry | R6 new code |
| `src/services/agent_run_service.py` (single `run_agent` build site) | One construction per run via factory | R6 new code |
| `tests/test_skill_registry.py` | SkillRegistry class tests | Unchanged |
| `tests/test_skill_tool.py` | Top-level SkillTool tests | Unchanged |
| `tests/matmaster/tools/builtin/test_skill_tool.py` | SkillTool tests | Unchanged |
| `tests/matmaster/tools/test_skill_tool_callback.py` | SkillTool callback tests | Unchanged |
| `tests/matmaster/tools/test_skill_meta_extras.py` | SkillMeta tests | Unchanged |
| `tests/matmaster/services/test_skill_registry_factory.py` | Factory tests | R6 new code |
| `tests/matmaster/services/test_skill_resolver.py` | Resolver tests | R6 new code |
| `tests/matmaster/services/test_active_mcp_replay.py`, `test_lazy_mcp_replay.py` | Replay tests using factory + resolver | R6 updated |

If anything else surfaces, fix it. **Do not extend the whitelist without consensus**.

- [ ] **Step 5c: Confirm no `resolve_active_skills` / standalone `skill_name` helper remains**

```bash
rg -n "resolve_active_skills|^from .* import .*skill_name|^from .* import .*\\bskill_name\\b" matmaster src tests
```

Expected: no matches. (Note: local variables named `skill_name` inside loops are fine — only the deleted module-level helper from `matmaster.context.sources.skills` matters.)

- [ ] **Step 6: Verify diff scope**

```bash
git diff --stat $(git merge-base HEAD test)
git diff --name-only $(git merge-base HEAD test)
```

Expected: changed paths match the File Structure section above; no R7 / R1 / E3 file touched.

- [ ] **Step 7: Prepare final PR**

```text
Title: refactor(context): decouple matmaster from SkillRegistry via ActiveSkill DTO

Summary:
- Introduce ActiveSkill DTO and SkillResolver type alias as matmaster's
  contract; matmaster/context/* no longer references SkillRegistry
  (system_prompt.py registry-wide prompt prefix path is out of scope).
- Add service-layer skill_registry_factory and SkillRegistryResolver,
  centralizing roots / disabled / local-user-root handling in one place.
  Factory is stateless; caller in run_agent owns the skills.enabled guard.
- Symmetrize SessionSkillsSource and SessionToolsSource around from_skills.
- Resolver flows through explicit Exp.run_stream/build_runtime kwargs,
  never via run_meta. run_meta["active_skills"] remains frozenset[str].
- `Exp.build_runtime` normalizes `skill_resolver=None` to the module-level
  `empty_skill_resolver` so devshell paths and isolated tests work
  unchanged; downstream signatures stay non-optional.
- `_make_spawn_fn` forwards parent's resolver to child Exp's `run_stream`
  so subagents render the same active skills as the parent path.
- ContextAssembler._build_via_factory now crops events by until_event_id
  before invoking the factory, making scope correctness an assembler-level
  invariant rather than a SessionContextBuilder convention.
- Fix Bug A (local_user_skills_root missing from prompt rendering) and
  Bug B (disabled skills appearing in prompt while removed from runtime).
- Service-layer registry is constructed exactly once per run; the duplicate
  _build_skill_registry call sites and _remote_skill_roots_from_session
  helper are removed.

Tests:
- uv run pytest tests/matmaster/context tests/matmaster/services tests/matmaster/core tests/services -q
- uv run pytest tests -q
```

---

## Self-Review Checklist

- [ ] R6 remains a single PR after all tasks complete.
- [ ] `matmaster/context/*` does not import `SkillRegistry` or use `skill_registry` parameter names, except `matmaster/context/system_prompt.py` (out of scope, see Out Of Scope). Verified by `test_matmaster_context_does_not_reference_skill_registry`.
- [ ] `SessionSkillsSource` and `SessionToolsSource` both expose `from_skills(skills: Iterable[ActiveSkill], ...)`, no `from_events`.
- [ ] `SessionContextBuilder.active_skills` is `tuple[ActiveSkill, ...]`, not `Any`.
- [ ] Service layer constructs `SkillRegistry` exactly once per run via `build_skill_registry`.
- [ ] **`skills.enabled=False` guard preserved**: caller in `agent_run_service.run_agent` short-circuits to `skill_registry = None` before invoking `build_skill_registry`. Regression test `test_skills_disabled_in_config_returns_empty_resolver` covers this.
- [ ] **`SkillResolver` flows through explicit kwargs only**: `Exp.run_stream(skill_resolver=...)` → `Exp.build_runtime(skill_resolver=...)` → `build_runtime_context_assembly(skill_resolver=...)`. **Not** carried by `run_meta`. `run_meta["active_skills"]` remains `frozenset[str]` exactly as today.
- [ ] **`empty_skill_resolver` defined in `matmaster/core/runtime_context_assembly.py` and used as the only `None` fallback**, at `Exp.build_runtime` entry: `self._skill_resolver = skill_resolver or empty_skill_resolver`. Downstream signatures (`build_runtime_context_assembly`, `build_session_context_factory`) remain non-optional `SkillResolver`. Devshell-style `exp.run_stream(ctx, "task")` without resolver kwarg must complete a session assembly without raising.
- [ ] **Subagent path propagates parent resolver**: `_make_spawn_fn(skill_resolver=...)` captures it, `child_exp.run_stream(skill_resolver=...)` forwards it. Regression test `test_spawn_fn_propagates_skill_resolver_to_child_exp` covers this.
- [ ] **`skills.enabled=False` guard is verified at the caller boundary**, not by re-testing `SkillRegistryResolver(None)`. Tests patch `build_skill_registry` with a sentinel that raises on invocation, asserting `_build_skill_resolver` returns the `empty_skill_resolver` constant without constructing a registry.
- [ ] **`ContextAssembler._build_via_factory` crops events by `until_event_id` before invoking factory**: skill resolution sees scope-correct events even if a port returns extra rows. Regression test `test_assembler_scopes_events_to_until_event_id_before_factory` covers this.
- [ ] `SkillRegistryResolver` handles `None` registry without raising; `get_skill()` exceptions are caught and logged, never propagated. Regression test `test_resolver_skips_skill_when_registry_lookup_raises` covers this.
- [ ] `_resolve_active_skill_names` no longer takes or constructs a registry; computes `frozenset[str]` directly from events for LazyMCP replay.
- [ ] Core-layer `Exp._init_skill_tools` still constructs an independent registry for SkillTool registration, with a comment marking the boundary. `self._skill_registry` (core-layer) and `self._skill_resolver` (service-layer) are distinct instance fields.
- [ ] `system_prompt.py:_build_skills` is unchanged (out of scope, documented as follow-up).
- [ ] `scan_skill_hits` remains in `matmaster/context/scanner.py` (typed event scanner, not row decoder).
- [ ] Hot cache `_active_skills` stores `frozenset[str]` only; **no `tuple[ActiveSkill, ...]` form anywhere**. Resolver output is not cached at service layer (factory closure invokes resolver on every assembly).
- [ ] No backwards-compatibility shim for removed `resolve_active_skills` / `skill_name` / `from_events`.
- [ ] Final grep audit uses the whitelist table (Task 10 Step 5b), not negative `-v` filters.
- [ ] Full tests run with `uv run pytest`, not system Python.

## Out Of Scope / Follow-Up

The following items are deliberately deferred and should be tracked separately:

- **`SystemPromptBuilder._build_skills(skill_registry)`**: registry-wide prompt prefix (lists all available skills, not just active). Different semantics from R6's active-skill rehydrate path. Cleanest fix: pass a precomputed `skills_directory_text: str` from service layer. Not in this plan.
- **Unifying core-layer and service-layer SkillRegistry instances**: rejected as scope creep. The two registries have intentionally different responsibilities (tool registration vs prompt rendering). Future work — if at all — should first prove that consolidation does not regress the disabled / local-user-root semantics each path currently enforces.
- **`SkillRegistryResolver` caching its own output**: currently invoked per assembly call. Profile before adding caching; remember that compaction paths legitimately need fresh resolution per `until_event_id`.
