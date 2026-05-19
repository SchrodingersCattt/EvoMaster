# Phase 2B Session Source 迁移与 manifests 等价 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 DESIGN.md v3.3 Phase 2B：把"events → session-level sections"的装配规则从 `matmaster/manifests/` 迁入 `matmaster/context/`，新增 `scanner.py` / `session.py` / `history_restore.py` 与三个 session source（`attachments.py` / `skills.py` / `tools.py`），并将 `matmaster/manifests/*` 改造为薄 shim 委托新 source。`ContextAssembler` 保留 2A 的空默认 builder，同时新增 `session_context_factory` seam，让 Phase 2C runtime cutover 能注入真正的 `SessionContextBuilder`。`src/services/model_history_restore_service.py` 重接到 `matmaster/context/history_restore.py` 的纯算法实现。runtime 主路径仍走 `matmaster.manifests` 入口（agent_run_service / context_compactor / agent_run_history_wiring 不动），整体行为与 Phase 1 末态逐 fixture 等价。

**Architecture:** Phase 2A 已经把核心类型、composition、ports、assembler、turn intent、平台 port 全部落到 `matmaster/context/` 与 `src/services/`，但 `ContextAssembler` 在 session sections 这一块还是空函数 seam。Phase 2B 的核心是把 events 流装配 session sections 的实际逻辑接上：先在 `matmaster/context/sources/{attachments,skills,tools}.py` 落地 3 个 typed source，再把它们装配进 `matmaster/context/session.py` 的 `SessionContextBuilder`，最后通过 `ContextAssembler(session_context_factory=...)` 接入这条新链路；无 factory 时继续使用 `_empty_session_section_builder`，避免 Phase 2B 提前改变 runtime 默认行为。`matmaster/context/scanner.py` 承担 events typed 扫描（迁自 `manifests/scanner.py`），新 source 都直接消费 `tuple[SessionEvent, ...]` 而非 `list[dict[str, Any]]`。`matmaster/context/history_restore.py` 落地 DESIGN §11.1 的 `ModelHistoryRestorer` 纯算法（DI 注入 events 访问），`src/services/model_history_restore_service.py` 把已经写在自己内部的 v0/hybrid/v1 三分支算法切换到调用新 restorer，同时把 checkpoint codec、tool_result JSON 归一化、`ChatHistoryConverter.events_to_messages` 和 history validation 作为兼容回调注入，保持 Phase 1 字节等价。`matmaster/manifests/*` 全部改造为薄 shim：`attachment.py` / `skill.py` / `mcp.py` / `scanner.py` 各自从 `matmaster/context/sources/...` 与 `matmaster/context/scanner.py` re-export 原有公共符号；`attachment.py` 通过 legacy adapter 兼容 display-flattened `User/query` rows；`rehydrator.py::CompactionRehydrator` 在 manifests 边界直接调用低层 source helper 并保留旧 XML 顺序（`attachments → loaded_skills → active_tools → runtime_context → external_artifacts`），而不是调用 `SessionContextBuilder.build_sections()` 的 `SectionOrder` 顺序。这样 `core/context_compactor.py`、`core/exp.py`、`src/services/agent_run_service.py`、`src/services/agent_run_history_wiring.py` 这四个 runtime 入口的函数签名与文本输出与 Phase 1 末态完全一致。`tests/matmaster/manifests/` 整套测试不删除、不重写、不修改用例，只通过 shim 重新指向新实现。在所有以上动作落地后，新增一组 events fixture golden master 等价对照测试，断言"同一 events 输入下 `matmaster.manifests.*` 旧出口 == `matmaster.context.*` 新出口"。Phase 2B 不切 runtime 主路径，不删除 `_apply_user_instructions_to_initial_user_query`，不动 `core/context_compactor.py` 主体，不动 `core/agent.py`，不切 checkpoint v1 marker，不做 prompt 形态 A/B。

**Tech Stack:** Python 3.11+ / uv / pytest / pytest-asyncio / dataclasses / Protocol / Pydantic `UserMessage` / `matmaster.context.ports.SessionEvent` typed envelope

**Spec 来源:** `.planning/context-refactor/DESIGN.md` §4.2、§5.1、§5.3、§6.3、§6bis、§7.1-7.3、§7bis、§11、§12、§14 Phase 2B、§15、§16、附录 B「Phase 2B 改动」、PHASE-2A-PLAN.md「Notes For Phase 2B」。

---

## 全局约束

1. Phase 2B 不切运行时主路径。`src/services/agent_run_service.py`、`src/services/agent_run_history_wiring.py`、`matmaster/core/agent.py`、`matmaster/core/exp.py`、`matmaster/core/context_compactor.py` 不允许新增 `from matmaster.context` 的 import。这些文件继续走 `matmaster.manifests.*` 入口；shim 内部把工作转交给 `matmaster.context.*`。
2. 不迁移 `ContextCompactor` 主体逻辑，不触碰 `matmaster/core/context_compactor.py` 的 LLM summary 路径，不切 checkpoint v1 marker，不做 prompt 形态 A/B。Phase 2B 唯一允许动 `core/context_compactor.py` 的场景是「`CompactionRehydrator` 改为 shim 后 import 路径不变、构造参数不变、方法签名与字符串输出不变」——这一切都在 shim 内部完成，`ContextCompactor.apply_compaction_plan` 不需要任何编辑。
3. 不删除 `_apply_user_instructions_to_initial_user_query`。该函数体与 `COMPAT:legacy-runtime-injection-helper` 标记由 Phase 2C cutover 负责清理。
4. 所有新 source / `SessionContextBuilder` / `ModelHistoryRestorer` 的主路径 events 入参类型为 `tuple[SessionEvent, ...]`（DESIGN.md §7.3 末段 v3.1 改造）。`coerce_session_events()` 只服务 raw context row / `query_context_events` 这类未被前端展示层 flatten 的事件。`matmaster.manifests` shim 还必须兼容 Phase 1 旧出口的 display-flattened `User/query` row（`content` 是 str，`files/images/workspace_paths` 在顶层，且部分历史测试没有 `id`），因此 attachment shim 使用 `matmaster/context/sources/attachments.py` 中的 legacy adapter helper，而不是强行先转 `SessionEvent`。这是 shim 边界的兼容逻辑，不进入 `SessionContextBuilder` / `ContextAssembler` 主路径。
5. `matmaster.manifests` 的所有公开符号（函数名、dataclass、类名、参数签名、返回类型）在 Phase 2B 内必须保持二进制兼容：现有 import 语句（如 `from matmaster.manifests.attachment import build_available_attachments`）继续可用，调用结果与 Phase 1 末态逐字节等价。任何对公共符号的 rename / 增删都属于 Phase 4 范围。
6. `tests/matmaster/manifests/` 下的所有现有用例不修改、不删除。Phase 2B 完成后这些测试仍然通过（验证 shim 等价性）。
7. `matmaster/context/sources/attachments.py` 必须暴露 `AttachmentEntry`、`AttachmentKind`、`scan_attachment_entries(events)`、`scan_legacy_attachment_entries(rows)`、`filter_entries_in_event_range(...)`、`format_entries_text(...)` 作为 `manifests/attachment.py` 的 source of truth；`SessionAttachmentsSource` 在 typed `SessionEvent` 基础上做 frozen wrapper，legacy shim 只调用 `scan_legacy_attachment_entries` 保持旧公开 API 等价。所有合并的 list/tuple 边界都用 `tuple` 表示。
8. `matmaster/context/sources/skills.py` 必须暴露 `resolve_active_skills(events, registry)`、`skill_name(skill)`、`format_loaded_skills(skills)`，并新增 `SessionSkillsSource`。`matmaster/context/sources/tools.py` 暴露 `resolve_declared_servers`、`resolve_runnable_servers`、`format_active_mcp`，并新增 `SessionToolsSource`。
9. `matmaster/context/session.py::SessionContextBuilder` 是项目里**唯一**新声明把 events / skills / tools / attachments 拼成 ContextSection tuple 的地方；composition step `_step_session_sections` 只读 `inputs.session_sections`，不直接调 source；`ContextAssembler` 通过 `SessionContextBuilder.build_sections(...)` 装配。
10. `matmaster/context/history_restore.py` 暴露的 `ModelHistoryRestorer` 必须严格遵循 DESIGN §11.1 v3.3 三分支（纯 v0 → `legacy_restore`，hybrid v1 → `_restore_v1(checkpoint=None)`，纯 v1 → `_restore_v1(checkpoint=...)`）。`covered_until_event_id` 为 None 时回退到 legacy restore（DESIGN §11.1 第二段 v3.3 修正）。Phase 1 写入的 `model_history_restore_service.py` 的等价行为是 Phase 2B 的回归基线；调用 `ModelHistoryRestorer.restore` 的最终 messages 列表必须与 Phase 1 末态完全相同。为保证等价，`ModelHistoryRestorer` 不手写 assistant/tool message 语义，而是通过注入的 `deserialize_base_messages` / `events_to_messages` / `normalize_tool_result_event` / `validate_history` 回调复用 Phase 1 的 codec 与 `ChatHistoryConverter` 行为。
11. `src/services/model_history_restore_service.py` 在 Phase 2B 改造后的角色是 thin DI factory + image trimming + spawn/task event 过滤。算法逻辑（v0/hybrid/v1 选择、event 合并、`covered_invocation_ids` 计算）迁到 `matmaster/context/history_restore.py::ModelHistoryRestorer`；Phase 1 service 特有的 checkpoint codec、tool_result JSON 归一化、`ChatHistoryConverter.events_to_messages` 衔接以回调形式注入 restorer。service 文件保留 `restore_history(...)` 这个 public method 入口给 `agent_run_history_wiring.py`，并保证 `trim_history_images` 只在最外层调用一次。
12. Phase 2B 的 commit 单位与 Phase 2A 一致：一个 Task → 一个 commit。`matmaster/manifests/*.py` 改为 shim 与对应新 source 同 commit；所有 commits 应在同一个 PR 内（DESIGN §14 Phase 2B "必须与新 source 同 PR"）。
13. 所有 Python 命令使用 `uv run python` 或 `uv run pytest`，不要使用系统 Python。
14. 当前工作树可能已有 `.planning/` 与若干源文件/测试文件的用户改动。执行本计划时不要恢复、格式化或改写任何与 Phase 2B 无关的 dirty 文件；若 Phase 2B 需要编辑某个已 dirty 文件，先读 diff 再最小化叠加修改。

---

## File Structure

新建文件（`matmaster/context/`）：

- Create: `matmaster/context/scanner.py`
- Create: `matmaster/context/session.py`
- Create: `matmaster/context/history_restore.py`
- Create: `matmaster/context/sources/attachments.py`
- Create: `matmaster/context/sources/skills.py`
- Create: `matmaster/context/sources/tools.py`

改造为 shim（`matmaster/manifests/`）：

- Modify: `matmaster/manifests/attachment.py` → 薄 shim re-export from `matmaster/context/sources/attachments.py`
- Modify: `matmaster/manifests/skill.py` → 薄 shim re-export from `matmaster/context/sources/skills.py`
- Modify: `matmaster/manifests/mcp.py` → 薄 shim re-export from `matmaster/context/sources/tools.py`
- Modify: `matmaster/manifests/scanner.py` → 薄 shim re-export from `matmaster/context/scanner.py`
- Modify: `matmaster/manifests/rehydrator.py` → `CompactionRehydrator` shim 直接调用 context source helpers，保留 legacy XML 顺序；构造参数与 `build()` 字符串输出保持 Phase 1 末态等价

Runtime 接线（最小化变更，签名不变）：

- Modify: `matmaster/context/assembly.py` → 新增 `session_context_factory` production seam；无 factory 时保留 `_empty_session_section_builder` 默认；保留 `_session_section_builder_for_tests` seam
- Modify: `src/services/model_history_restore_service.py` → 内部用 `ModelHistoryRestorer` DI 装配；`restore_history()` 签名不变；保留 `_normalize_tool_result_event` 等服务专属 helper

新测试（`tests/matmaster/context/`）：

- Test: `tests/matmaster/context/test_scanner.py`
- Test: `tests/matmaster/context/test_session.py`
- Test: `tests/matmaster/context/test_history_restore.py`
- Test: `tests/matmaster/context/sources/test_attachments.py`
- Test: `tests/matmaster/context/sources/test_skills.py`
- Test: `tests/matmaster/context/sources/test_tools.py`

新增 golden master fixture 对照测试：

- Test: `tests/matmaster/context/test_manifests_equivalence.py`（events fixture × manifests 旧出口 vs context 新出口）

更新 Phase 2A 测试（最小化）：

- Modify Test: `tests/matmaster/context/test_assembly.py` → 新增用例覆盖 `session_context_factory` 注入后的行为与无 factory 默认空 sections 行为；保留所有现有 seam 用例

保留并继续通过（不修改）：

- `tests/matmaster/manifests/test_attachment.py`（若存在；当前仅有以下四个 test 文件）
- `tests/matmaster/manifests/test_scanner.py`
- `tests/matmaster/manifests/test_skill.py`
- `tests/matmaster/manifests/test_mcp.py`
- `tests/matmaster/manifests/test_rehydrator.py`
- `tests/matmaster/services/test_model_history_restore_service.py`
- `tests/matmaster/services/test_user_turn_context_service.py`
- `tests/matmaster/services/test_agent_run_stream.py`
- 所有 Phase 2A 单元测试

Note: 仓库当前未存在 `tests/matmaster/manifests/test_attachment.py`；若发现 attachment 相关用例零散散落在 `tests/services/test_attachment_manifest_service.py`，按 DESIGN 不动它，shim 必须保证它继续通过。

---

## Task 1: Baseline And Phase Boundary Inventory

**Files:** read-only

**Spec 依据:** DESIGN.md §14 Phase 2A 终态 / Phase 2B 起手、附录 B「Phase 2A 改动」/「Phase 2B 改动」、PHASE-2A-PLAN.md「Notes For Phase 2B」。

- [ ] **Step 1: Confirm uv environment and dirty files**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -V && git status --short
```

Expected:

```text
Python 3.11+ or Python 3.13.x
git status --short prints the current dirty files (may include unrelated .planning/* and DESIGN.md edits)
```

已知工作树可能包含与 Phase 2B 无关的 dirty 文件。不要把 expected dirty list 当成必须匹配的断言；只需要确认 Python 环境正确，并记录哪些 dirty 文件可能与本计划编辑重叠（重点关注 `matmaster/context/assembly.py`、`matmaster/manifests/`、`src/services/model_history_restore_service.py`）。If other source files are dirty, read them before editing and do not revert them.

- [ ] **Step 2: Confirm Phase 2A artifacts are present and Phase 2B targets are absent**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && \
  test -f matmaster/context/sections.py && \
  test -f matmaster/context/rendering.py && \
  test -f matmaster/context/turn_context.py && \
  test -f matmaster/context/compositions.py && \
  test -f matmaster/context/assembly.py && \
  test -f matmaster/context/turn_intent.py && \
  test -f matmaster/context/ports.py && \
  test -f matmaster/context/sources/turn_input.py && \
  test -f matmaster/context/sources/user_instructions.py && \
  test -f matmaster/context/sources/compacted_history.py && \
  test -f matmaster/context/sources/session_jobs.py && \
  test ! -f matmaster/context/scanner.py && \
  test ! -f matmaster/context/session.py && \
  test ! -f matmaster/context/history_restore.py && \
  test ! -f matmaster/context/sources/attachments.py && \
  test ! -f matmaster/context/sources/skills.py && \
  test ! -f matmaster/context/sources/tools.py
```

Expected: command exits `0`. If any Phase 2A file is missing, stop and report — Phase 2B depends on a clean Phase 2A baseline. If any Phase 2B target file already exists, stop and inspect it.

- [ ] **Step 2b: Confirm `PlaygroundContext` fixture signature for Task 11**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "class PlaygroundContext|workdir:|session_type:|cache_area:" matmaster/core/playground.py matmaster/types/context.py
```

Expected: `PlaygroundContext` can still be constructed in tests with `workdir=...`, `session_type="local"`, and `cache_area=...` (via `matmaster/types/context.py` shim if applicable). If Phase 0.5 changed those field names, update Task 11 fixtures before implementing the equivalence tests.

- [ ] **Step 3: Run Phase 2A + Phase 1 focused baseline**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/manifests \
  tests/test_chat_events_history_checkpoint.py \
  -q
```

Expected: all tests pass. If baseline fails, stop and report failing test names before starting Phase 2B.

- [ ] **Step 4: Snapshot runtime callers of `matmaster.manifests`**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "from matmaster\.manifests|import matmaster\.manifests" matmaster src tests
```

Expected: matches limited to the following files (the runtime contract Phase 2B must preserve):

```text
src/services/agent_run_service.py
src/services/agent_run_history_wiring.py
matmaster/core/context_compactor.py
matmaster/core/exp.py
matmaster/manifests/rehydrator.py        # internal cross-import to attachment/skill/mcp
matmaster/manifests/skill.py             # internal cross-import to scanner
tests/services/test_attachment_manifest_service.py
tests/matmaster/manifests/test_*.py
tests/matmaster/integration/test_history_checkpoint_recovery.py
tests/matmaster/services/test_active_mcp_replay.py
```

Capture this list mentally; the same `rg` must continue to succeed after Phase 2B (no new runtime importer added, no caller removed).

- [ ] **Step 5: Confirm runtime caller types**

Read the following lines to inventory the public API the shim must preserve:

- [src/services/agent_run_history_wiring.py:21](../../src/services/agent_run_history_wiring.py:21) — `attachment_manifest.build_available_attachments(query_events)` 返回 `list[AttachmentEntry]`；下一行 `attachment_manifest.format_available_attachments(entries)` 返回 `str`。
- [src/services/agent_run_service.py:25](../../src/services/agent_run_service.py:25) — `skill_manifest.resolve_active_skills(raw_events, registry)`、[src/services/agent_run_service.py:218](../../src/services/agent_run_service.py:218) — `skill_manifest.skill_name(skill)`。
- [matmaster/core/exp.py:468](../../matmaster/core/exp.py:468) — `CompactionRehydrator(get_query_events=..., get_all_events=..., get_latest_checkpoint_covered_until_event_id=..., skill_registry=..., playground_ctx=..., legal_mcp_servers=..., schemas_by_server=...)`。
- [matmaster/core/context_compactor.py:181](../../matmaster/core/context_compactor.py:181) — `rehydrator: CompactionRehydrator` 参数；[matmaster/core/context_compactor.py:327](../../matmaster/core/context_compactor.py:327) — `await self._rehydrator.build(until_event_id=until_event_id)` 返回 `str`。

记录这些 import path、function signature、argument shape，作为 Task 10 shim 接线的等价契约。

This Task has no commit.

---

## Task 2: Add Typed Event Scanner Module

**Files:**
- Create: `matmaster/context/scanner.py`
- Create: `tests/matmaster/context/test_scanner.py`

**Spec 依据:** DESIGN.md §5.1（`scanner.py` 落在 `matmaster/context/`）、§7bis.2（`SessionEvent` 类型）、§7.3、§14 Phase 2B 新增文件清单、附录 B「Phase 2B 改动」。

`matmaster/context/scanner.py` 承担两类职责：(1) 把 `list[dict[str, Any]]` 形态的 raw DAO event row 转为 typed `tuple[SessionEvent, ...]`，供 shim 在不破坏外部签名的前提下接到新 source；(2) 暴露通用的 `scan_skill_hits` 等纯函数（从 `matmaster/manifests/scanner.py` 迁来），以便 `matmaster/context/sources/skills.py` 调用。

- [ ] **Step 1: Write failing tests for `coerce_session_events`**

Create `tests/matmaster/context/test_scanner.py`:

```python
from __future__ import annotations

import pytest

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import (
    SkillHitRecord,
    coerce_session_events,
    scan_skill_hits,
)


def test_coerce_session_events_maps_basic_fields() -> None:
    rows = [
        {
            "id": 10,
            "type": "query",
            "source": "User",
            "content": {"content": "hi", "files": ["a"]},
            "invocation_id": "inv-1",
            "spawn_id": None,
            "task_id": "task-1",
        },
        {
            "id": 11,
            "type": "skill_hit",
            "source": "System",
            "content": {"skill_name": "pxrd"},
        },
    ]

    events = coerce_session_events(rows)

    assert isinstance(events, tuple)
    assert len(events) == 2
    assert events[0] == SessionEvent(
        id=10,
        event_type="query",
        source="User",
        content={"content": "hi", "files": ("a",)},
        invocation_id="inv-1",
        spawn_id=None,
        task_id="task-1",
    )
    assert events[1].invocation_id is None
    assert events[1].content["skill_name"] == "pxrd"


def test_coerce_session_events_freezes_nested_lists_into_tuples() -> None:
    rows = [
        {
            "id": 7,
            "type": "query",
            "source": "User",
            "content": {
                "files": ["a", "b"],
                "images": ["c"],
                "nested": {"deep": ["x"]},
            },
        }
    ]

    events = coerce_session_events(rows)

    assert events[0].content["files"] == ("a", "b")
    assert events[0].content["images"] == ("c",)
    assert events[0].content["nested"]["deep"] == ("x",)


def test_coerce_session_events_drops_rows_without_int_id() -> None:
    rows = [
        {"id": None, "type": "query"},
        {"id": "not-an-int", "type": "query"},
        {"id": 9, "type": "query", "content": None, "source": None},
    ]

    events = coerce_session_events(rows)

    assert len(events) == 1
    assert events[0].id == 9
    assert events[0].source is None
    assert events[0].content == {}


def test_scan_skill_hits_accepts_session_events() -> None:
    events = coerce_session_events(
        [
            {"id": 1, "type": "query", "content": "skip"},
            {
                "id": 2,
                "type": "skill_hit",
                "content": {"skill_name": "pxrd"},
                "created_at": "2026-01-01T00:00:00",
            },
            {"id": 3, "type": "skill_hit", "content": {"skill_name": "mlip"}},
            {"id": 4, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
            {"id": 5, "type": "skill_hit", "content": {"skill_name": ""}},
        ]
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(skill_name="pxrd", event_id=2, timestamp="2026-01-01T00:00:00"),
        SkillHitRecord(skill_name="mlip", event_id=3, timestamp=None),
    )


def test_scan_skill_hits_accepts_legacy_string_content_via_coerce() -> None:
    events = coerce_session_events(
        [{"id": 7, "type": "skill_hit", "content": "search"}]
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(skill_name="search", event_id=7, timestamp=None),
    )
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_scanner.py -q
```

Expected: `ModuleNotFoundError: No module named 'matmaster.context.scanner'`.

- [ ] **Step 3: Implement `matmaster/context/scanner.py`**

Create `matmaster/context/scanner.py`:

```python
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import JsonObject, JsonValue, SessionEvent


@dataclass(frozen=True)
class SkillHitRecord:
    skill_name: str
    event_id: int | None = None
    timestamp: str | None = None


def _freeze_json_value(value: Any) -> JsonValue:
    """Convert raw DAO row payload into the restricted ports.JsonValue tree.

    Lists become tuples (immutable); dicts become regular dicts with frozen
    children; scalars pass through. None becomes None. Non-JSON values
    (sets, bytes, datetime, ...) are coerced via str() to avoid leaking
    untyped objects into the SessionEvent envelope; coerce_session_events
    keeps the source of truth in DB rows, so downstream consumers should
    treat freezing as a defensive boundary, not a contract for novel types.
    """
    if isinstance(value, Mapping):
        return {str(k): _freeze_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(v) for v in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _coerce_content(value: Any) -> JsonObject:
    if isinstance(value, Mapping):
        return {str(k): _freeze_json_value(v) for k, v in value.items()}
    if value is None:
        return {}
    # legacy events may carry bare strings (e.g. skill_hit with content="search")
    return {"content": _freeze_json_value(value)}


def _coerce_event_id(value: Any) -> int | None:
    if isinstance(value, bool):
        # bool is a subclass of int; explicit guard avoids treating True as id=1
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def coerce_session_events(rows: Iterable[Mapping[str, Any]]) -> tuple[SessionEvent, ...]:
    """Translate raw DAO event rows into the typed SessionEvent tuple.

    Rows without a coercible int id are dropped (defensive; the DAO should
    always supply an id, but legacy events / fixtures occasionally don't).
    Order is preserved. This helper is for raw context events, not
    display-flattened manifests rows. For compatibility with the legacy
    skill scanner, a top-level created_at value is copied into content when
    the content is a mapping and does not already provide created_at.
    """
    events: list[SessionEvent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        event_id = _coerce_event_id(row.get("id"))
        if event_id is None:
            continue
        content = _coerce_content(row.get("content"))
        if (
            isinstance(content, dict)
            and "created_at" not in content
            and row.get("created_at") is not None
        ):
            content = {**content, "created_at": _freeze_json_value(row.get("created_at"))}
        events.append(
            SessionEvent(
                id=event_id,
                event_type=str(row.get("type") or "").strip(),
                source=_coerce_optional_str(row.get("source")),
                content=content,
                task_id=_coerce_optional_str(row.get("task_id")),
                invocation_id=_coerce_optional_str(row.get("invocation_id")),
                spawn_id=_coerce_optional_str(row.get("spawn_id")),
            )
        )
    return tuple(events)


def _skill_name_from_content(content: JsonValue) -> str:
    if isinstance(content, Mapping):
        raw = content.get("skill_name") or content.get("content")
        return str(raw or "").strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def scan_skill_hits(events: Iterable[SessionEvent]) -> tuple[SkillHitRecord, ...]:
    seen: set[str] = set()
    records: list[SkillHitRecord] = []
    for event in events:
        if event.event_type != "skill_hit":
            continue
        name = _skill_name_from_content(event.content)
        if not name or name in seen:
            continue
        seen.add(name)
        timestamp_raw = event.content.get("created_at") if isinstance(event.content, Mapping) else None
        timestamp = str(timestamp_raw) if isinstance(timestamp_raw, str) and timestamp_raw else None
        records.append(
            SkillHitRecord(
                skill_name=name,
                event_id=event.id,
                timestamp=timestamp,
            )
        )
    return tuple(records)
```

- [ ] **Step 4: Verify tests are green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_scanner.py -q
```

Expected: all tests pass.

Note: the legacy `manifests/scanner.py` keeps a `timestamp` field that reads from `event.get("created_at")` (top-level), but the new typed scanner reads `event.content["created_at"]`. This is intentional: typed `SessionEvent` does not have a top-level `created_at`; `coerce_session_events()` copies a top-level `created_at` into content only as a compatibility bridge. Callers that construct `SessionEvent` directly must put timestamp data in `content`.

- [ ] **Step 5: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/context/scanner.py tests/matmaster/context/test_scanner.py && git commit -m "feat(context): add typed event scanner

Adds matmaster/context/scanner.py with coerce_session_events() and
scan_skill_hits() over the new SessionEvent envelope. Scanner is the
single conversion point between raw DAO rows and the typed event
sequence consumed by Phase 2B sources and SessionContextBuilder."
```

---

## Task 3: Add Session Attachments Source

**Files:**
- Create: `matmaster/context/sources/attachments.py`
- Create: `tests/matmaster/context/sources/test_attachments.py`

**Spec 依据:** DESIGN.md §5.1、§7.2（SessionAttachmentsSource, `order=SESSION_ATTACHMENTS=500`, views=RUNTIME+CHECKPOINT）、§14 Phase 2B 新增 `sources/attachments.py`、附录 B「Phase 2B 改动」。

新模块在 `matmaster/context/sources/attachments.py` 提供：
- `AttachmentKind` Literal（"file" / "image" / "workspace"）
- `AttachmentEntry` frozen dataclass（保留 `kind` / `label` / `name` / `value` / `source_event_id`）
- `scan_attachment_entries(events: tuple[SessionEvent, ...], *, max_entries: int = 30) -> tuple[AttachmentEntry, ...]`
- `scan_legacy_attachment_entries(rows: Iterable[Mapping[str, Any]], *, max_entries: int = 30) -> tuple[AttachmentEntry, ...]`（仅供 `matmaster.manifests.attachment` shim 使用；兼容 display-flattened 顶层 `files/images/workspace_paths` 与缺失 `id` 的旧测试/生产形态）
- `filter_entries_in_event_range(entries, *, after_id, until_id) -> tuple[AttachmentEntry, ...]`
- `filter_entries_after_event_id(entries, after_id) -> tuple[AttachmentEntry, ...]`
- `format_entries_text(entries) -> str`（保留 `[Available attachments]` 前缀格式，逐字节匹配 `manifests/attachment.format_available_attachments`）
- `SessionAttachmentsSource` frozen dataclass，按 DESIGN §7 接口约定暴露 `to_sections() -> tuple[ContextSection, ...]`，并提供 `from_events(events, *, until_event_id, after_id, max_entries=30)` classmethod

- [ ] **Step 1: Write failing tests**

Create `tests/matmaster/context/sources/test_attachments.py`:

```python
from __future__ import annotations

from matmaster.context.scanner import coerce_session_events
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.attachments import (
    AttachmentEntry,
    SessionAttachmentsSource,
    filter_entries_in_event_range,
    format_entries_text,
    scan_attachment_entries,
    scan_legacy_attachment_entries,
)


_QUERY_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {
            "files": ["https://oss.example.com/chat/a.csv"],
            "images": ["https://img.example.com/x.png"],
            "workspace_paths": ["/ws/data.csv"],
        },
    },
    {
        "id": 20,
        "source": "User",
        "type": "query",
        "content": {
            "files": ["https://oss.example.com/chat/b.csv"],
        },
    },
    {
        "id": 30,
        "source": "Assistant",
        "type": "response",
        "content": {"text": "ignored"},
    },
]


def test_scan_attachment_entries_dedup_and_label() -> None:
    events = coerce_session_events(_QUERY_EVENTS)

    entries = scan_attachment_entries(events)

    assert entries == (
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="a.csv",
            value="https://oss.example.com/chat/a.csv",
            source_event_id=10,
        ),
        AttachmentEntry(
            kind="image",
            label="image_1",
            name="x.png",
            value="https://img.example.com/x.png",
            source_event_id=10,
        ),
        AttachmentEntry(
            kind="workspace",
            label="workspace_1",
            name="/ws/data.csv",
            value="/ws/data.csv",
            source_event_id=10,
        ),
        AttachmentEntry(
            kind="file",
            label="file_2",
            name="b.csv",
            value="https://oss.example.com/chat/b.csv",
            source_event_id=20,
        ),
    )


def test_scan_legacy_attachment_entries_reads_top_level_metadata_without_id() -> None:
    rows = [
        {
            "source": "User",
            "type": "query",
            "content": "analyze attachments",
            "files": ["https://oss.example.com/chat/data.csv"],
            "images": ["https://oss.example.com/chat/em.png"],
            "workspace_paths": ["/share/a.cif"],
        }
    ]

    entries = scan_legacy_attachment_entries(rows)

    assert entries == (
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="data.csv",
            value="https://oss.example.com/chat/data.csv",
            source_event_id=None,
        ),
        AttachmentEntry(
            kind="image",
            label="image_1",
            name="em.png",
            value="https://oss.example.com/chat/em.png",
            source_event_id=None,
        ),
        AttachmentEntry(
            kind="workspace",
            label="workspace_1",
            name="/share/a.cif",
            value="/share/a.cif",
            source_event_id=None,
        ),
    )


def test_filter_entries_in_event_range_window() -> None:
    events = coerce_session_events(_QUERY_EVENTS)
    entries = scan_attachment_entries(events)

    filtered = filter_entries_in_event_range(entries, after_id=10, until_id=None)

    assert tuple(entry.label for entry in filtered) == ("file_2",)


def test_format_entries_text_matches_legacy_shape() -> None:
    events = coerce_session_events(_QUERY_EVENTS)
    entries = scan_attachment_entries(events)

    text = format_entries_text(entries)

    assert text.startswith("[Available attachments]\n")
    assert "file_1 a.csv https://oss.example.com/chat/a.csv" in text
    assert "image_1 x.png https://img.example.com/x.png" in text
    assert "workspace_1 /ws/data.csv" in text


def test_format_entries_text_empty() -> None:
    assert format_entries_text(()) == ""


def test_source_to_sections_emits_runtime_plus_checkpoint() -> None:
    events = coerce_session_events(_QUERY_EVENTS)

    source = SessionAttachmentsSource.from_events(events)
    sections = source.to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert isinstance(section, ContextSection)
    assert section.key == "session_attachments"
    assert section.tag == "attachments"
    assert section.order == SectionOrder.SESSION_ATTACHMENTS
    assert ContextView.RUNTIME in section.views
    assert ContextView.CHECKPOINT in section.views
    assert "[Available attachments]" in section.content


def test_source_to_sections_empty_returns_no_section() -> None:
    source = SessionAttachmentsSource.from_events(())
    assert source.to_sections() == ()


def test_source_from_events_respects_until_event_id() -> None:
    events = coerce_session_events(_QUERY_EVENTS)

    source = SessionAttachmentsSource.from_events(events, until_event_id=10)
    text = source.to_sections()[0].content

    assert "file_1 a.csv" in text
    assert "b.csv" not in text


def test_source_with_added_appends_entries_idempotently() -> None:
    """Phase 4 oversized-input bypass requires SessionAttachmentsSource.with_added.

    Ensures the appended entries do not collide with existing labels and
    that the result is still a frozen dataclass.
    """
    base = SessionAttachmentsSource(
        entries=(
            AttachmentEntry(
                kind="file",
                label="file_1",
                name="a.csv",
                value="https://oss.example.com/chat/a.csv",
                source_event_id=10,
            ),
        )
    )
    extra = AttachmentEntry(
        kind="file",
        label="file_2",
        name="b.csv",
        value="https://oss.example.com/chat/b.csv",
        source_event_id=20,
    )

    extended = base.with_added((extra,))

    assert extended.entries == (base.entries[0], extra)
    assert extended is not base
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/sources/test_attachments.py -q
```

Expected: `ModuleNotFoundError: No module named 'matmaster.context.sources.attachments'`.

- [ ] **Step 3: Implement `matmaster/context/sources/attachments.py`**

Create `matmaster/context/sources/attachments.py`. Port the regex / URL parsing logic verbatim from `matmaster/manifests/attachment.py` so that legacy formatting (label numbering, URL normalization, basename extraction) is byte-equivalent.

```python
from __future__ import annotations

import posixpath
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, unquote, urlparse, urlunparse

from matmaster.context.ports import SessionEvent
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.utils.event_source import normalize_event_source

AttachmentKind = Literal["file", "image", "workspace"]

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class AttachmentEntry:
    kind: AttachmentKind
    label: str
    name: str
    value: str
    source_event_id: int | None = None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                out.append(stripped)
    return tuple(out)


def _name_from_url(value: str, fallback: str) -> str:
    parsed = urlparse(value)
    path = unquote(parsed.path or "")
    basename = posixpath.basename(path)
    return basename or fallback


def _entry_name(kind: AttachmentKind, value: str) -> str:
    if kind == "file":
        return _name_from_url(value, "file")
    if kind == "image":
        return _name_from_url(value, "image")
    return value


def _normalize_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return urlunparse(
        parsed._replace(path=quote(unquote(parsed.path or ""), safe="/"))
    )


def _normalize_value(kind: AttachmentKind, value: str) -> str:
    if kind in {"file", "image"}:
        return _normalize_url(value)
    return value


def _query_payload(event: SessionEvent) -> Mapping[str, object]:
    content = event.content
    if isinstance(content, Mapping):
        return content
    return {}


def _event_id_from_mapping(row: Mapping[str, object]) -> int | None:
    raw = row.get("id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _legacy_query_payload(row: Mapping[str, object]) -> dict[str, object]:
    """Preserve Phase 1 manifests/attachment payload semantics.

    ChatEventsTable.get_session_events() and several legacy tests pass
    display-flattened User/query rows where content is a string and
    files/images/workspace_paths were promoted to top-level keys. The typed
    context path never sees that shape, but the manifests shim must keep it.
    """
    payload: dict[str, object] = {}
    content = row.get("content")
    if isinstance(content, Mapping):
        payload.update(content)
    for key in ("files", "images", "workspace_paths"):
        if key in row:
            payload[key] = row.get(key)
    return payload


def _scan_payloads(
    payloads: Iterable[tuple[Mapping[str, object], int | None]],
    *,
    max_entries: int,
) -> tuple[AttachmentEntry, ...]:
    counters: dict[AttachmentKind, int] = {"file": 0, "image": 0, "workspace": 0}
    seen: set[tuple[AttachmentKind, str]] = set()
    entries: list[AttachmentEntry] = []

    def add(kind: AttachmentKind, value: str, source_event_id: int | None) -> None:
        if len(entries) >= max_entries:
            return
        normalized = _normalize_value(kind, value)
        key = (kind, normalized)
        if key in seen:
            return
        seen.add(key)
        counters[kind] += 1
        entries.append(
            AttachmentEntry(
                kind=kind,
                label=f"{kind}_{counters[kind]}",
                name=_entry_name(kind, normalized),
                value=normalized,
                source_event_id=source_event_id,
            )
        )

    for payload, source_event_id in payloads:
        if len(entries) >= max_entries:
            break
        for value in _string_tuple(payload.get("files")):
            add("file", value, source_event_id)
        for value in _string_tuple(payload.get("images")):
            add("image", value, source_event_id)
        for value in _string_tuple(payload.get("workspace_paths")):
            add("workspace", value, source_event_id)

    return tuple(entries)


def scan_attachment_entries(
    events: Iterable[SessionEvent],
    *,
    max_entries: int = 30,
) -> tuple[AttachmentEntry, ...]:
    """Walk events in arrival order, materializing AttachmentEntry tuples.

    Mirrors manifests/attachment.build_available_attachments semantics:
    only User/query events contribute; labels are 1-indexed per kind;
    duplicate (kind, normalized_value) is skipped; entries are capped at
    max_entries.
    """
    payloads: list[tuple[Mapping[str, object], int | None]] = []
    for event in events:
        if len(payloads) >= max_entries:
            break
        if event.source != "User":
            continue
        if event.event_type != "query":
            continue
        payloads.append((_query_payload(event), event.id))

    return _scan_payloads(payloads, max_entries=max_entries)


def scan_legacy_attachment_entries(
    rows: Iterable[Mapping[str, object]],
    *,
    max_entries: int = 30,
) -> tuple[AttachmentEntry, ...]:
    """Legacy adapter for matmaster.manifests.attachment shim only."""
    payloads: list[tuple[Mapping[str, object], int | None]] = []
    for row in rows:
        if len(payloads) >= max_entries:
            break
        if not isinstance(row, Mapping):
            continue
        if normalize_event_source(row.get("source")) != "User":
            continue
        if str(row.get("type") or "").strip() != "query":
            continue
        payloads.append((_legacy_query_payload(row), _event_id_from_mapping(row)))
    return _scan_payloads(payloads, max_entries=max_entries)


def filter_entries_in_event_range(
    entries: Iterable[AttachmentEntry],
    *,
    after_id: int | None,
    until_id: int | None,
) -> tuple[AttachmentEntry, ...]:
    if after_id is None and until_id is None:
        return tuple(entries)
    return tuple(
        entry
        for entry in entries
        if entry.source_event_id is not None
        and (after_id is None or entry.source_event_id > after_id)
        and (until_id is None or entry.source_event_id <= until_id)
    )


def filter_entries_after_event_id(
    entries: Iterable[AttachmentEntry],
    after_id: int | None,
) -> tuple[AttachmentEntry, ...]:
    return filter_entries_in_event_range(entries, after_id=after_id, until_id=None)


def format_entries_text(entries: Iterable[AttachmentEntry]) -> str:
    seq = tuple(entries)
    if not seq:
        return ""
    lines = ["[Available attachments]"]
    for entry in seq:
        if entry.kind == "workspace":
            lines.append(f"{entry.label} {entry.value}")
        else:
            lines.append(f"{entry.label} {entry.name} {entry.value}")
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionAttachmentsSource:
    entries: tuple[AttachmentEntry, ...] = ()

    @classmethod
    def from_events(
        cls,
        events: Iterable[SessionEvent],
        *,
        until_event_id: int | None = None,
        after_id: int | None = None,
        max_entries: int = 30,
    ) -> "SessionAttachmentsSource":
        raw_entries = scan_attachment_entries(events, max_entries=max_entries)
        scoped = filter_entries_in_event_range(
            raw_entries,
            after_id=after_id,
            until_id=until_event_id,
        )
        return cls(entries=scoped)

    def with_added(
        self, extra: Iterable[AttachmentEntry]
    ) -> "SessionAttachmentsSource":
        added = tuple(extra)
        if not added:
            return self
        return SessionAttachmentsSource(entries=(*self.entries, *added))

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = format_entries_text(self.entries)
        if not text:
            return ()
        return (
            ContextSection(
                key="session_attachments",
                tag="attachments",
                content=text,
                order=SectionOrder.SESSION_ATTACHMENTS,
                views=_VIEWS,
            ),
        )
```

- [ ] **Step 4: Verify tests are green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/sources/test_attachments.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Verify Phase 2A regressions still pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/context/sources/attachments.py tests/matmaster/context/sources/test_attachments.py && git commit -m "feat(context): add SessionAttachmentsSource with typed event scan

Adds matmaster/context/sources/attachments.py with AttachmentEntry,
scan_attachment_entries(SessionEvent), filter_entries_in_event_range,
format_entries_text, and SessionAttachmentsSource. Label numbering,
URL normalization, and text formatting are byte-equivalent to
manifests/attachment.py to support shim equivalence in Phase 2B."
```

---

## Task 4: Add Session Skills Source

**Files:**
- Create: `matmaster/context/sources/skills.py`
- Create: `tests/matmaster/context/sources/test_skills.py`

**Spec 依据:** DESIGN.md §5.1、§7.2（SessionSkillsSource, `order=SESSION_SKILLS=300`, views=RUNTIME+CHECKPOINT）、§14 Phase 2B、附录 B「Phase 2B 改动」。

`matmaster/context/sources/skills.py` 提供：
- `skill_name(skill) -> str`
- `resolve_active_skills(events: Iterable[SessionEvent], skill_registry) -> tuple[Any, ...]`
- `format_loaded_skills(skills) -> str`（保留 `[Loaded skills]` 前缀格式）
- `SessionSkillsSource` frozen dataclass，暴露 `to_sections()` 与 `from_events(events, *, skill_registry)`

- [ ] **Step 1: Write failing tests**

Create `tests/matmaster/context/sources/test_skills.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.context.scanner import coerce_session_events
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.skills import (
    SessionSkillsSource,
    format_loaded_skills,
    resolve_active_skills,
    skill_name,
)
from matmaster.skills.registry import SkillRegistry


def _registry(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    skill_dir = root / "pxrd"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pxrd\ndescription: PXRD helper\nmcp_server: mat_xrd\n---\nbody\n",
        encoding="utf-8",
    )
    other_dir = root / "mlip"
    other_dir.mkdir(parents=True)
    (other_dir / "SKILL.md").write_text(
        "---\nname: mlip\ndescription: MLIP runner\nmcp_server: mat_mlip\n---\nbody\n",
        encoding="utf-8",
    )
    return SkillRegistry([root])


def test_resolve_active_skills_returns_registered_skills_in_event_order(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    events = coerce_session_events(
        [
            {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
            {"id": 2, "type": "skill_hit", "content": {"skill_name": "mlip"}},
            {"id": 3, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
        ]
    )

    skills = resolve_active_skills(events, registry)

    names = tuple(skill_name(skill) for skill in skills)
    assert names == ("pxrd", "mlip")


def test_resolve_active_skills_handles_missing_registry_lookup(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    events = coerce_session_events(
        [
            {"id": 1, "type": "skill_hit", "content": {"skill_name": "unknown"}},
        ]
    )

    skills = resolve_active_skills(events, registry)

    assert skills == ()


def test_resolve_active_skills_with_none_registry_returns_empty() -> None:
    events = coerce_session_events(
        [{"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}}]
    )

    assert resolve_active_skills(events, None) == ()


def test_format_loaded_skills_emits_legacy_header(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    events = coerce_session_events(
        [{"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}}]
    )

    text = format_loaded_skills(resolve_active_skills(events, registry))

    assert text.startswith("[Loaded skills]\n")
    assert "- pxrd: PXRD helper (mcp_server=mat_xrd)" in text


def test_session_skills_source_to_sections(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    events = coerce_session_events(
        [{"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}}]
    )

    source = SessionSkillsSource.from_events(events, skill_registry=registry)
    sections = source.to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert isinstance(section, ContextSection)
    assert section.key == "session_skills"
    assert section.tag == "loaded_skills"
    assert section.order == SectionOrder.SESSION_SKILLS
    assert ContextView.RUNTIME in section.views and ContextView.CHECKPOINT in section.views
    assert "- pxrd: PXRD helper (mcp_server=mat_xrd)" in section.content


def test_session_skills_source_empty(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    source = SessionSkillsSource.from_events((), skill_registry=registry)
    assert source.to_sections() == ()


def test_session_skills_source_keeps_skills_for_downstream_tool_source(
    tmp_path: Path,
) -> None:
    """SessionToolsSource (Task 5) must consume the same resolved skills."""
    registry = _registry(tmp_path)
    events = coerce_session_events(
        [{"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}}]
    )

    source = SessionSkillsSource.from_events(events, skill_registry=registry)

    assert source.skills != ()
    assert skill_name(source.skills[0]) == "pxrd"
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/sources/test_skills.py -q
```

Expected: `ModuleNotFoundError: No module named 'matmaster.context.sources.skills'`.

- [ ] **Step 3: Implement `matmaster/context/sources/skills.py`**

Create `matmaster/context/sources/skills.py`:

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import scan_skill_hits
from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


def skill_name(skill: Any) -> str:
    return str(
        getattr(skill, "name", "")
        or getattr(getattr(skill, "meta_info", None), "name", "")
    ).strip()


def resolve_active_skills(
    events: Iterable[SessionEvent],
    skill_registry: Any,
) -> tuple[Any, ...]:
    if skill_registry is None:
        return ()
    resolved: list[Any] = []
    for record in scan_skill_hits(events):
        try:
            skill = skill_registry.get_skill(record.skill_name)
        except Exception:
            continue
        if skill is not None:
            resolved.append(skill)
    return tuple(resolved)


def format_loaded_skills(skills: Iterable[Any]) -> str:
    skill_tuple = tuple(skills)
    if not skill_tuple:
        return ""
    lines = ["[Loaded skills]"]
    for skill in skill_tuple:
        name = skill_name(skill)
        meta = getattr(skill, "meta_info", None)
        description = getattr(meta, "description", "") or ""
        mcp_server = getattr(meta, "mcp_server", None)
        suffix = f" (mcp_server={mcp_server})" if mcp_server else ""
        if description:
            lines.append(f"- {name}: {description}{suffix}")
        else:
            lines.append(f"- {name}{suffix}")
    return "\n".join(lines)


@dataclass(frozen=True)
class SessionSkillsSource:
    skills: tuple[Any, ...] = ()

    @classmethod
    def from_events(
        cls,
        events: Iterable[SessionEvent],
        *,
        skill_registry: Any,
    ) -> "SessionSkillsSource":
        return cls(skills=resolve_active_skills(events, skill_registry))

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
                views=_VIEWS,
            ),
        )
```

- [ ] **Step 4: Verify tests are green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/sources/test_skills.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/context/sources/skills.py tests/matmaster/context/sources/test_skills.py && git commit -m "feat(context): add SessionSkillsSource over typed events

Adds matmaster/context/sources/skills.py with skill_name,
resolve_active_skills(SessionEvent), format_loaded_skills, and the
SessionSkillsSource dataclass. Text formatting matches
manifests/skill.py byte-for-byte to support Phase 2B shim equivalence.
SessionSkillsSource.skills is kept on the dataclass so the upcoming
SessionToolsSource can derive its active MCP server set from the same
resolved skill objects."
```

---

## Task 5: Add Session Tools Source

**Files:**
- Create: `matmaster/context/sources/tools.py`
- Create: `tests/matmaster/context/sources/test_tools.py`

**Spec 依据:** DESIGN.md §5.1（`sources/tools.py` 替代 `mcp.py`）、§7.2（SessionToolsSource, `order=SESSION_TOOLS=400`, views=RUNTIME+CHECKPOINT）、§14 Phase 2B、附录 B「Phase 2B 改动」。

`matmaster/context/sources/tools.py` 暴露：
- `resolve_declared_servers(skills) -> set[str]`
- `resolve_runnable_servers(skills, *, legal_servers, schemas_by_server) -> set[str]`
- `format_active_mcp(skills, *, legal_servers, schemas_by_server) -> str`（保留 `[Active MCP servers]` 前缀格式）
- `SessionToolsSource` frozen dataclass，暴露 `to_sections()` 与 `from_skills(skills, *, legal_servers, schemas_by_server)`

- [ ] **Step 1: Write failing tests**

Create `tests/matmaster/context/sources/test_tools.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.tools import (
    SessionToolsSource,
    format_active_mcp,
    resolve_declared_servers,
    resolve_runnable_servers,
)


@dataclass(frozen=True)
class _Meta:
    name: str
    description: str = ""
    mcp_server: str | None = None


@dataclass(frozen=True)
class _Skill:
    meta_info: _Meta

    @property
    def name(self) -> str:
        return self.meta_info.name


def _skill(name: str, server: str | None) -> _Skill:
    return _Skill(meta_info=_Meta(name=name, mcp_server=server))


def test_resolve_declared_servers_dedup() -> None:
    skills = (_skill("a", "srv1"), _skill("b", "srv1"), _skill("c", None))
    assert resolve_declared_servers(skills) == {"srv1"}


def test_resolve_runnable_servers_filters_by_legal_and_schemas() -> None:
    skills = (_skill("a", "srv1"), _skill("b", "srv2"))

    runnable = resolve_runnable_servers(
        skills,
        legal_servers={"srv1"},
        schemas_by_server={"srv1": [{"name": "read"}], "srv2": [{"name": "x"}]},
    )

    assert runnable == {"srv1"}


def test_format_active_mcp_emits_legacy_header() -> None:
    skills = (_skill("a", "srv1"), _skill("b", "srv2"))
    text = format_active_mcp(
        skills,
        legal_servers={"srv1", "srv2"},
        schemas_by_server={
            "srv1": [{"name": "read"}],
            "srv2": [{"name": "write"}, {"name": "list"}],
        },
    )

    assert text.startswith("[Active MCP servers]\n")
    assert "- srv1: available" in text
    assert "  - srv1_read" in text
    assert "- srv2: available" in text
    assert "  - srv2_write" in text
    assert "  - srv2_list" in text


def test_format_active_mcp_marks_unavailable_when_no_schema() -> None:
    skills = (_skill("a", "srv1"),)

    text = format_active_mcp(
        skills,
        legal_servers={"srv1"},
        schemas_by_server={"srv1": []},
    )

    assert "- srv1: unavailable" in text


def test_session_tools_source_to_sections() -> None:
    skills = (_skill("a", "srv1"),)

    source = SessionToolsSource.from_skills(
        skills,
        legal_servers={"srv1"},
        schemas_by_server={"srv1": [{"name": "read"}]},
    )
    sections = source.to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert isinstance(section, ContextSection)
    assert section.key == "session_tools"
    assert section.tag == "active_tools"
    assert section.order == SectionOrder.SESSION_TOOLS
    assert ContextView.RUNTIME in section.views
    assert ContextView.CHECKPOINT in section.views
    assert "srv1_read" in section.content


def test_session_tools_source_empty_when_no_declared_servers() -> None:
    skills = (_skill("a", None),)
    source = SessionToolsSource.from_skills(
        skills,
        legal_servers=None,
        schemas_by_server=None,
    )
    assert source.to_sections() == ()
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/sources/test_tools.py -q
```

Expected: `ModuleNotFoundError: No module named 'matmaster.context.sources.tools'`.

- [ ] **Step 3: Implement `matmaster/context/sources/tools.py`**

Create `matmaster/context/sources/tools.py`:

```python
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


def _skill_mcp_server(skill: Any) -> str | None:
    raw = getattr(getattr(skill, "meta_info", None), "mcp_server", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def resolve_declared_servers(skills: Iterable[Any]) -> set[str]:
    return {
        server for skill in skills if (server := _skill_mcp_server(skill)) is not None
    }


def resolve_runnable_servers(
    skills: Iterable[Any],
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
            if isinstance(schemas_by_server.get(server), list)
            and len(schemas_by_server.get(server) or []) > 0
        }
    return runnable


def format_active_mcp(
    skills: Iterable[Any],
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
    skills: tuple[Any, ...] = ()
    legal_servers: frozenset[str] | None = None
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None

    @classmethod
    def from_skills(
        cls,
        skills: Iterable[Any],
        *,
        legal_servers: set[str] | None,
        schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
    ) -> "SessionToolsSource":
        return cls(
            skills=tuple(skills),
            legal_servers=frozenset(legal_servers) if legal_servers is not None else None,
            schemas_by_server=schemas_by_server,
        )

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = format_active_mcp(
            self.skills,
            legal_servers=set(self.legal_servers) if self.legal_servers is not None else None,
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
                views=_VIEWS,
            ),
        )
```

- [ ] **Step 4: Verify tests are green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/sources/test_tools.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/context/sources/tools.py tests/matmaster/context/sources/test_tools.py && git commit -m "feat(context): add SessionToolsSource replacing manifests/mcp

Adds matmaster/context/sources/tools.py with resolve_declared_servers,
resolve_runnable_servers, format_active_mcp, and SessionToolsSource.
Text formatting matches manifests/mcp.py byte-for-byte. Per DESIGN.md
section 5.1 the new module replaces the legacy 'mcp.py' name with
'tools.py' to align with the active-tools section tag."
```

---

## Task 6: Add SessionContextBuilder

**Files:**
- Create: `matmaster/context/session.py`
- Create: `tests/matmaster/context/test_session.py`

**Spec 依据:** DESIGN.md §5.1、§7.3「SessionContextBuilder」、§16 `test_session.py`、§14 Phase 2B、附录 B「Phase 2B 改动」、§6bis.4 `_step_session_sections` 期望传入预装配 sections。

`matmaster/context/session.py::SessionContextBuilder` 是 events → session-level ContextSection 的唯一装配点。接受 typed `tuple[SessionEvent, ...]`，内部不持有 service / port 对象；构造时只接外部已 resolve 的 skill registry / legal mcp servers / schemas。

- [ ] **Step 1: Write failing tests**

Create `tests/matmaster/context/test_session.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.context.scanner import coerce_session_events
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.session import SessionContextBuilder
from matmaster.skills.registry import SkillRegistry


def _registry(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    skill_dir = root / "pxrd"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pxrd\ndescription: PXRD helper\nmcp_server: mat_xrd\n---\nbody\n",
        encoding="utf-8",
    )
    return SkillRegistry([root])


_BASE_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {
            "content": "first turn",
            "files": ["https://oss.example.com/a.csv"],
        },
    },
    {"id": 11, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
    {
        "id": 20,
        "source": "User",
        "type": "query",
        "content": {
            "content": "second turn",
            "files": ["https://oss.example.com/b.csv"],
        },
    },
]


def test_build_sections_returns_attachments_skills_tools_in_order(
    tmp_path: Path,
) -> None:
    builder = SessionContextBuilder(
        events=coerce_session_events(_BASE_EVENTS),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)

    keys = tuple(section.key for section in sections)
    assert "session_skills" in keys
    assert "session_tools" in keys
    assert "session_attachments" in keys


def test_build_sections_until_event_id_truncates_attachments(
    tmp_path: Path,
) -> None:
    builder = SessionContextBuilder(
        events=coerce_session_events(_BASE_EVENTS),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=10, include_attachments=True)
    attachments = next(s for s in sections if s.key == "session_attachments")

    assert "a.csv" in attachments.content
    assert "b.csv" not in attachments.content


def test_build_sections_exclude_attachments_drops_section(
    tmp_path: Path,
) -> None:
    builder = SessionContextBuilder(
        events=coerce_session_events(_BASE_EVENTS),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=False)

    keys = tuple(section.key for section in sections)
    assert "session_attachments" not in keys


def test_build_sections_empty_inputs_returns_empty_tuple(tmp_path: Path) -> None:
    builder = SessionContextBuilder(
        events=(),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers=None,
        schemas_by_server=None,
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)

    assert sections == ()


def test_constructor_rejects_list_input_to_enforce_typed_envelope(
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="tuple"):
        SessionContextBuilder(
            events=list(coerce_session_events(_BASE_EVENTS)),  # type: ignore[arg-type]
            skill_registry=_registry(tmp_path),
            legal_mcp_servers=None,
            schemas_by_server=None,
        )


def test_sections_are_in_section_order_after_render_sort(tmp_path: Path) -> None:
    """SectionOrder enum sorts <loaded_skills> before <active_tools>
    before <attachments> per DESIGN.md section 7.2."""
    builder = SessionContextBuilder(
        events=coerce_session_events(_BASE_EVENTS),
        skill_registry=_registry(tmp_path),
        legal_mcp_servers={"mat_xrd"},
        schemas_by_server={"mat_xrd": [{"name": "read"}]},
    )

    sections = builder.build_sections(until_event_id=None, include_attachments=True)
    orders = [section.order for section in sections]
    assert orders == sorted(orders), (
        "SessionContextBuilder should emit sections in SectionOrder ascending order"
    )
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_session.py -q
```

Expected: `ModuleNotFoundError: No module named 'matmaster.context.session'`.

- [ ] **Step 3: Implement `matmaster/context/session.py`**

Create `matmaster/context/session.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import SessionEvent
from matmaster.context.sections import ContextSection
from matmaster.context.sources.attachments import SessionAttachmentsSource
from matmaster.context.sources.skills import SessionSkillsSource
from matmaster.context.sources.tools import SessionToolsSource


@dataclass(frozen=True)
class SessionContextBuilder:
    """Compose session-level sections from a typed SessionEvent tuple.

    The constructor signature is intentionally tight:

    - events: tuple[SessionEvent, ...] — typed envelope (DESIGN.md section 7.3
      v3.1 change). Plain list inputs are rejected to prevent legacy
      list[dict[str, Any]] from sneaking through and bypassing the
      coerce_session_events boundary.
    - skill_registry: SkillRegistry-shaped object with get_skill(name);
      can be None for tests that only need attachments.
    - legal_mcp_servers / schemas_by_server: same shape as the legacy
      CompactionRehydrator inputs. None means "no filter".

    build_sections(until_event_id, include_attachments) is the single
    entry point used by ContextAssembler and by the manifests/rehydrator
    shim. include_attachments=False removes the SessionAttachmentsSource
    output entirely (used by compaction.session_attachments_override).
    """

    events: tuple[SessionEvent, ...]
    skill_registry: Any
    legal_mcp_servers: set[str] | None = None
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple):
            raise TypeError(
                "SessionContextBuilder.events must be a tuple of SessionEvent; "
                "convert via matmaster.context.scanner.coerce_session_events first"
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

        skills_source = SessionSkillsSource.from_events(
            scoped_events,
            skill_registry=self.skill_registry,
        )
        tools_source = SessionToolsSource.from_skills(
            skills_source.skills,
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

- [ ] **Step 4: Verify tests are green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_session.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Run cumulative Phase 2A + 2B-so-far suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/context/session.py tests/matmaster/context/test_session.py && git commit -m "feat(context): add SessionContextBuilder over typed events

Adds matmaster/context/session.py::SessionContextBuilder, the single
assembly point that turns a typed SessionEvent tuple plus skill
registry / legal mcp servers / schemas into the
loaded_skills/active_tools/attachments section trio per DESIGN.md
section 7.3. The constructor rejects list inputs to enforce the
SessionEvent envelope boundary (hard invariant section 4.2 #10)."
```

---

## Task 7: Wire SessionContextBuilder Into ContextAssembler

**Files:**
- Modify: `matmaster/context/assembly.py`
- Modify: `tests/matmaster/context/test_assembly.py`

**Spec 依据:** DESIGN.md §7bis.3 (`ContextAssembler` 调 `SessionContextBuilder`)、§14 Phase 2B 验收「新 session builder 能从 typed events 生成 sections」、PHASE-2A-PLAN.md「Notes For Phase 2B」（add production seam for real builder, keep empty default and test seam until Phase 2C runtime injection）。

Phase 2A 在 `matmaster/context/assembly.py` 留下 `_session_section_builder_for_tests` seam，默认是 `_empty_session_section_builder`（返回 ()）。Phase 2B 不直接替换这个默认值，而是新增一个 production seam：`ContextAssembler.__init__` 接受可选参数 `session_context_factory: Callable[[tuple[SessionEvent, ...]], SessionContextBuilder] | None = None`。当 factory 存在时，assembler 用它构造真实 `SessionContextBuilder`；当 factory 不存在时，继续返回空 session sections，保证 Phase 2B 不提前改变 runtime 默认行为。Phase 2C runtime cutover 再由 service 层注入带 skill registry / legal mcp servers / schemas 的 factory。

- [ ] **Step 1: Read current `matmaster/context/assembly.py`**

Read [matmaster/context/assembly.py](../../matmaster/context/assembly.py) to confirm the Phase 2A shape:
- `_empty_session_section_builder(events, until_event_id, include_attachments)` returns `()`
- `ContextAssembler.__init__(self, ports, *, _session_section_builder_for_tests=None)` stores `self._session_section_builder = _session_section_builder_for_tests or _empty_session_section_builder`
- `assemble_turn` (ANCHOR branch) calls `self._session_section_builder(events, history_boundary, True)`
- `assemble_compaction` calls `self._session_section_builder(events, covered_until, request.session_attachments_override is None)`

- [ ] **Step 2: Write failing assembler tests for `session_context_factory`**

Append to `tests/matmaster/context/test_assembly.py`:

```python
# ---------- Phase 2B additions ----------

from pathlib import Path

from matmaster.context.session import SessionContextBuilder
from matmaster.skills.registry import SkillRegistry


def _skill_registry(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    skill_dir = root / "pxrd"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pxrd\ndescription: PXRD helper\nmcp_server: mat_xrd\n---\nbody\n",
        encoding="utf-8",
    )
    return SkillRegistry([root])


class _RecordingEventsPort:
    def __init__(self, events: tuple[SessionEvent, ...]) -> None:
        self._events = events
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        return self._events


@pytest.mark.asyncio
async def test_assemble_turn_anchor_uses_session_context_factory(
    tmp_path: Path,
) -> None:
    registry = _skill_registry(tmp_path)
    events = (
        SessionEvent(
            id=1,
            event_type="query",
            source="User",
            content={"files": ("https://oss.example.com/a.csv",)},
        ),
        SessionEvent(
            id=2,
            event_type="skill_hit",
            source="System",
            content={"skill_name": "pxrd"},
        ),
    )
    port = _RecordingEventsPort(events)

    def factory(loaded_events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=loaded_events,
            skill_registry=registry,
            legal_mcp_servers={"mat_xrd"},
            schemas_by_server={"mat_xrd": [{"name": "read"}]},
        )

    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=port),
        session_context_factory=factory,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="hi"),
                pre_turn_history_event_id=2,
            ),
            user_instructions=UserInstructions(text="Use SI.", hash="sha256:abc"),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<loaded_skills>" in runtime
    assert "<active_tools>" in runtime
    assert "<attachments>" in runtime
    assert port.queries[0].until_event_id == 2


@pytest.mark.asyncio
async def test_assemble_turn_continuation_does_not_invoke_session_factory(
    tmp_path: Path,
) -> None:
    call_count = 0

    def factory(_events):
        nonlocal call_count
        call_count += 1
        return SessionContextBuilder(
            events=(),
            skill_registry=None,
            legal_mcp_servers=None,
            schemas_by_server=None,
        )

    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=_RecordingEventsPort(())),
        session_context_factory=factory,
    )

    await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="continue"),
                pre_turn_history_event_id=2,
            ),
            user_instructions=UserInstructions(text="Use SI.", hash="sha256:abc"),
        ),
    )

    assert call_count == 0


@pytest.mark.asyncio
async def test_assembler_default_session_factory_returns_empty_sections() -> None:
    """When no factory is injected, ContextAssembler must still return a valid
    UserTurnContext (legacy 2A behaviour). Phase 2C runtime wiring will
    always inject a factory."""
    port = _RecordingEventsPort(())
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=port))

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="hi"),
                pre_turn_history_event_id=0,
            ),
            user_instructions=UserInstructions(text="Use SI.", hash="sha256:abc"),
        ),
    )

    runtime = result.user_turn_context.render(ContextView.RUNTIME)
    assert "<loaded_skills>" not in runtime
    assert "<active_tools>" not in runtime
```

- [ ] **Step 3: Verify new tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_assembly.py -q
```

Expected: the three new tests fail with `TypeError: __init__() got an unexpected keyword argument 'session_context_factory'` or equivalent.

- [ ] **Step 4: Modify `matmaster/context/assembly.py`**

In [matmaster/context/assembly.py](../../matmaster/context/assembly.py):

1. Add a new import:

```python
from matmaster.context.session import SessionContextBuilder
```

2. Extend the `SessionSectionBuilder` type alias and add `SessionContextFactory`:

```python
SessionContextFactory = Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]
```

3. Update `ContextAssembler.__init__` signature to accept the factory; the existing `_session_section_builder_for_tests` seam stays exactly as is for unit tests:

```python
class ContextAssembler:
    def __init__(
        self,
        ports: ContextAssemblyPorts,
        *,
        session_context_factory: SessionContextFactory | None = None,
        _session_section_builder_for_tests: SessionSectionBuilder | None = None,
    ) -> None:
        self._ports = ports
        self._session_context_factory = session_context_factory
        # Test-only seam from Phase 2A: production runtime wiring must use
        # session_context_factory instead of injecting prebuilt sections.
        if _session_section_builder_for_tests is not None:
            self._session_section_builder: SessionSectionBuilder = (
                _session_section_builder_for_tests
            )
        elif session_context_factory is not None:
            self._session_section_builder = self._build_via_factory
        else:
            self._session_section_builder = _empty_session_section_builder

    def _build_via_factory(
        self,
        events: tuple[SessionEvent, ...],
        until_event_id: int,
        include_attachments: bool,
    ) -> tuple[ContextSection, ...]:
        assert self._session_context_factory is not None
        builder = self._session_context_factory(events)
        return builder.build_sections(
            until_event_id=until_event_id,
            include_attachments=include_attachments,
        )
```

4. Leave `_empty_session_section_builder` and its docstring intact; expand the docstring to say "default kept for the no-factory tests case; runtime wiring must inject session_context_factory in Phase 2C".

- [ ] **Step 5: Verify tests are green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_assembly.py -q
```

Expected: all tests pass (including the original 2A seam tests).

- [ ] **Step 6: Run cumulative suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/context/assembly.py tests/matmaster/context/test_assembly.py && git commit -m "feat(context): wire SessionContextBuilder factory into ContextAssembler

Adds session_context_factory keyword to ContextAssembler.__init__ so
that anchor / compaction intents can delegate session-section assembly
to a real SessionContextBuilder once Phase 2C injects one. The
_session_section_builder_for_tests seam stays as a unit-test escape
hatch; the empty default is preserved so Phase 2A tests that
instantiate ContextAssembler without arguments continue to pass."
```

---

## Task 8: Add ModelHistoryRestorer Pure Algorithm

**Files:**
- Create: `matmaster/context/history_restore.py`
- Create: `tests/matmaster/context/test_history_restore.py`

**Spec 依据:** DESIGN.md §11.1 v3.3 三分支算法、§11.2 / §11.2.1 hybrid v1 与混合 session、§11.3 service DI 实现、§11.4 多次压缩、§14 Phase 2B 新增 `history_restore.py`、§16 `test_history_restore.py`、附录 B「Phase 2B 改动」。

`matmaster/context/history_restore.py` 暴露纯算法 `ModelHistoryRestorer`，通过 callable 注入 events 访问能力（DESIGN §11.1 第一段）。算法分三支：纯 v1（v1 checkpoint） / hybrid v1（无 v1 checkpoint 但 events 含 `user_turn_context`） / 纯 v0（既无 v1 checkpoint 也无 `user_turn_context` → 委托 legacy restore）。

- [ ] **Step 1: Write failing tests**

Create `tests/matmaster/context/test_history_restore.py`:

```python
from __future__ import annotations

from typing import Any

import pytest

from matmaster.context.history_restore import ModelHistoryRestorer
from matmaster.types.messages import (
    AssistantMessage,
    Message,
    ToolMessage,
    UserMessage,
)


def _build(
    *,
    checkpoint: dict[str, Any] | None = None,
    events_after: list[dict[str, Any]] | None = None,
    has_utc: bool = False,
    legacy: list[Message] | None = None,
):
    calls: dict[str, list] = {
        "checkpoint": [],
        "events_after": [],
        "has_utc": [],
        "legacy": [],
    }

    def get_latest_checkpoint(session_id: str, spawn_id: str | None) -> dict | None:
        calls["checkpoint"].append((session_id, spawn_id))
        return checkpoint

    def get_events_after(
        session_id: str,
        after_id: int | None,
        spawn_id: str | None,
    ) -> list[dict]:
        calls["events_after"].append((session_id, after_id, spawn_id))
        return events_after or []

    def has_user_turn_context(session_id: str, spawn_id: str | None) -> bool:
        calls["has_utc"].append((session_id, spawn_id))
        return has_utc

    def legacy_restore(session_id: str, spawn_id: str | None) -> list[Message]:
        calls["legacy"].append((session_id, spawn_id))
        return legacy or []

    def deserialize_base_messages(raw: list[dict[str, Any]]) -> list[Message]:
        return [
            UserMessage.model_validate(item)
            if item.get("role") == "user"
            else AssistantMessage.model_validate(item)
            for item in raw
            if item.get("role") in {"user", "assistant"}
        ]

    def events_to_messages(events: list[dict[str, Any]]) -> list[Message]:
        messages: list[Message] = []
        for event in events:
            etype = event.get("type")
            payload = event.get("content") or {}
            if event.get("source") == "User" and etype == "query":
                messages.append(UserMessage(content=str(payload.get("content") or "")))
            elif etype in {"response", "run_result", "finish"}:
                messages.append(AssistantMessage(content=str(payload.get("content") or "")))
            elif etype == "assistant_state":
                state = payload.get("state") or payload
                messages.append(AssistantMessage.model_validate(state))
            elif etype == "tool_result":
                messages.append(
                    ToolMessage(
                        content=str(payload.get("result", "")),
                        tool_call_id=str(payload.get("id") or payload.get("call_id") or ""),
                        tool_name=str(payload.get("name") or payload.get("tool_name") or ""),
                    )
                )
        return messages

    def normalize_tool_result_event(event: dict[str, Any]) -> dict[str, Any]:
        content = dict(event.get("content") or {})
        if "id" not in content and content.get("call_id"):
            content["id"] = content["call_id"]
        if "name" not in content and content.get("tool_name"):
            content["name"] = content["tool_name"]
        return {**event, "content": content}

    restorer = ModelHistoryRestorer(
        get_latest_checkpoint=get_latest_checkpoint,
        get_events_after=get_events_after,
        has_user_turn_context=has_user_turn_context,
        legacy_restore=legacy_restore,
        deserialize_base_messages=deserialize_base_messages,
        events_to_messages=events_to_messages,
        normalize_tool_result_event=normalize_tool_result_event,
    )
    return restorer, calls


def test_restore_pure_v0_delegates_to_legacy() -> None:
    restorer, calls = _build(
        checkpoint=None,
        has_utc=False,
        legacy=[UserMessage(content="legacy")],
    )

    result = restorer.restore("sess-1")

    assert len(result) == 1
    assert isinstance(result[0], UserMessage)
    assert result[0].content == "legacy"
    assert calls["legacy"] == [("sess-1", None)]
    assert calls["events_after"] == []


def test_restore_v0_checkpoint_falls_back_to_legacy() -> None:
    """A checkpoint whose schema_version is not history_checkpoint.v1 must
    still be treated as 'no v1 checkpoint'. Phase 1 still writes v0
    markers, so this is the dominant production case."""
    restorer, calls = _build(
        checkpoint={"content": {"schema_version": "checkpoint.v0"}, "id": 99},
        has_utc=False,
        legacy=[UserMessage(content="legacy")],
    )

    result = restorer.restore("sess-1")

    assert len(result) == 1
    assert calls["legacy"] == [("sess-1", None)]


def test_restore_hybrid_v1_consumes_uncovered_user_query() -> None:
    events = [
        {
            "id": 5,
            "type": "query",
            "source": "User",
            "content": {"content": "pre-Phase-1 turn"},
            "invocation_id": "inv-old",
        },
        {
            "id": 6,
            "type": "response",
            "content": {"content": "old response"},
        },
        {
            "id": 7,
            "type": "user_turn_context",
            "invocation_id": "inv-new",
            "content": {
                "message": {
                    "role": "user",
                    "content": "rendered new turn",
                }
            },
        },
    ]
    restorer, calls = _build(checkpoint=None, events_after=events, has_utc=True)

    result = restorer.restore("sess-1")

    contents = [m.content for m in result]
    assert "pre-Phase-1 turn" in contents
    assert "old response" in contents
    assert "rendered new turn" in contents
    assert calls["legacy"] == []


def test_restore_hybrid_v1_skips_user_query_covered_by_utc() -> None:
    events = [
        {
            "id": 5,
            "type": "query",
            "source": "User",
            "content": {"content": "old raw"},
            "invocation_id": "inv-1",
        },
        {
            "id": 6,
            "type": "user_turn_context",
            "invocation_id": "inv-1",
            "content": {
                "message": {"role": "user", "content": "rendered with anchor"},
            },
        },
    ]
    restorer, _ = _build(checkpoint=None, events_after=events, has_utc=True)

    result = restorer.restore("sess-1")

    contents = [m.content for m in result]
    assert "old raw" not in contents
    assert "rendered with anchor" in contents


def test_restore_pure_v1_loads_base_messages_and_skips_user_query() -> None:
    checkpoint = {
        "id": 99,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": 50,
            "base_messages": [
                {"role": "user", "content": "summary as user"},
            ],
        },
    }
    events_after = [
        {
            "id": 51,
            "type": "query",
            "source": "User",
            "content": {"content": "should be skipped"},
            "invocation_id": "inv-after-checkpoint",
        },
        {
            "id": 52,
            "type": "user_turn_context",
            "invocation_id": "inv-after-checkpoint",
            "content": {
                "message": {"role": "user", "content": "rendered after checkpoint"},
            },
        },
        {
            "id": 53,
            "type": "tool_result",
            "content": {"result": "tool out", "call_id": "c1", "tool_name": "t"},
        },
    ]
    restorer, calls = _build(checkpoint=checkpoint, events_after=events_after)

    result = restorer.restore("sess-1")

    assert calls["legacy"] == []
    assert calls["events_after"][0] == ("sess-1", 50, None)
    contents = [m.content for m in result]
    assert contents[0] == "summary as user"
    assert "should be skipped" not in contents
    assert "rendered after checkpoint" in contents
    assert any(isinstance(m, ToolMessage) and m.content == "tool out" for m in result)


def test_restore_pure_v1_with_null_covered_until_falls_back_to_legacy() -> None:
    """DESIGN section 11.1 second paragraph (v3.3 fix): covered_until_event_id
    == None on a v1 checkpoint is treated as corruption; restore reverts to
    legacy to avoid silently replaying the full event log."""
    checkpoint = {
        "id": 99,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": None,
            "base_messages": [],
        },
    }
    restorer, calls = _build(
        checkpoint=checkpoint,
        legacy=[UserMessage(content="from legacy")],
    )

    result = restorer.restore("sess-1")

    assert len(result) == 1
    assert result[0].content == "from legacy"


def test_restore_pure_v1_consumes_assistant_state_and_response() -> None:
    checkpoint = {
        "id": 99,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": 0,
            "base_messages": [],
        },
    }
    events_after = [
        {
            "id": 1,
            "type": "user_turn_context",
            "invocation_id": "inv-1",
            "content": {
                "message": {"role": "user", "content": "ask"},
            },
        },
        {
            "id": 2,
            "type": "response",
            "content": {"content": "natural reply"},
        },
        {
            "id": 3,
            "type": "user_turn_context",
            "invocation_id": "inv-2",
            "content": {"message": {"role": "user", "content": "next ask"}},
        },
        {
            "id": 4,
            "type": "assistant_state",
            "content": {
                "state": {
                    "role": "assistant",
                    "content": "calling tool",
                    "tool_calls": [],
                }
            },
        },
    ]
    restorer, _ = _build(checkpoint=checkpoint, events_after=events_after)

    result = restorer.restore("sess-1")

    user_msgs = [m for m in result if isinstance(m, UserMessage)]
    asst_msgs = [m for m in result if isinstance(m, AssistantMessage)]

    assert [m.content for m in user_msgs] == ["ask", "next ask"]
    assert [m.content for m in asst_msgs] == ["natural reply", "calling tool"]


def test_restore_passes_spawn_id_to_callbacks() -> None:
    restorer, calls = _build(
        checkpoint=None,
        has_utc=False,
        legacy=[],
    )

    restorer.restore("sess-1", spawn_id="spawn-A")

    assert calls["legacy"] == [("sess-1", "spawn-A")]
    assert calls["has_utc"] == [("sess-1", "spawn-A")]
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_history_restore.py -q
```

Expected: `ModuleNotFoundError: No module named 'matmaster.context.history_restore'`.

- [ ] **Step 3: Implement `matmaster/context/history_restore.py`**

Create `matmaster/context/history_restore.py` following DESIGN.md §11.1 v3.3:

```python
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from matmaster.types.messages import (
    Message,
    UserMessage,
)

logger = logging.getLogger(__name__)


_V1_SCHEMA = "history_checkpoint.v1"


GetLatestCheckpoint = Callable[[str, str | None], dict[str, Any] | None]
GetEventsAfter = Callable[[str, int | None, str | None], list[dict[str, Any]]]
HasUserTurnContext = Callable[[str, str | None], bool]
LegacyRestore = Callable[[str, str | None], list[Message]]
DeserializeBaseMessages = Callable[[list[dict[str, Any]]], list[Message]]
EventsToMessages = Callable[[list[dict[str, Any]]], list[Message]]
NormalizeToolResultEvent = Callable[[dict[str, Any]], dict[str, Any]]
ValidateHistory = Callable[[list[Message]], None]


class ModelHistoryRestorer:
    """Rebuild backend-visible LLM history from session events.

    All DB / service access is injected as callbacks so this module stays a
    pure algorithm (DESIGN.md section 11.1). Phase 2B keeps the v3.3 three-
    branch dispatch:

    - pure v1: checkpoint exists and schema_version == history_checkpoint.v1
    - hybrid v1: no v1 checkpoint but events contain user_turn_context
    - pure v0: no v1 checkpoint and no user_turn_context -> legacy restore

    covered_until_event_id == None on a v1 checkpoint is treated as
    corruption per section 11.1 second paragraph (v3.3 fix); the restorer
    logs a warning and falls back to legacy to avoid silently replaying
    every event.
    """

    def __init__(
        self,
        *,
        get_latest_checkpoint: GetLatestCheckpoint,
        get_events_after: GetEventsAfter,
        has_user_turn_context: HasUserTurnContext,
        legacy_restore: LegacyRestore,
        deserialize_base_messages: DeserializeBaseMessages,
        events_to_messages: EventsToMessages,
        normalize_tool_result_event: NormalizeToolResultEvent,
        validate_history: ValidateHistory | None = None,
    ) -> None:
        self._get_latest_checkpoint = get_latest_checkpoint
        self._get_events_after = get_events_after
        self._has_user_turn_context = has_user_turn_context
        self._legacy_restore = legacy_restore
        self._deserialize_base_messages = deserialize_base_messages
        self._events_to_messages = events_to_messages
        self._normalize_tool_result_event = normalize_tool_result_event
        self._validate_history = validate_history

    def restore(
        self,
        session_id: str,
        *,
        spawn_id: str | None = None,
    ) -> list[Message]:
        checkpoint = self._get_latest_checkpoint(session_id, spawn_id)
        schema_v1 = self._is_v1_checkpoint(checkpoint)

        if not schema_v1:
            if not self._has_user_turn_context(session_id, spawn_id):
                # COMPAT:v0-restore
                return self._legacy_restore(session_id, spawn_id)
            return self._restore_v1(
                session_id=session_id,
                spawn_id=spawn_id,
                checkpoint=None,
            )

        assert checkpoint is not None
        content = checkpoint["content"]
        covered = content.get("covered_until_event_id")
        if covered is None:
            logger.warning(
                "history_checkpoint.v1 has null covered_until_event_id; "
                "falling back to legacy restore (COMPAT:v0-restore)"
            )
            return self._legacy_restore(session_id, spawn_id)

        return self._restore_v1(
            session_id=session_id,
            spawn_id=spawn_id,
            checkpoint=checkpoint,
        )

    @staticmethod
    def _is_v1_checkpoint(checkpoint: dict[str, Any] | None) -> bool:
        if checkpoint is None:
            return False
        content = checkpoint.get("content")
        if not isinstance(content, dict):
            return False
        return content.get("schema_version") == _V1_SCHEMA

    def _restore_v1(
        self,
        *,
        session_id: str,
        spawn_id: str | None,
        checkpoint: dict[str, Any] | None,
    ) -> list[Message]:
        if checkpoint is not None:
            content = checkpoint["content"]
            after = int(content["covered_until_event_id"])
            base_messages = self._deserialize_base_messages(
                content.get("base_messages") or []
            )
            hybrid_mode = False
        else:
            base_messages = []
            after = None
            hybrid_mode = True

        events = self._get_events_after(session_id, after, spawn_id)

        covered_invocations: set[str] = set()
        if hybrid_mode:
            for ev in events:
                if ev.get("type") == "user_turn_context":
                    inv = ev.get("invocation_id")
                    if inv:
                        covered_invocations.add(str(inv))

        compatible_tail_events: list[dict[str, Any]] = []
        for event in events:
            compatible = self._event_to_v1_compatible_event(
                event,
                hybrid_mode=hybrid_mode,
                covered_invocations=covered_invocations,
            )
            if compatible is not None:
                compatible_tail_events.append(compatible)

        tail_messages = self._events_to_messages(compatible_tail_events)
        history = [*base_messages, *tail_messages]
        if base_messages and self._validate_history is not None:
            self._validate_history(history)
        return history

    def _event_to_v1_compatible_event(
        self,
        event: dict[str, Any],
        *,
        hybrid_mode: bool,
        covered_invocations: set[str],
    ) -> dict[str, Any] | None:
        etype = (event.get("type") or "").strip()
        source = str(event.get("source") or "").strip()
        payload = event.get("content")

        if etype == "user_turn_context":
            if not isinstance(payload, dict):
                return None
            raw_message = payload.get("message")
            if not isinstance(raw_message, dict):
                return None
            message = UserMessage.model_validate(raw_message)
            image_urls = [image.url for image in message.images if image.url]
            return {
                **event,
                "source": "User",
                "type": "query",
                "content": {
                    "content": message.content or "",
                    "images": image_urls,
                },
                "images": image_urls,
            }

        if source == "User" and etype == "query":
            if not hybrid_mode:
                return None
            inv = event.get("invocation_id")
            if inv and str(inv) in covered_invocations:
                return None
            return event

        if etype == "tool_result":
            return self._normalize_tool_result_event(event)

        if etype in {
            "assistant_state",
            "response",
            "run_result",
            "finish",
            "tool_call",
        }:
            return event

        if etype in {
            "thought",
            "skill_hit",
            "compaction",
            "history_checkpoint",
            "context_compaction",
        }:
            return None

        return None
```

- [ ] **Step 4: Verify tests are green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_history_restore.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/context/history_restore.py tests/matmaster/context/test_history_restore.py && git commit -m "feat(context): add ModelHistoryRestorer pure algorithm

Adds matmaster/context/history_restore.py with the v3.3 three-branch
schema-aware restore dispatch (pure v0 / hybrid v1 / pure v1). All DB
access is injected via callbacks so the module stays a pure algorithm.
A null covered_until_event_id on a v1 checkpoint falls back to legacy
restore per DESIGN.md section 11.1 v3.3 to avoid silently replaying
the full event log."
```

---

## Task 9: Rewire Service ModelHistoryRestoreService To Delegate

**Files:**
- Modify: `src/services/model_history_restore_service.py`
- Modify Test: `tests/matmaster/services/test_model_history_restore_service.py`（追加委托验证与 Phase 1 byte-equivalence guards；现有用例不修改、不削弱）

**Spec 依据:** DESIGN.md §11.3 service DI 实现、§5.3 删除/迁移清单、§14 Phase 2B、附录 B「Phase 2B 改动」。

`src/services/model_history_restore_service.py` 在 Phase 1 内已完成 v0/hybrid/v1 三分支实现。Phase 2B 任务是把算法核心切到调用 `ModelHistoryRestorer`，service 文件只保留：
1. `restore_history()` public API（被 `agent_run_history_wiring.py` 使用，签名不变）
2. checkpoint 选最新 + 多 checkpoint 兜底 fallback（DESIGN §11.4 注：最新 checkpoint 已合并旧 summary，不叠加）
3. `_normalize_tool_result_event` 等 service-specific helper（如果 Phase 1 留下）
4. `trim_history_images` / `exclude_spawn_events` / `exclude_task_events` 三个 service 边界过滤
5. legacy restore wrapping `ChatHistoryConverter.events_to_dialog_messages`

切换后 `restore_history(session_id=..., spawn_id=..., task_id=..., raw_limit=...)` 的返回值必须与 Phase 1 末态字节等价。

- [ ] **Step 1: Read Phase 1 implementation**

Read [src/services/model_history_restore_service.py](../../src/services/model_history_restore_service.py) end-to-end. Identify:
- `restore_history` outer dispatch (checkpoint loop + hybrid check + COMPAT:v0-restore fallback)
- `_restore_v1` algorithm
- `_event_to_v1_compatible_event` translator
- `_restore_legacy`
- `_v1_checkpoints` helper
- `_session_has_user_turn_context`
- `_normalize_tool_result_event`

The Phase 2B refactor keeps the file but delegates the algorithm portion to `ModelHistoryRestorer`. The service layer still owns:
- multi-checkpoint fallback (try `v1_checkpoints[0]`, then `v1_checkpoints[1]`, ... per Phase 1 code path)
- raw event log loading via `events_table.get_scope_events_after_id`
- `exclude_task_events` / `exclude_spawn_events` filtering
- `trim_history_images`

The cleanest approach is to make `ModelHistoryRestorer` the source of truth for schema dispatch and hybrid coverage, while service remains the compatibility adapter for Phase 1 message codecs. Concretely:

- `restore_history(...)` keeps the public signature and multi-checkpoint fallback loop.
- For each v1 checkpoint candidate, service constructs a fresh `ModelHistoryRestorer` whose `get_latest_checkpoint` callback returns that checkpoint.
- For the no-v1-checkpoint case, service calls the same delegate once with `checkpoint=None`; restorer itself calls `has_user_turn_context` and decides hybrid v1 vs pure v0, so the service does not query `has_user_turn_context` twice.
- Service injects `deserialize_base_messages`, `validate_base_messages`, `ChatHistoryConverter.events_to_messages`, and `_normalize_tool_result_event` into the restorer. These are not optional niceties: they preserve Phase 1 byte-equivalence for checkpoint marker validation, assistant/tool-call pairing, orphan tool-result skipping, and JSON encoding of non-string tool results.
- `trim_history_images` is called exactly once at the outer `restore_history` boundary. Add an untrimmed legacy helper so restorer's `legacy_restore` callback does not double-trim image history.

- [ ] **Step 2: Add delegation and Phase 1 byte-equivalence regression tests (do not edit existing tests)**

Read existing [tests/matmaster/services/test_model_history_restore_service.py](../../tests/matmaster/services/test_model_history_restore_service.py) to find the fakes used. Without changing any existing test, append focused regression tests for the new delegation seam and for Phase 1 output invariants.

The delegation test asserts:
- `restore_history(session_id="sess-1", spawn_id=None, task_id=None)` constructs at least one `ModelHistoryRestorer` and calls `restorer.restore(session_id, spawn_id=None)` exactly once for the successful path.
- The output of `restore_history` includes the messages returned by `restorer.restore` after `trim_history_images` post-processing.

The byte-equivalence test uses the real `history_checkpoint_codec.serialize_base_messages` / `deserialize_base_messages` contract and a mixed event fixture. It should assert the final `Message.model_dump(mode="json")` list, not just message count:
- v1 checkpoint `base_messages` starts with a compact `UserMessage` containing `<compacted_history>` so marker validation is exercised.
- tail events include `user_turn_context` with `images`, `assistant_state` with `tool_calls`, dict/list-form `tool_result.result`, and same-turn `response` + `run_result`.
- expected output preserves user image URLs, keeps assistant/tool-call pairing valid, encodes non-string tool result through `_normalize_tool_result_event` / `json.dumps(..., sort_keys=True)`, and does not duplicate assistant output when `response` and `run_result` both appear in the same turn.

Do not weaken the existing `tests/matmaster/services/test_model_history_restore_service.py` fixtures. They are the byte-equivalence guard for Phase 1 behaviour, especially:
- dict/list `tool_result.result` still serializes through `json.dumps(..., sort_keys=True)` via `_normalize_tool_result_event`
- `tool_call` + public `tool_result` still pair through `ChatHistoryConverter`
- orphan `tool_result` is skipped
- `response` followed by same-turn `run_result` does not duplicate assistant output
- `ImageContentPart` nested in `user_turn_context.message.images` survives restore as `UserMessage.images`
- null `covered_until_event_id` on v1 checkpoint falls back to legacy restore and image trimming happens only once

- [ ] **Step 3: Verify the new test is red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_model_history_restore_service.py -q
```

Expected: failure citing the absence of `ModelHistoryRestorer` invocation inside the service.

- [ ] **Step 4: Refactor `src/services/model_history_restore_service.py`**

Apply the following edits (in this order, preserving every public method signature):

1. Add an import:

```python
from matmaster.context.history_restore import ModelHistoryRestorer
```

2. Refactor `restore_history` so that the inner algorithm is delegated:

```python
def restore_history(
    self,
    *,
    session_id: str,
    spawn_id: str | None,
    task_id: str | None,
    raw_limit: int | None = None,
) -> list[Message]:
    checkpoints = self.events_table.get_history_checkpoints(
        session_id, spawn_id, limit=5
    )
    v1_checkpoints = self._v1_checkpoints(checkpoints)

    for v1_checkpoint in v1_checkpoints:
        content = v1_checkpoint.get("content")
        if not isinstance(content, dict):
            logger.warning(
                "model_history_restore: v1 checkpoint content is not a dict "
                "session_id=%s spawn_id=%s checkpoint_id=%s",
                session_id,
                spawn_id,
                v1_checkpoint.get("id"),
            )
            continue

        try:
            messages = self._delegate_v1_restore(
                session_id=session_id,
                spawn_id=spawn_id,
                task_id=task_id,
                raw_limit=raw_limit,
                checkpoint=v1_checkpoint,
            )
            return trim_history_images(messages)
        except Exception as exc:
            logger.warning(
                "model_history_restore: v1 checkpoint restore failed; trying older "
                "checkpoint session_id=%s spawn_id=%s checkpoint_id=%s err=%s: %s",
                session_id,
                spawn_id,
                v1_checkpoint.get("id"),
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            continue

    if v1_checkpoints:
        return trim_history_images(
            self._restore_legacy_untrimmed(session_id, spawn_id, task_id, raw_limit)
        )

    messages = self._delegate_v1_restore(
        session_id=session_id,
        spawn_id=spawn_id,
        task_id=task_id,
        raw_limit=raw_limit,
        checkpoint=None,
    )
    return trim_history_images(messages)
```

3. Add `_delegate_v1_restore`:

```python
def _delegate_v1_restore(
    self,
    *,
    session_id: str,
    spawn_id: str | None,
    task_id: str | None,
    raw_limit: int | None,
    checkpoint: dict[str, Any] | None,
) -> list[Message]:
    """Wrap ModelHistoryRestorer with the service-level task_id filter.

    ModelHistoryRestorer expects raw event dicts with type / content /
    source / invocation_id. The service still owns task_id-scoped
    filtering of the underlying event log, so we provide a get_events_after
    callback that loads scope events and removes task-owned ones before
    handing them to the restorer.
    """

    def get_latest_checkpoint(_session_id: str, _spawn_id: str | None):
        return checkpoint

    def get_events_after(
        _session_id: str,
        after_event_id: int | None,
        _spawn_id: str | None,
    ) -> list[dict[str, Any]]:
        events = self.events_table.get_scope_events_after_id(
            session_id,
            spawn_id,
            after_event_id,
        )
        events = ChatHistoryConverter.exclude_task_events(events, task_id)
        return [self._coerce_to_restorer_dict(event) for event in events]

    def has_user_turn_context(_session_id: str, _spawn_id: str | None) -> bool:
        return bool(
            self.events_table.has_user_turn_context(session_id, spawn_id)
        )

    def legacy_restore(_session_id: str, _spawn_id: str | None) -> list[Message]:
        return self._restore_legacy_untrimmed(
            session_id,
            spawn_id,
            task_id,
            raw_limit,
        )

    def deserialize_checkpoint_base_messages(raw: list[dict[str, Any]]) -> list[Message]:
        messages = deserialize_base_messages(raw)
        validate_base_messages(messages)
        return messages

    def validate_history(messages: list[Message]) -> None:
        validate_base_messages(messages)

    restorer = ModelHistoryRestorer(
        get_latest_checkpoint=get_latest_checkpoint,
        get_events_after=get_events_after,
        has_user_turn_context=has_user_turn_context,
        legacy_restore=legacy_restore,
        deserialize_base_messages=deserialize_checkpoint_base_messages,
        events_to_messages=ChatHistoryConverter.events_to_messages,
        normalize_tool_result_event=self._normalize_tool_result_event,
        validate_history=validate_history,
    )
    return restorer.restore(session_id, spawn_id=spawn_id)
```

4. Add `_restore_legacy_untrimmed` and keep `_restore_legacy` as a tiny compatibility wrapper if internal callers still exist. The untrimmed helper prevents double `trim_history_images` when restorer falls back to pure v0:

```python
def _restore_legacy_untrimmed(
    self,
    session_id: str,
    spawn_id: str | None,
    task_id: str | None,
    raw_limit: int | None,
) -> list[Message]:
    raw_events = self.events_table.get_session_events(
        session_id,
        limit=raw_limit,
        include_spawn=spawn_id is not None,
    )
    if spawn_id is None:
        raw_events = ChatHistoryConverter.exclude_spawn_events(raw_events)
    else:
        raw_events = [
            event for event in raw_events if event.get("spawn_id") == spawn_id
        ]
    raw_events = ChatHistoryConverter.exclude_task_events(raw_events, task_id)
    return ChatHistoryConverter.events_to_messages(raw_events)


def _restore_legacy(
    self,
    session_id: str,
    spawn_id: str | None,
    task_id: str | None,
    raw_limit: int | None,
) -> list[Message]:
    return trim_history_images(
        self._restore_legacy_untrimmed(session_id, spawn_id, task_id, raw_limit)
    )
```

5. Add `_coerce_to_restorer_dict` to keep the new restorer happy without leaking DAO-row metadata:

```python
@staticmethod
def _coerce_to_restorer_dict(event: dict[str, Any]) -> dict[str, Any]:
    """Shape an events_table row for ModelHistoryRestorer.

    The restorer keys on type / content / source / invocation_id / id.
    Service-only fields (raw images list, created_at, ...) are forwarded
    through unchanged so the hybrid v1 path can still read them.
    """
    if isinstance(event, dict):
        return dict(event)
    return {}
```

6. Remove the now-unused private methods `_restore_v1` and `_event_to_v1_compatible_event` from this service file — these belong to `matmaster.context.history_restore.ModelHistoryRestorer`. Keep `_restore_legacy_untrimmed`, `_restore_legacy`, `_v1_checkpoints`, `_session_has_user_turn_context`, and `_normalize_tool_result_event`.

Critical: `_normalize_tool_result_event` must **not** be deleted in Phase 2B even if its old direct caller is removed. It is the injected compatibility callback that preserves Phase 1 JSON encoding and `call_id` / `tool_name` normalization semantics. It can only be retired with the broader `ChatHistoryConverter` compatibility cleanup in Phase 4.

- [ ] **Step 5: Verify service restore regression tests pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_model_history_restore_service.py -q
```

Expected: pass. This includes the new delegation assertion and the Phase 1 byte-equivalence guard.

- [ ] **Step 6: Verify Phase 1 checkpoint codec tests still pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  -q
```

Expected: pass without modification. If any test fails, the refactor diverged from Phase 1 codec / restore behaviour — fix the refactor, not the test.

- [ ] **Step 7: Verify Phase 2A + 2B-so-far tests still pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context tests/matmaster/services -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add src/services/model_history_restore_service.py tests/matmaster/services/test_model_history_restore_service.py && git commit -m "refactor(restore): delegate algorithm to ModelHistoryRestorer

src/services/model_history_restore_service.py now delegates the
v0/hybrid/v1 schema-aware dispatch to matmaster.context.history_restore.
ModelHistoryRestorer, keeping only multi-checkpoint fallback, task_id
filtering, and trim_history_images post-processing in the service
layer. restore_history() signature and behaviour are unchanged; the
new delegation test confirms ModelHistoryRestorer.restore is the
authoritative algorithm path."
```

---

## Task 10: Convert manifests/* Into Thin Shims

**Files:**
- Modify: `matmaster/manifests/scanner.py`
- Modify: `matmaster/manifests/attachment.py`
- Modify: `matmaster/manifests/skill.py`
- Modify: `matmaster/manifests/mcp.py`
- Modify: `matmaster/manifests/rehydrator.py`
- (Read-only consumers; do not edit) `matmaster/core/exp.py`, `matmaster/core/context_compactor.py`, `src/services/agent_run_service.py`, `src/services/agent_run_history_wiring.py`

**Spec 依据:** DESIGN.md §14 Phase 2B 「shim 改造」、附录 B「Phase 2B 改动」、§5.3、§7.3 末段、§4.2（硬约束 §4.2 #1 / #12）。

每个 manifests module 改造为薄 shim：保留原有公共符号与函数签名，把实现 re-export / delegate 到 `matmaster.context.*`。skill / mcp / scanner shim 可通过 `coerce_session_events()` 进入 typed 路径；attachment shim 必须走 `scan_legacy_attachment_entries()`，因为 legacy callers 仍传 display-flattened `User/query` row。

`CompactionRehydrator` 改造：构造参数与 `build()` 签名保持不变，在 manifests shim 边界保留 legacy XML 顺序（`attachments → loaded_skills → active_tools → runtime_context → external_artifacts`）。它直接调用 `matmaster/context/sources/*` 的低层 helper 拼回原有字符串，不经过 `SessionContextBuilder.build_sections()`；后者按 `SectionOrder` 输出 `skills → tools → attachments`，是 Phase 2C runtime cutover 使用的新主路径顺序。这个差异是有意的，不能在 Phase 2B shim 中混淆。

- [ ] **Step 1: Shim `matmaster/manifests/scanner.py`**

Replace the file contents with:

```python
"""Phase 2B shim — delegates to matmaster.context.scanner.

Removed in Phase 4 along with the rest of matmaster/manifests.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.scanner import (
    SkillHitRecord,
    coerce_session_events,
    scan_skill_hits as _typed_scan_skill_hits,
)

__all__ = ["SkillHitRecord", "scan_skill_hits"]


def scan_skill_hits(events: Iterable[dict[str, Any]]) -> list[SkillHitRecord]:
    """Legacy entry point: accepts list[dict] and returns list[SkillHitRecord].

    Internally adapts to the typed SessionEvent path. Top-level
    created_at on the input dict is preserved by injecting it into
    content before coercion, matching the legacy field-fishing behaviour
    in manifests/scanner.py prior to Phase 2B.
    """
    rows = []
    for event in events:
        if not isinstance(event, dict):
            continue
        adapted = dict(event)
        content = adapted.get("content")
        if isinstance(content, dict) and "created_at" not in content:
            if event.get("created_at") is not None:
                merged = dict(content)
                merged["created_at"] = event.get("created_at")
                adapted["content"] = merged
        rows.append(adapted)
    typed = coerce_session_events(rows)
    return list(_typed_scan_skill_hits(typed))
```

- [ ] **Step 2: Shim `matmaster/manifests/attachment.py`**

Replace contents with a thin shim that preserves every existing function name and return type (`list[AttachmentEntry]` rather than tuple, because callers pass the result around as `list`):

```python
"""Phase 2B shim — delegates to matmaster.context.sources.attachments.

Removed in Phase 4 along with the rest of matmaster/manifests.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.sources.attachments import (
    AttachmentEntry,
    AttachmentKind,
    filter_entries_after_event_id as _typed_filter_after,
    filter_entries_in_event_range as _typed_filter_range,
    format_entries_text as _typed_format,
    scan_legacy_attachment_entries as _legacy_scan,
)

__all__ = [
    "AttachmentEntry",
    "AttachmentKind",
    "build_available_attachments",
    "filter_entries_after_event_id",
    "filter_entries_in_event_range",
    "format_available_attachments",
]


def build_available_attachments(
    events: Iterable[dict[str, Any]],
    *,
    max_entries: int = 30,
) -> list[AttachmentEntry]:
    return list(_legacy_scan(events, max_entries=max_entries))


def filter_entries_after_event_id(
    entries: Iterable[AttachmentEntry],
    after_id: int | None,
) -> list[AttachmentEntry]:
    return list(_typed_filter_after(entries, after_id))


def filter_entries_in_event_range(
    entries: Iterable[AttachmentEntry],
    *,
    after_id: int | None,
    until_id: int | None,
) -> list[AttachmentEntry]:
    return list(_typed_filter_range(entries, after_id=after_id, until_id=until_id))


def format_available_attachments(entries: Iterable[AttachmentEntry]) -> str:
    return _typed_format(entries)
```

- [ ] **Step 3: Shim `matmaster/manifests/skill.py`**

```python
"""Phase 2B shim — delegates to matmaster.context.sources.skills.

Removed in Phase 4 along with the rest of matmaster/manifests.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.scanner import coerce_session_events
from matmaster.context.sources.skills import (
    format_loaded_skills as _typed_format,
    resolve_active_skills as _typed_resolve,
    skill_name as _typed_skill_name,
)

__all__ = ["skill_name", "resolve_active_skills", "format_loaded_skills"]


def skill_name(skill: Any) -> str:
    return _typed_skill_name(skill)


def resolve_active_skills(
    events: Iterable[dict[str, Any]],
    skill_registry: Any,
) -> list[Any]:
    typed_events = coerce_session_events(events)
    return list(_typed_resolve(typed_events, skill_registry))


def format_loaded_skills(skills: Iterable[Any]) -> str:
    return _typed_format(skills)
```

- [ ] **Step 4: Shim `matmaster/manifests/mcp.py`**

```python
"""Phase 2B shim — delegates to matmaster.context.sources.tools.

Removed in Phase 4 along with the rest of matmaster/manifests.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.context.sources.tools import (
    format_active_mcp as _typed_format,
    resolve_declared_servers as _typed_declared,
    resolve_runnable_servers as _typed_runnable,
)

__all__ = [
    "resolve_declared_servers",
    "resolve_runnable_servers",
    "format_active_mcp",
]


def resolve_declared_servers(skills: Iterable[Any]) -> set[str]:
    return _typed_declared(skills)


def resolve_runnable_servers(
    skills: Iterable[Any],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: dict[str, list[dict[str, Any]]] | None = None,
) -> set[str]:
    return _typed_runnable(
        skills,
        legal_servers=legal_servers,
        schemas_by_server=schemas_by_server,
    )


def format_active_mcp(
    skills: Iterable[Any],
    *,
    legal_servers: set[str] | None = None,
    schemas_by_server: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    return _typed_format(
        skills,
        legal_servers=legal_servers,
        schemas_by_server=schemas_by_server,
    )
```

- [ ] **Step 5: Shim `matmaster/manifests/rehydrator.py`**

`CompactionRehydrator` is the most delicate piece. Constructors and `build()` outputs are referenced by [matmaster/core/context_compactor.py:181](../../matmaster/core/context_compactor.py:181) and [matmaster/core/exp.py:496](../../matmaster/core/exp.py:496). The new implementation:

1. Keeps the same constructor signature.
2. Resolves attachments / skills / active tools by calling the low-level helpers in `matmaster.context.sources.*`.
3. Reconstitutes the legacy XML output (`<attachments>` + `<loaded_skills>` + `<active_tools>` + `<runtime_context>` + `<external_artifacts>` in the legacy ordering, with `runtime_context` / `external_artifacts` left empty). This intentionally does **not** call `SessionContextBuilder.build_sections()`: `SessionContextBuilder` sorts by `SectionOrder` (`skills → tools → attachments`), while the legacy rehydrator must keep `attachments → skills → tools`.
4. Continues to apply `_safe_call` wrapping to keep build resilience equivalent to Phase 1.

Replace `matmaster/manifests/rehydrator.py` with:

```python
"""Phase 2B shim — delegates to matmaster.context source helpers.

Removed in Phase 4 along with the rest of matmaster/manifests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from matmaster.context.scanner import coerce_session_events
from matmaster.context.sources.attachments import (
    filter_entries_in_event_range,
    format_entries_text,
    scan_legacy_attachment_entries,
)
from matmaster.context.sources.skills import format_loaded_skills, resolve_active_skills
from matmaster.context.sources.tools import format_active_mcp

logger = logging.getLogger(__name__)


class CompactionRehydrator:
    def __init__(
        self,
        *,
        get_query_events: Callable[[], list[dict[str, Any]]],
        get_all_events: Callable[[], list[dict[str, Any]]],
        get_latest_checkpoint_covered_until_event_id: (
            Callable[[], int | None] | None
        ) = None,
        skill_registry: Any,
        playground_ctx: Any,
        legal_mcp_servers: set[str] | None = None,
        schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None,
    ) -> None:
        self._get_query_events = get_query_events
        self._get_all_events = get_all_events
        self._get_latest_checkpoint_covered_until_event_id = (
            get_latest_checkpoint_covered_until_event_id
        )
        self._skill_registry = skill_registry
        self._playground_ctx = playground_ctx  # kept for parameter parity; unused
        self._legal_mcp_servers = legal_mcp_servers
        self._schemas_by_server = schemas_by_server

    async def build(self, *, until_event_id: int | None = None) -> str:
        query_events = self._safe_call("query_events", self._get_query_events, [])
        all_events = self._safe_call("all_events", self._get_all_events, [])
        latest_covered_until = None
        if self._get_latest_checkpoint_covered_until_event_id is not None:
            latest_covered_until = self._safe_call(
                "latest_checkpoint",
                self._get_latest_checkpoint_covered_until_event_id,
                None,
            )

        typed_all_events = coerce_session_events(all_events)

        skills = self._safe_call(
            "loaded_skills",
            lambda: resolve_active_skills(typed_all_events, self._skill_registry),
            (),
        )

        # attachments respect the latest checkpoint window (after_id) and any
        # explicit until_event_id from compactor — matches the Phase 1
        # CompactionRehydrator semantics exactly.
        entries = scan_legacy_attachment_entries(query_events)
        scoped_entries = filter_entries_in_event_range(
            entries,
            after_id=latest_covered_until,
            until_id=until_event_id,
        )
        attachments_text = self._safe_call(
            "attachments",
            lambda: format_entries_text(scoped_entries),
            "",
        )
        loaded_skills_text = self._safe_call(
            "loaded_skills_text",
            lambda: format_loaded_skills(skills),
            "",
        )
        active_mcp_text = self._safe_call(
            "active_mcp",
            lambda: format_active_mcp(
                skills,
                legal_servers=self._legal_mcp_servers,
                schemas_by_server=self._schemas_by_server,
            ),
            "",
        )

        return self._compose(
            attachments=attachments_text,
            loaded_skills=loaded_skills_text,
            active_mcp=active_mcp_text,
            runtime_context="",
            external_artifacts="",
        )

    @staticmethod
    def _safe_call(name: str, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception:
            logger.warning(
                "compaction rehydrator manifest failed: %s", name, exc_info=True
            )
            return default

    @staticmethod
    def _wrap(tag: str, content: str) -> str:
        text = (content or "").strip()
        if not text:
            return ""
        return f"<{tag}>\n{text}\n</{tag}>"

    def _compose(
        self,
        *,
        attachments: str,
        loaded_skills: str,
        active_mcp: str,
        runtime_context: str,
        external_artifacts: str,
    ) -> str:
        sections = [
            self._wrap("attachments", attachments),
            self._wrap("loaded_skills", loaded_skills),
            self._wrap("active_tools", active_mcp),
            self._wrap("runtime_context", runtime_context),
            self._wrap("external_artifacts", external_artifacts),
        ]
        return "\n\n".join(section for section in sections if section)
```

Note: the Phase 1 rehydrator imported `attachment_manifest`, `skill_manifest`, `mcp_manifest`. The shim imports the typed source helpers directly to remove a layer of indirection within `matmaster/manifests/`. This is safe because nothing outside `matmaster/manifests/` imports `attachment_manifest` from `matmaster/manifests/rehydrator.py`.

- [ ] **Step 6: Verify legacy manifests tests still pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/manifests -q
```

Expected: all four test files pass with zero edits. If any test breaks, the shim has drifted from the Phase 1 byte-equivalent contract — fix the shim, not the test.

- [ ] **Step 7: Verify Phase 1 runtime regression tests still pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/services/test_active_mcp_replay.py \
  tests/services/test_attachment_manifest_service.py \
  -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add matmaster/manifests/scanner.py matmaster/manifests/attachment.py matmaster/manifests/skill.py matmaster/manifests/mcp.py matmaster/manifests/rehydrator.py && git commit -m "refactor(manifests): convert to thin shims over matmaster.context

matmaster/manifests/{scanner,attachment,skill,mcp,rehydrator}.py
become thin shims that re-export public symbols from
matmaster.context.scanner / sources.{attachments,skills,tools} /
session. Function signatures and string outputs are byte-equivalent
to Phase 1 so that core/exp.py, core/context_compactor.py, and the
service callers remain untouched until Phase 2C cutover. The
manifests test suite continues to pass without edits, providing the
shim-equivalence guarantee."
```

---

## Task 11: Golden Master Events-Fixture Equivalence Tests

**Files:**
- Create: `tests/matmaster/context/test_manifests_equivalence.py`

**Spec 依据:** DESIGN.md §14 Phase 2B 测试目标「至少准备以下几类 events fixture，逐组对比旧 manifests 输出与新 source 输出」、§16、附录 B「Phase 2B 改动」。

Phase 2B 的核心验收门是 events fixture × golden master 等价对照。Task 10 已经让 `matmaster/manifests/*` 委托到 `matmaster/context/*`，所以等价对照本质上是「同一个 typed event 序列经过 source.to_sections().content 得到的字符串 == 旧 manifests 函数返回的字符串」。这一组测试 future-proof 的目的是：当 Phase 2C 切 runtime 主路径到 `ContextAssembler` 时，新链路输出仍然与旧链路逐字节对齐。

涵盖 fixture 类型（按 DESIGN.md §14 Phase 2B 列举）：
1. 普通附件累积（单轮 / 多轮）
2. 多轮 skill 激活 / 变化
3. tool catalog 演化
4. 带 `until_event_id` 的边界截断
5. 带 `spawn_id` 的过滤
6. checkpoint 前后事件混合
7. hash anchor 与 checkpoint 交错

- [ ] **Step 1: Write failing equivalence tests**

Create `tests/matmaster/context/test_manifests_equivalence.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.context.scanner import coerce_session_events, scan_skill_hits
from matmaster.context.sources.attachments import (
    SessionAttachmentsSource,
    filter_entries_in_event_range,
    format_entries_text,
    scan_attachment_entries,
)
from matmaster.context.sources.skills import (
    SessionSkillsSource,
    format_loaded_skills,
    resolve_active_skills,
)
from matmaster.context.sources.tools import (
    SessionToolsSource,
    format_active_mcp,
)
from matmaster.context.session import SessionContextBuilder
from matmaster.manifests import attachment as legacy_attachment
from matmaster.manifests import mcp as legacy_mcp
from matmaster.manifests import skill as legacy_skill
from matmaster.manifests.scanner import scan_skill_hits as legacy_scan_skill_hits
from matmaster.manifests.rehydrator import CompactionRehydrator
from matmaster.skills.registry import SkillRegistry
from matmaster.types.context import PlaygroundContext


def _registry(tmp_path: Path, skills: tuple[tuple[str, str, str | None], ...]) -> SkillRegistry:
    root = tmp_path / "skills"
    for name, description, server in skills:
        skill_dir = root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        meta = ["---", f"name: {name}", f"description: {description}"]
        if server:
            meta.append(f"mcp_server: {server}")
        meta.extend(["---", "body"])
        (skill_dir / "SKILL.md").write_text("\n".join(meta), encoding="utf-8")
    return SkillRegistry([root])


# ---------- fixture: single-turn attachments ----------

SINGLE_TURN_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {
            "content": "upload",
            "files": ["https://oss.example.com/a.csv"],
            "images": ["https://img.example.com/x.png"],
            "workspace_paths": ["/ws/data"],
        },
    },
]

# ---------- fixture: multi-turn attachments ----------

MULTI_TURN_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/a.csv"]},
    },
    {
        "id": 20,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/b.csv"]},
    },
    {
        "id": 30,
        "source": "User",
        "type": "query",
        "content": {"images": ["https://img.example.com/c.png"]},
    },
]

# ---------- fixture: skill evolution ----------

SKILL_EVOLUTION_EVENTS = [
    {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
    {"id": 2, "type": "skill_hit", "content": {"skill_name": "mlip"}},
    {"id": 3, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
]

# ---------- fixture: tool catalog evolution ----------

TOOL_CATALOG_EVENTS = [
    {"id": 1, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
    {"id": 2, "type": "skill_hit", "content": {"skill_name": "mlip"}},
    {"id": 3, "type": "skill_hit", "content": {"skill_name": "deprecated"}},
]

# ---------- fixture: checkpoint-mixed events ----------

CHECKPOINT_MIXED_EVENTS = [
    {
        "id": 10,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/pre.csv"]},
    },
    {"id": 15, "type": "history_checkpoint", "content": {"covered_until_event_id": 14}},
    {
        "id": 20,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/post.csv"]},
    },
]

# ---------- fixture: hash anchor + checkpoint interleave ----------

HASH_ANCHOR_EVENTS = [
    {
        "id": 5,
        "type": "user_turn_context",
        "content": {
            "message": {"role": "user", "content": "anchor turn"},
            "user_instructions_hash": "sha256:aaa",
        },
    },
    {"id": 10, "type": "history_checkpoint", "content": {"covered_until_event_id": 9}},
    {
        "id": 12,
        "source": "User",
        "type": "query",
        "content": {"files": ["https://oss.example.com/post-anchor.csv"]},
    },
]


# ---------- attachments ----------

@pytest.mark.parametrize(
    "events",
    [SINGLE_TURN_EVENTS, MULTI_TURN_EVENTS, CHECKPOINT_MIXED_EVENTS, HASH_ANCHOR_EVENTS],
)
def test_attachment_entries_equivalence(events) -> None:
    legacy_entries = legacy_attachment.build_available_attachments(events)
    typed = coerce_session_events(events)
    typed_entries = list(scan_attachment_entries(typed))

    assert legacy_entries == typed_entries


def test_legacy_top_level_attachment_shape_stays_supported() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "display-flattened production row",
            "files": ["https://oss.example.com/chat/data.csv"],
            "images": ["https://oss.example.com/chat/em.png"],
            "workspace_paths": ["/share/a.cif"],
        }
    ]

    # This assertion goes through the manifests shim. It must keep working even
    # though strict typed SessionEvent sources do not accept display-flattened
    # User/query rows.
    legacy_entries = legacy_attachment.build_available_attachments(events)

    assert [entry.label for entry in legacy_entries] == [
        "file_1",
        "image_1",
        "workspace_1",
    ]
    assert legacy_entries[0].source_event_id is None


@pytest.mark.parametrize("events", [MULTI_TURN_EVENTS, CHECKPOINT_MIXED_EVENTS])
def test_attachment_format_equivalence(events) -> None:
    legacy_entries = legacy_attachment.build_available_attachments(events)
    legacy_text = legacy_attachment.format_available_attachments(legacy_entries)

    typed = coerce_session_events(events)
    typed_entries = scan_attachment_entries(typed)
    typed_text = format_entries_text(typed_entries)

    assert legacy_text == typed_text


def test_attachment_filter_after_checkpoint_equivalence() -> None:
    legacy_entries = legacy_attachment.build_available_attachments(CHECKPOINT_MIXED_EVENTS)
    legacy_filtered = legacy_attachment.filter_entries_in_event_range(
        legacy_entries, after_id=14, until_id=None
    )

    typed = coerce_session_events(CHECKPOINT_MIXED_EVENTS)
    typed_entries = scan_attachment_entries(typed)
    typed_filtered = list(
        filter_entries_in_event_range(typed_entries, after_id=14, until_id=None)
    )

    assert legacy_filtered == typed_filtered


def test_attachment_until_event_id_boundary_equivalence() -> None:
    legacy_entries = legacy_attachment.build_available_attachments(MULTI_TURN_EVENTS)
    legacy_clipped = legacy_attachment.filter_entries_in_event_range(
        legacy_entries, after_id=None, until_id=20
    )

    typed = coerce_session_events(MULTI_TURN_EVENTS)
    source = SessionAttachmentsSource.from_events(typed, until_event_id=20)

    assert format_entries_text(source.entries) == legacy_attachment.format_available_attachments(
        legacy_clipped
    )


# ---------- skills ----------

def test_skill_equivalence(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        (
            ("pxrd", "PXRD helper", "mat_xrd"),
            ("mlip", "MLIP runner", "mat_mlip"),
        ),
    )

    legacy_skills = legacy_skill.resolve_active_skills(SKILL_EVOLUTION_EVENTS, registry)
    legacy_text = legacy_skill.format_loaded_skills(legacy_skills)

    typed = coerce_session_events(SKILL_EVOLUTION_EVENTS)
    typed_skills = resolve_active_skills(typed, registry)
    typed_text = format_loaded_skills(typed_skills)

    assert [s.name for s in legacy_skills] == [s.name for s in typed_skills]
    assert legacy_text == typed_text


def test_skill_hit_timestamp_bridge_equivalence(tmp_path: Path) -> None:
    events = [
        {
            "id": 1,
            "type": "skill_hit",
            "content": {"skill_name": "pxrd"},
            "created_at": "2026-01-01T00:00:00",
        }
    ]

    legacy_records = legacy_scan_skill_hits(events)
    typed_records = scan_skill_hits(coerce_session_events(events))

    assert typed_records == tuple(legacy_records)
    assert typed_records[0].timestamp == "2026-01-01T00:00:00"


# ---------- tools / active mcp ----------

def test_active_mcp_equivalence(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        (
            ("pxrd", "PXRD helper", "mat_xrd"),
            ("mlip", "MLIP runner", "mat_mlip"),
            ("deprecated", "old", "mat_dead"),
        ),
    )
    schemas = {
        "mat_xrd": [{"name": "read"}, {"name": "write"}],
        "mat_mlip": [{"name": "run"}],
        "mat_dead": [],
    }
    legal = {"mat_xrd", "mat_mlip"}

    typed = coerce_session_events(TOOL_CATALOG_EVENTS)
    typed_skills = resolve_active_skills(typed, registry)
    legacy_text = legacy_mcp.format_active_mcp(
        list(typed_skills), legal_servers=legal, schemas_by_server=schemas
    )
    typed_text = format_active_mcp(
        typed_skills, legal_servers=legal, schemas_by_server=schemas
    )

    assert legacy_text == typed_text


# ---------- rehydrator vs SessionContextBuilder ----------

@pytest.mark.asyncio
async def test_compaction_rehydrator_vs_session_builder(tmp_path: Path) -> None:
    """End-to-end equivalence: CompactionRehydrator (shim) and
    SessionContextBuilder + manual XML wrap produce the same string."""
    registry = _registry(tmp_path, (("pxrd", "PXRD helper", "mat_xrd"),))
    schemas = {"mat_xrd": [{"name": "read"}]}
    legal = {"mat_xrd"}
    events = MULTI_TURN_EVENTS + SKILL_EVOLUTION_EVENTS

    rehydrator = CompactionRehydrator(
        get_query_events=lambda: events,
        get_all_events=lambda: events,
        get_latest_checkpoint_covered_until_event_id=lambda: None,
        skill_registry=registry,
        playground_ctx=PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        legal_mcp_servers=legal,
        schemas_by_server=schemas,
    )
    legacy_text = await rehydrator.build()

    typed = coerce_session_events(events)
    builder = SessionContextBuilder(
        events=typed,
        skill_registry=registry,
        legal_mcp_servers=legal,
        schemas_by_server=schemas,
    )
    sections = builder.build_sections(until_event_id=None, include_attachments=True)

    def _wrap(tag: str, content: str) -> str:
        text = (content or "").strip()
        return f"<{tag}>\n{text}\n</{tag}>" if text else ""

    tag_map = {
        "session_attachments": "attachments",
        "session_skills": "loaded_skills",
        "session_tools": "active_tools",
    }
    legacy_order = (
        "session_attachments",
        "session_skills",
        "session_tools",
    )
    section_by_key = {section.key: section for section in sections}
    composed = "\n\n".join(
        _wrap(tag_map[key], section_by_key[key].content)
        for key in legacy_order
        if key in section_by_key and section_by_key[key].content.strip()
    )

    # This intentionally uses legacy XML order (attachments -> skills -> tools),
    # not SectionOrder (skills -> tools -> attachments). Phase 2B shim must
    # preserve legacy compaction bundle bytes; Phase 2C runtime cutover uses
    # SessionContextBuilder/SectionOrder for provider-facing user context.
    # The rehydrator also emits empty <runtime_context> and <external_artifacts>
    # tags only when their content is non-empty (both empty in Phase 1 baseline).
    assert legacy_text == composed


# ---------- spawn / task scope is the caller's concern ----------

def test_spawn_id_filtering_lives_in_caller_not_in_source(tmp_path: Path) -> None:
    """Phase 2B sources operate on whatever events the caller passes.
    Spawn / task filtering belongs to the DAO + service layer; this test
    pins down that the typed source contract does not implicitly filter
    by spawn_id."""
    typed = coerce_session_events(
        [
            {
                "id": 1,
                "source": "User",
                "type": "query",
                "content": {"files": ["https://oss.example.com/main.csv"]},
                "spawn_id": None,
            },
            {
                "id": 2,
                "source": "User",
                "type": "query",
                "content": {"files": ["https://oss.example.com/spawn.csv"]},
                "spawn_id": "spawn-A",
            },
        ]
    )
    entries = scan_attachment_entries(typed)
    assert {e.name for e in entries} == {"main.csv", "spawn.csv"}
```

- [ ] **Step 2: Verify equivalence tests pass**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_manifests_equivalence.py -q
```

Expected: all tests pass. If any test fails, the shim or new source has drifted — fix the implementation, not the test. The fixture set is the contract Phase 2C will need.

- [ ] **Step 3: Run full Phase 2A + 2B suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context tests/matmaster/manifests -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add tests/matmaster/context/test_manifests_equivalence.py && git commit -m "test(context): pin manifests/context equivalence with events fixtures

Adds tests/matmaster/context/test_manifests_equivalence.py covering
the DESIGN.md section 14 Phase 2B fixture matrix: single/multi-turn
attachments, skill evolution, tool catalog evolution, until_event_id
boundary, spawn filtering scope, checkpoint-mixed events, and hash
anchor / checkpoint interleave. The end-to-end test pins
CompactionRehydrator output against SessionContextBuilder sections
wrapped in legacy XML order so that the runtime path (still on manifests)
and the future Phase 2C cutover share a single byte-equivalent contract."
```

---

## Task 12: Phase Boundary Static Checks And Regression Verification

**Files:** no new source files

**Spec 依据:** DESIGN.md §4.2、§14 Phase 2B boundary、§16、附录 B「Phase 2B 改动」。

- [ ] **Step 1: Run Phase 2B unit test suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/manifests \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  -q
```

Expected: all pass.

- [ ] **Step 2: Run Phase 1 regression suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/matmaster/services/test_active_mcp_replay.py \
  tests/services/test_attachment_manifest_service.py \
  -q
```

Expected: all pass. This proves Phase 2B left the Phase 1 runtime contract intact.

- [ ] **Step 3: Compile new modules**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m compileall -q matmaster/context matmaster/manifests src/services/model_history_restore_service.py
```

Expected: exit 0.

- [ ] **Step 4: Verify runtime import boundary**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "from matmaster\.context|import matmaster\.context" matmaster src tests
```

Expected: every match must be inside one of:

```text
matmaster/context/                     # internal
matmaster/manifests/                   # shim (Task 10)
src/services/context_assembly_ports.py # Phase 2A
src/services/context_turn_intent.py    # Phase 2A
src/services/model_history_restore_service.py # Phase 2B Task 9
tests/                                 # all test files allowed
```

There must be **no** match in:

```text
src/services/agent_run_service.py
src/services/agent_run_history_wiring.py
matmaster/core/agent.py
matmaster/core/context_compactor.py
matmaster/core/exp.py
matmaster/types/current_input.py
matmaster/types/context.py
```

These four runtime callers must stay on the `matmaster.manifests` API until Phase 2C cutover (DESIGN.md §14 Phase 2C).

- [ ] **Step 5: Verify Phase 2B target file presence**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && \
  test -f matmaster/context/scanner.py && \
  test -f matmaster/context/session.py && \
  test -f matmaster/context/history_restore.py && \
  test -f matmaster/context/sources/attachments.py && \
  test -f matmaster/context/sources/skills.py && \
  test -f matmaster/context/sources/tools.py
```

Expected: exit 0.

- [ ] **Step 6: Verify Phase 2B did NOT leak Phase 2C cleanup**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "COMPAT:legacy-runtime-injection-helper|_apply_user_instructions_to_initial_user_query" src/services/agent_run_instructions.py src/services/agent_run_service.py
```

Expected: matches still exist in `src/services/agent_run_instructions.py`. Phase 2C owns deleting this helper; Phase 2B must not delete it.

- [ ] **Step 7: Verify Phase 2B did NOT touch compaction**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git diff --name-only $(git merge-base HEAD main 2>/dev/null || git rev-list --max-parents=0 HEAD | tail -1)..HEAD -- matmaster/core/context_compactor.py
```

Expected: empty output. `core/context_compactor.py` belongs to Phase 3 (`compaction.py` migration + checkpoint v1 marker + prompt A/B); Phase 2B must not edit it. If output is non-empty, revert that file.

- [ ] **Step 8: Verify manifest tests still pass without edits**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git diff --name-only HEAD -- tests/matmaster/manifests
```

Expected: empty output. Phase 2B preserves `tests/matmaster/manifests/` as a frozen shim-equivalence contract.

- [ ] **Step 9: Final status review**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git status --short
```

Expected: only Phase 2B files remain staged or committed. The pre-existing `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/context-refactor/DESIGN.md` working-tree states must not be changed by Phase 2B execution.

This Task has no commit unless verification revealed a small fix. If a fix was needed, stage only the files changed by that fix and commit with `fix: stabilize context phase 2b tests`.

---

## Phase 2B Acceptance Checklist

- [ ] `matmaster/context/scanner.py` exposes `coerce_session_events` and `scan_skill_hits` over the typed `SessionEvent` envelope; freezes nested lists into tuples; drops rows without a coercible int id; copies top-level `created_at` into `content["created_at"]`; keeps legacy string `content` skill-hit compatibility through the `content` fallback.
- [ ] `matmaster/context/sources/attachments.py` exposes `AttachmentEntry`, `scan_attachment_entries`, `scan_legacy_attachment_entries`, `filter_entries_in_event_range`, `filter_entries_after_event_id`, `format_entries_text`, and `SessionAttachmentsSource.from_events / with_added / to_sections`. The typed source stays strict; only `scan_legacy_attachment_entries` accepts display-flattened rows with top-level `files/images/workspace_paths` and missing `id`.
- [ ] `matmaster/context/sources/skills.py` exposes `skill_name`, `resolve_active_skills`, `format_loaded_skills`, and `SessionSkillsSource.from_events`; `SessionSkillsSource.skills` is reusable by `SessionToolsSource`.
- [ ] `matmaster/context/sources/tools.py` exposes `resolve_declared_servers`, `resolve_runnable_servers`, `format_active_mcp`, and `SessionToolsSource.from_skills`.
- [ ] `matmaster/context/session.py::SessionContextBuilder` constructor rejects non-tuple `events`, exposes `build_sections(until_event_id, include_attachments)`, and emits sections sorted by `SectionOrder`.
- [ ] `matmaster/context/history_restore.py::ModelHistoryRestorer` implements the v3.3 three-branch dispatch (pure v0 / hybrid v1 / pure v1) with all DB access injected via callbacks; null `covered_until_event_id` on v1 checkpoint falls back to `legacy_restore`; assistant/tool semantics are delegated through injected `deserialize_base_messages`, `events_to_messages`, `normalize_tool_result_event`, and `validate_history` callbacks rather than reimplemented by hand.
- [ ] `matmaster/context/assembly.py::ContextAssembler` accepts `session_context_factory`; when provided, the factory path builds sections through `SessionContextBuilder`; when omitted, `_empty_session_section_builder` remains the default; the `_session_section_builder_for_tests` seam is preserved for unit tests.
- [ ] `src/services/model_history_restore_service.py::ModelHistoryRestoreService.restore_history` delegates the algorithmic core to `ModelHistoryRestorer`; the service signature is unchanged; checkpoint codec validation, `ChatHistoryConverter.events_to_messages`, and `_normalize_tool_result_event` are injected into the restorer; `trim_history_images` runs exactly once at the outer service boundary.
- [ ] `_normalize_tool_result_event` remains in `src/services/model_history_restore_service.py` during Phase 2B and is not deleted until the broader Phase 4 `ChatHistoryConverter` compatibility cleanup.
- [ ] `matmaster/manifests/scanner.py / attachment.py / skill.py / mcp.py / rehydrator.py` are thin shims. `attachment.py` uses the legacy adapter; `rehydrator.py` intentionally bypasses `SessionContextBuilder.build_sections()` and preserves legacy XML order (`attachments → loaded_skills → active_tools`). All public symbols and function signatures match Phase 1 byte-for-byte; tests under `tests/matmaster/manifests/` pass without edits.
- [ ] `tests/matmaster/context/test_manifests_equivalence.py` covers single-turn attachments, display-flattened top-level attachments without `id`, multi-turn attachments, skill evolution, skill-hit `created_at` timestamp bridge, tool catalog evolution, until_event_id boundary, checkpoint-mixed events, hash anchor / checkpoint interleave, and end-to-end `CompactionRehydrator` legacy XML output ≡ `SessionContextBuilder` sections manually wrapped in legacy XML order.
- [ ] `tests/matmaster/services/test_model_history_restore_service.py` keeps Phase 1 restore invariants covered: codec marker validation, `ImageContentPart` rehydration, `assistant_state` with tool calls, dict/list tool_result JSON encoding, orphan tool_result skipping, same-turn `response` / `run_result` dedupe, null `covered_until_event_id` fallback, and single image trim.
- [ ] `src/services/agent_run_service.py`, `src/services/agent_run_history_wiring.py`, `matmaster/core/agent.py`, `matmaster/core/context_compactor.py`, `matmaster/core/exp.py`, `matmaster/types/current_input.py`, and `matmaster/types/context.py` contain no `matmaster.context` import.
- [ ] `_apply_user_instructions_to_initial_user_query` and the `COMPAT:legacy-runtime-injection-helper` marker still exist in `src/services/agent_run_instructions.py` (Phase 2C owns the cleanup).
- [ ] `matmaster/core/context_compactor.py` is untouched by Phase 2B (Phase 3 owns the compaction migration).

---

## Notes For Phase 2C

Phase 2C cuts the runtime over to the new path:

1. `src/services/agent_run_service.py` and `src/services/agent_run_history_wiring.py` switch from `from matmaster.manifests ...` to `from matmaster.context ...` and start building `TurnInput` + calling `resolve_turn_context_intent` + `context_assembler.assemble_turn(...)` instead of the manifests-based legacy path.
2. `matmaster/core/agent.py` import flips from `matmaster.manifests` to `matmaster.context`; the kernel entry stops assembling `turn_input` itself and uses the history-tail `UserMessage` written by the service layer.
3. `AgentRuntimeSpec` is extended with `context_assembler: ContextAssembler`, `user_instructions_port`, `session_events_port`, `session_jobs_port | None`. The `session_context_factory` parameter introduced in Task 7 becomes the production injection point: `agent_run_service` builds it from `playground_ctx`-derived skill registry + mcp configuration, and passes it into `ContextAssembler.__init__`.
4. `_apply_user_instructions_to_initial_user_query` is deleted from `src/services/agent_run_instructions.py`; the `COMPAT:legacy-runtime-injection-helper` marker is removed.
5. `matmaster/types/current_input.py` becomes a shim re-exporting `TurnInput` from `matmaster.context.sources.turn_input` (the `matmaster/types/context.py` shim is already done in Phase 0.5 — do not re-touch it).

Phase 2C must keep `matmaster/manifests/` in place as a shim. Its removal happens in Phase 4 along with the rest of the COMPAT cleanup. The golden-master fixture suite from Task 11 is the regression guard for Phase 2C's snapshot tests of prompt string output.
