# Phase 2A Context 内核与装配三件套 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 DESIGN.md v3.3 Phase 2A：新增 `matmaster/context/` 的最小可单测内核、简单 source、composition、assembler、turn intent 以及平台 port 实现；所有新代码对运行时保持 dead code，不切任何业务路径。

**Architecture:** 先建立纯核心类型与渲染原语，再建立 typed ports 与简单 source，然后用 `ContextComposition` 把 source 选择规则收口，最后用 `ContextAssembler` 在 mock ports 上验证 anchor / continuation / compaction 四类 intent。Phase 2A 不实现 events 重放型 source，因此 `ContextAssembler` 通过一个测试专用的 `_session_section_builder_for_tests` seam 接收 mock session sections；默认 builder 返回空 tuple，Phase 2B 再替换为 `SessionContextBuilder`。该 seam 不属于平台 port 契约，不能被 `src/services/` 生产代码使用。平台侧只新增读能力 adapter 与 intent resolver，不让 `agent_run_service`、`core/agent.py`、`matmaster/manifests/` 引入 `matmaster.context`。

**Tech Stack:** Python 3.11+ / uv / pytest / dataclasses / Protocol / Pydantic `UserMessage` / MySQL events DAO

**Spec 来源:** `.planning/context-refactor/DESIGN.md` §4.2、§5.1、§6、§6bis、§7.1-7.3、§7bis、§12、§14 Phase 2A、§16、附录 B「Phase 2A 改动」。

---

## 全局约束

1. Phase 2A 只新增 dead-code 模块和测试；不得修改 `src/services/agent_run_service.py` 的运行路径，不得修改 `matmaster/core/agent.py` 的运行路径，不得把 `matmaster/manifests/` 改为 shim。
2. 不迁移 `ContextCompactor`，不触碰 `matmaster/core/context_compactor.py`，不切 checkpoint v1 marker，不做 prompt 形态 A/B。
3. 不新增 `matmaster/context/session.py`、`scanner.py`、`history_restore.py`、`sources/attachments.py`、`sources/skills.py`、`sources/tools.py`。这些属于 Phase 2B。
4. `matmaster/context/ports.py` 的 port 返回 typed data carrier 或 event sequence；不得返回 `ContextSection`、`UserMessage`、`UserTurnContext`，不得返回 service 对象，不使用 `dict[str, Any]` 作为 port 边界。
5. `UserInstructions.text` 保留 raw text，只做 utf-8 bytes 截断；hash 基于 raw text，不做 strip。
6. `TurnInput.pre_turn_history_event_id` 是 `int`，默认 `0`；`0` 表示本轮前 session 没有任何 event。不要继续扩散 `pre_query_scope_event_id: int | None` 的旧语义。
7. 默认 prompt 形态保持 Phase 1 末态：本轮附件仍合并进 `<current_instruction>` block。`TurnAttachmentsSource` 可以独立存在，但 `TurnInput.to_sections()` 默认返回合并后的 `current_instruction` section；拆分版只通过显式参数 `split_attachments=True` 暴露给测试和后续 A/B。
8. `AppSessionEventsPort` 需要读取 DB 原始 event payload。`query_context_events()` 不得复用 display/replay 语义的 `_row_to_event()` flatten 逻辑；尤其 `User/query.content` 中的 `files/images/workspace_paths` 必须仍保留在 `SessionEvent.content` 内。
9. 所有 Python 命令使用 `uv run python` 或 `uv run pytest`，不要使用系统 Python。
10. 当前工作树可能已有 `.planning/` 与若干源文件/测试文件的用户改动。执行本计划时不要恢复、格式化或改写任何与 Phase 2A 无关的 dirty 文件；若 Phase 2A 需要编辑某个已 dirty 文件，先读 diff 再最小化叠加修改。

## File Structure

- Create: `matmaster/context/__init__.py`
- Create: `matmaster/context/sections.py`
- Create: `matmaster/context/rendering.py`
- Create: `matmaster/context/turn_context.py`
- Create: `matmaster/context/ports.py`
- Create: `matmaster/context/compositions.py`
- Create: `matmaster/context/assembly.py`
- Create: `matmaster/context/turn_intent.py`
- Create: `matmaster/context/sources/__init__.py`
- Create: `matmaster/context/sources/turn_input.py`
- Create: `matmaster/context/sources/user_instructions.py`
- Create: `matmaster/context/sources/compacted_history.py`
- Create: `matmaster/context/sources/session_jobs.py`
- Create: `matmaster/context/sources/workspace.py`
- Create: `matmaster/context/sources/artifacts.py`
- Create: `src/services/context_assembly_ports.py`
- Create: `src/services/context_turn_intent.py`
- Modify: `src/dao/chat_events_table.py`，只新增 read-only `query_context_events()` 与 raw context row parser，供 `AppSessionEventsPort` 调用；现有业务路径不调用，不复用 `_row_to_event()` 的 display flatten 语义。
- Test: `tests/matmaster/context/test_sections.py`
- Test: `tests/matmaster/context/test_rendering.py`
- Test: `tests/matmaster/context/test_turn_context.py`
- Test: `tests/matmaster/context/test_ports.py`
- Test: `tests/matmaster/context/sources/test_turn_input.py`
- Test: `tests/matmaster/context/sources/test_user_instructions.py`
- Test: `tests/matmaster/context/sources/test_compacted_history.py`
- Test: `tests/matmaster/context/sources/test_session_jobs.py`
- Test: `tests/matmaster/context/sources/test_placeholder_sources.py`
- Test: `tests/matmaster/context/test_compositions.py`
- Test: `tests/matmaster/context/test_assembly.py`
- Test: `tests/matmaster/context/test_turn_intent.py`
- Test: `tests/matmaster/services/test_context_assembly_ports.py`
- Test: `tests/matmaster/services/test_context_turn_intent.py`
- Modify Test: `tests/test_chat_events_history_checkpoint.py`，追加 DAO read-only query 的 SQL 断言。

Note: DESIGN.md §16 写的是 `tests/src/services/`，本计划按仓库现有布局放在 `tests/matmaster/services/`，与 Phase 1 service 测试保持一致。

---

## Task 1: Baseline And Phase Boundary Inventory

**Files:** read-only

- [ ] **Step 1: Confirm uv environment and dirty files**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -V && git status --short
```

Expected:

```text
Python 3.11+ or Python 3.13.x
git status --short prints the current dirty files
```

已知工作树可能包含与 Phase 2A 无关的 `.planning/` 删除、`DESIGN.md` 修改，以及其它源文件/测试文件修改。不要把 expected dirty list 当成必须匹配的断言；只需要确认 Python 环境正确，并记录哪些 dirty 文件与本计划可能发生编辑重叠。If other source files are dirty, read them before editing and do not revert them.

- [ ] **Step 2: Confirm Phase 2A target modules do not already exist**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && test ! -d matmaster/context && test ! -f src/services/context_assembly_ports.py && test ! -f src/services/context_turn_intent.py
```

Expected: command exits `0`. If one of these files already exists, stop and inspect it before continuing so this plan can be adapted instead of overwriting user work.

- [ ] **Step 3: Run Phase 1 focused baseline**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  -q
```

Expected: all selected tests pass. If baseline fails, stop and report the failing test names before starting Phase 2A.

- [ ] **Step 4: Confirm no runtime caller imports the future context package**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "matmaster\.context|from matmaster.context|import matmaster.context" src matmaster/core matmaster/manifests matmaster/types
```

Expected: no matches. This is the pre-Phase-2A boundary baseline.

This Task has no commit.

---

## Task 2: Add Core Context Primitives

**Files:**
- Create: `matmaster/context/__init__.py`
- Create: `matmaster/context/sections.py`
- Create: `matmaster/context/rendering.py`
- Create: `matmaster/context/turn_context.py`
- Create: `tests/matmaster/context/test_sections.py`
- Create: `tests/matmaster/context/test_rendering.py`
- Create: `tests/matmaster/context/test_turn_context.py`

**Spec 依据:** DESIGN.md §6.1、§6.2、§6.3、§6.4、§6.6、§16。

- [ ] **Step 1: Write failing tests for `ContextSection` and rendering**

Create `tests/matmaster/context/test_sections.py`:

```python
from __future__ import annotations

import pytest

from matmaster.context.sections import ContextSection, ContextView, SectionOrder


def test_context_section_accepts_runtime_and_checkpoint_views() -> None:
    section = ContextSection(
        key="user_instructions",
        tag="user_instructions",
        content="Use SI units.",
        order=SectionOrder.USER_INSTRUCTIONS,
        views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
    )

    assert section.key == "user_instructions"
    assert section.order == SectionOrder.USER_INSTRUCTIONS


def test_context_section_rejects_checkpoint_without_runtime() -> None:
    with pytest.raises(ValueError, match="CHECKPOINT view requires RUNTIME"):
        ContextSection(
            key="broken",
            tag="broken",
            content="content",
            order=1,
            views=frozenset({ContextView.CHECKPOINT}),
        )


@pytest.mark.parametrize(
    ("key", "tag", "message"),
    [
        ("", "valid", "ContextSection.key must be non-empty"),
        ("valid", "", "ContextSection.tag must be non-empty"),
    ],
)
def test_context_section_rejects_empty_key_or_tag(
    key: str,
    tag: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ContextSection(
            key=key,
            tag=tag,
            content="content",
            order=1,
            views=frozenset({ContextView.RUNTIME}),
        )
```

Create `tests/matmaster/context/test_rendering.py`:

```python
from __future__ import annotations

import logging

from matmaster.context.rendering import render_sections, wrap_tag
from matmaster.context.sections import ContextSection, ContextView, SectionOrder


def test_wrap_tag_strips_outer_whitespace() -> None:
    assert wrap_tag("current_instruction", "  Explain FeO. \n") == (
        "<current_instruction>\nExplain FeO.\n</current_instruction>"
    )


def test_wrap_tag_returns_empty_for_blank_content() -> None:
    assert wrap_tag("current_instruction", " \n ") == ""


def test_wrap_tag_escapes_close_tag(
    caplog,
) -> None:
    with caplog.at_level(logging.WARNING):
        rendered = wrap_tag("current_instruction", "Do not emit </current_instruction>")

    assert "</ current_instruction>" in rendered
    assert "</current_instruction>" not in rendered.removeprefix(
        "<current_instruction>\n"
    ).removesuffix("\n</current_instruction>")
    assert "escaping to avoid breaking section boundary" in caplog.text


def test_render_sections_filters_view_and_sorts_by_order() -> None:
    sections = (
        ContextSection(
            key="turn",
            tag="current_instruction",
            content="Explain FeO.",
            order=SectionOrder.TURN_INSTRUCTION,
            views=frozenset({ContextView.RUNTIME}),
        ),
        ContextSection(
            key="instructions",
            tag="user_instructions",
            content="Use SI units.",
            order=SectionOrder.USER_INSTRUCTIONS,
            views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
        ),
    )

    runtime = render_sections(sections, view=ContextView.RUNTIME)
    checkpoint = render_sections(sections, view=ContextView.CHECKPOINT)

    assert runtime == (
        "<user_instructions>\nUse SI units.\n</user_instructions>\n\n"
        "<current_instruction>\nExplain FeO.\n</current_instruction>"
    )
    assert checkpoint == "<user_instructions>\nUse SI units.\n</user_instructions>"
```

Create `tests/matmaster/context/test_turn_context.py`:

```python
from __future__ import annotations

import pytest

from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.turn_context import UserTurnContext
from matmaster.types.messages import ImageContentPart, UserMessage


def _section(
    key: str,
    tag: str,
    content: str,
    order: int,
    views: frozenset[ContextView],
) -> ContextSection:
    return ContextSection(
        key=key,
        tag=tag,
        content=content,
        order=order,
        views=views,
    )


def test_from_sources_rejects_duplicate_keys() -> None:
    first = (_section("same", "a", "one", 1, frozenset({ContextView.RUNTIME})),)
    second = (_section("same", "b", "two", 2, frozenset({ContextView.RUNTIME})),)

    with pytest.raises(ValueError, match="Duplicate section key 'same'"):
        UserTurnContext.from_sources(first, second)


def test_render_and_to_message_preserve_images() -> None:
    context = UserTurnContext.from_sources(
        (
            _section(
                "instructions",
                "user_instructions",
                "Use SI units.",
                SectionOrder.USER_INSTRUCTIONS,
                frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
            ),
        ),
        (
            _section(
                "turn",
                "current_instruction",
                "Explain FeO.",
                SectionOrder.TURN_INSTRUCTION,
                frozenset({ContextView.RUNTIME}),
            ),
        ),
        images=(ImageContentPart(url="https://example.com/feo.png"),),
    )

    runtime = context.to_message(ContextView.RUNTIME)
    checkpoint = context.to_message(ContextView.CHECKPOINT)

    assert isinstance(runtime, UserMessage)
    assert runtime.content == (
        "<user_instructions>\nUse SI units.\n</user_instructions>\n\n"
        "<current_instruction>\nExplain FeO.\n</current_instruction>"
    )
    assert runtime.images == [ImageContentPart(url="https://example.com/feo.png")]
    assert checkpoint.content == (
        "<user_instructions>\nUse SI units.\n</user_instructions>"
    )
    assert checkpoint.images == [ImageContentPart(url="https://example.com/feo.png")]
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_sections.py \
  tests/matmaster/context/test_rendering.py \
  tests/matmaster/context/test_turn_context.py \
  -q
```

Expected: import errors for `matmaster.context`.

- [ ] **Step 3: Implement `sections.py`, `rendering.py`, and `turn_context.py`**

Create `matmaster/context/__init__.py` with this content:

```python
"""Provider-facing context assembly primitives.

Phase 2A introduces this package as dead code: modules are unit-tested, but no
runtime path imports them until Phase 2C.
"""
```

Create `matmaster/context/sections.py` with these definitions:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class ContextView(str, Enum):
    RUNTIME = "runtime"
    CHECKPOINT = "checkpoint"


class SectionOrder(IntEnum):
    USER_INSTRUCTIONS = 10
    COMPACTED_HISTORY = 100
    SESSION_SKILLS = 300
    SESSION_TOOLS = 400
    SESSION_ATTACHMENTS = 500
    SESSION_WORKSPACE = 600
    SESSION_ARTIFACTS = 700
    TURN_INSTRUCTION = 1000
    TURN_ATTACHMENTS = 1100
    SESSION_JOBS = 1200
    TURN_INSTRUCTION_LAST = 1300


@dataclass(frozen=True)
class ContextSection:
    key: str
    tag: str
    content: str
    order: int
    views: frozenset[ContextView]

    def __post_init__(self) -> None:
        if ContextView.CHECKPOINT in self.views and ContextView.RUNTIME not in self.views:
            raise ValueError(
                f"Section {self.key!r}: CHECKPOINT view requires RUNTIME view "
                "(invariant RUNTIME ⊇ CHECKPOINT)"
            )
        if not self.key:
            raise ValueError("ContextSection.key must be non-empty")
        if not self.tag:
            raise ValueError("ContextSection.tag must be non-empty")
```

Create `matmaster/context/rendering.py` with these definitions:

```python
from __future__ import annotations

import logging
from collections.abc import Iterable

from matmaster.context.sections import ContextSection, ContextView

logger = logging.getLogger(__name__)


def _escape_close_tag(content: str, tag: str) -> str:
    close = f"</{tag}>"
    if close not in content:
        return content
    logger.warning(
        "rendering._escape_close_tag triggered: tag=%r content contains close "
        "form, escaping to avoid breaking section boundary",
        tag,
    )
    return content.replace(close, f"</ {tag}>")


def wrap_tag(tag: str, content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    text = _escape_close_tag(text, tag)
    return f"<{tag}>\n{text}\n</{tag}>"


def render_sections(
    sections: Iterable[ContextSection],
    *,
    view: ContextView,
    separator: str = "\n\n",
) -> str:
    visible = [section for section in sections if view in section.views]
    visible = [section for section in visible if section.content.strip()]
    visible.sort(key=lambda section: section.order)
    return separator.join(wrap_tag(section.tag, section.content) for section in visible)
```

Create `matmaster/context/turn_context.py` with these definitions:

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from matmaster.context.rendering import render_sections
from matmaster.context.sections import ContextSection, ContextView
from matmaster.types.messages import ImageContentPart, UserMessage


@dataclass(frozen=True)
class UserTurnContext:
    sections: tuple[ContextSection, ...]
    images: tuple[ImageContentPart, ...] = ()

    @classmethod
    def from_sources(
        cls,
        *section_groups: Iterable[ContextSection],
        images: Iterable[ImageContentPart] = (),
    ) -> UserTurnContext:
        merged: list[ContextSection] = []
        seen_keys: set[str] = set()
        for group in section_groups:
            for section in group:
                if section.key in seen_keys:
                    raise ValueError(
                        f"Duplicate section key {section.key!r} in UserTurnContext "
                        "sources. Keys must be unique across all sources."
                    )
                seen_keys.add(section.key)
                merged.append(section)
        return cls(sections=tuple(merged), images=tuple(images))

    def render(self, view: ContextView) -> str:
        return render_sections(self.sections, view=view)

    def to_message(self, view: ContextView) -> UserMessage:
        return UserMessage(content=self.render(view), images=list(self.images))
```

- [ ] **Step 4: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_sections.py \
  tests/matmaster/context/test_rendering.py \
  tests/matmaster/context/test_turn_context.py \
  -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  matmaster/context/__init__.py \
  matmaster/context/sections.py \
  matmaster/context/rendering.py \
  matmaster/context/turn_context.py \
  tests/matmaster/context/test_sections.py \
  tests/matmaster/context/test_rendering.py \
  tests/matmaster/context/test_turn_context.py && \
git commit -m "feat: add context section primitives"
```

---

## Task 3: Add Typed Ports And Simple Sources

**Files:**
- Create: `matmaster/context/ports.py`
- Create: `matmaster/context/sources/__init__.py`
- Create: `matmaster/context/sources/turn_input.py`
- Create: `matmaster/context/sources/user_instructions.py`
- Create: `matmaster/context/sources/compacted_history.py`
- Create: `matmaster/context/sources/session_jobs.py`
- Create: `matmaster/context/sources/workspace.py`
- Create: `matmaster/context/sources/artifacts.py`
- Create: `tests/matmaster/context/test_ports.py`
- Create: `tests/matmaster/context/sources/test_turn_input.py`
- Create: `tests/matmaster/context/sources/test_user_instructions.py`
- Create: `tests/matmaster/context/sources/test_compacted_history.py`
- Create: `tests/matmaster/context/sources/test_session_jobs.py`
- Create: `tests/matmaster/context/sources/test_placeholder_sources.py`

**Spec 依据:** DESIGN.md §4.2 #9-#12、§7.1-7.3、§7bis.2、§12、§14 Phase 2A、§16。

- [ ] **Step 1: Write failing tests for ports**

Create `tests/matmaster/context/test_ports.py`:

```python
from __future__ import annotations

from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    UserInstructions,
)


def test_user_instructions_is_typed_data_carrier() -> None:
    instructions = UserInstructions(
        text="Use SI units.\n",
        hash="sha256:abc",
        truncated=True,
    )

    assert instructions.text == "Use SI units.\n"
    assert instructions.hash == "sha256:abc"
    assert instructions.truncated is True


def test_session_event_preserves_typed_envelope() -> None:
    event = SessionEvent(
        id=7,
        event_type="user_turn_context",
        source="MatMaster",
        content={"kind": "anchor", "images": ()},
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )

    assert event.id == 7
    assert event.event_type == "user_turn_context"
    assert event.content["kind"] == "anchor"
    assert event.invocation_id == "inv-1"


def test_session_event_query_defaults_are_scope_safe() -> None:
    query = SessionEventQuery(session_id="sess-1", spawn_id=None)

    assert query.until_event_id is None
    assert query.event_types is None
    assert query.limit is None
    assert query.order == "asc"


def test_session_jobs_empty_returns_no_active_jobs() -> None:
    assert SessionJobs.empty().active_jobs == ()


def test_context_assembly_ports_optional_jobs_port_defaults_none() -> None:
    class EventsPort:
        async def load_events(self, query):
            return ()

    ports = ContextAssemblyPorts(session_events=EventsPort())

    assert ports.session_jobs is None
    assert not hasattr(ports, "extra")
    assert not hasattr(ports, "metadata")
    assert not hasattr(ports, "state")
    assert not hasattr(ports, "services")
```

- [ ] **Step 2: Write failing tests for simple sources**

Create `tests/matmaster/context/sources/test_turn_input.py`:

```python
from __future__ import annotations

from matmaster.context.rendering import wrap_tag
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)
from matmaster.types.current_input import (
    CurrentInputContext,
    build_current_instruction_block,
)
from matmaster.types.messages import ImageContentPart


def test_turn_instruction_source_returns_runtime_only_section() -> None:
    sections = TurnInstructionSource(user_text=" Explain FeO. ").to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert section.key == "current_instruction"
    assert section.tag == "current_instruction"
    assert section.content == "Explain FeO."
    assert section.order == SectionOrder.TURN_INSTRUCTION
    assert section.views == frozenset({ContextView.RUNTIME})


def test_turn_instruction_source_deferred_uses_last_order() -> None:
    section = TurnInstructionSource(user_text="Continue.", deferred=True).to_sections()[0]

    assert section.order == SectionOrder.TURN_INSTRUCTION_LAST


def test_turn_attachments_source_renders_future_split_section() -> None:
    sections = TurnAttachmentsSource(
        files=("https://oss.example.com/input.cif",),
        images=("https://oss.example.com/image.png",),
        workspace_paths=("/share/result.xyz",),
    ).to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert section.key == "turn_attachments"
    assert section.tag == "turn_attachments"
    assert section.order == SectionOrder.TURN_ATTACHMENTS
    assert section.content == (
        "file_1 input.cif https://oss.example.com/input.cif\n"
        "workspace_1 /share/result.xyz\n"
        "image_1 image.png https://oss.example.com/image.png"
    )


def test_turn_input_default_merges_attachments_into_current_instruction() -> None:
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="Explain FeO."),
        attachments=TurnAttachmentsSource(
            files=("https://oss.example.com/input.cif",),
            images=("https://oss.example.com/image.png",),
            workspace_paths=("/share/result.xyz",),
        ),
        pre_turn_history_event_id=9,
    )

    sections = turn_input.to_sections()

    assert len(sections) == 1
    assert sections[0].key == "current_instruction"
    assert sections[0].content == (
        "Explain FeO.\n\n"
        "[Current attachments]\n"
        "file_1 input.cif https://oss.example.com/input.cif\n"
        "workspace_1 /share/result.xyz\n"
        "image_1 image.png https://oss.example.com/image.png"
    )


def test_turn_input_default_shape_matches_existing_current_input_renderer() -> None:
    """Pin Phase 2A default prompt shape to Phase 1 ground truth."""
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="Explain FeO."),
        attachments=TurnAttachmentsSource(
            files=("https://oss.example.com/input.cif",),
            images=("https://oss.example.com/image.png",),
            workspace_paths=("/share/result.xyz",),
        ),
    )
    legacy_context = CurrentInputContext.from_values(
        user_text="Explain FeO.",
        files=("https://oss.example.com/input.cif",),
        images=("https://oss.example.com/image.png",),
        workspace_paths=("/share/result.xyz",),
    )

    section = turn_input.to_sections()[0]

    assert wrap_tag(section.tag, section.content) == build_current_instruction_block(
        legacy_context
    )


def test_turn_input_can_split_attachments_for_future_ab() -> None:
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="Explain FeO."),
        attachments=TurnAttachmentsSource(files=("https://oss.example.com/input.cif",)),
    )

    sections = turn_input.to_sections(split_attachments=True)

    assert [section.key for section in sections] == [
        "current_instruction",
        "turn_attachments",
    ]


def test_turn_input_has_effective_input_and_images_as_parts() -> None:
    empty = TurnInput()
    with_image = TurnInput(
        attachments=TurnAttachmentsSource(images=("https://example.com/a.png",))
    )

    assert empty.has_effective_input() is False
    assert with_image.has_effective_input() is True
    assert with_image.attachments.images_as_parts() == (
        ImageContentPart(url="https://example.com/a.png"),
    )
```

Create `tests/matmaster/context/sources/test_user_instructions.py`:

```python
from __future__ import annotations

from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.user_instructions import UserInstructionsSource


def test_user_instructions_empty_returns_no_sections() -> None:
    assert UserInstructionsSource(text=" \n ").to_sections() == ()


def test_user_instructions_source_preserves_raw_content() -> None:
    section = UserInstructionsSource(text="Use SI units.\n").to_sections()[0]

    assert section.key == "user_instructions"
    assert section.tag == "user_instructions"
    assert section.content == "Use SI units.\n"
    assert section.order == SectionOrder.USER_INSTRUCTIONS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
```

Create `tests/matmaster/context/sources/test_compacted_history.py`:

```python
from __future__ import annotations

from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.compacted_history import CompactedHistorySource


def test_compacted_history_empty_returns_no_sections() -> None:
    assert CompactedHistorySource(summary="").to_sections() == ()


def test_compacted_history_source_returns_checkpoint_visible_section() -> None:
    section = CompactedHistorySource(summary="Earlier turns mention FeO.").to_sections()[0]

    assert section.key == "compacted_history"
    assert section.tag == "compacted_history"
    assert section.content == "Earlier turns mention FeO."
    assert section.order == SectionOrder.COMPACTED_HISTORY
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
```

Create `tests/matmaster/context/sources/test_session_jobs.py`:

```python
from __future__ import annotations

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.session_jobs import SessionJobsSource


def test_session_jobs_empty_returns_no_sections() -> None:
    assert SessionJobsSource.from_jobs(SessionJobs.empty()).to_sections() == ()


def test_session_jobs_source_renders_stable_json_lines() -> None:
    jobs = SessionJobs(
        active_jobs=(
            {"id": "job-2", "state": "running"},
            {"id": "job-1", "state": "queued"},
        )
    )

    section = SessionJobsSource.from_jobs(jobs).to_sections()[0]

    assert section.key == "session_jobs"
    assert section.tag == "session_jobs"
    assert section.order == SectionOrder.SESSION_JOBS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
    assert section.content == (
        'job_1 {"id": "job-2", "state": "running"}\n'
        'job_2 {"id": "job-1", "state": "queued"}'
    )
```

Create `tests/matmaster/context/sources/test_placeholder_sources.py`:

```python
from __future__ import annotations

from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.artifacts import SessionArtifactsSource
from matmaster.context.sources.workspace import SessionWorkspaceSource


def test_workspace_source_empty_returns_no_sections() -> None:
    assert SessionWorkspaceSource(text="").to_sections() == ()


def test_workspace_source_renders_checkpoint_visible_section() -> None:
    section = SessionWorkspaceSource(text="/share/result.xyz").to_sections()[0]

    assert section.key == "session_workspace"
    assert section.tag == "session_workspace"
    assert section.order == SectionOrder.SESSION_WORKSPACE
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


def test_artifacts_source_empty_returns_no_sections() -> None:
    assert SessionArtifactsSource(text="").to_sections() == ()


def test_artifacts_source_renders_checkpoint_visible_section() -> None:
    section = SessionArtifactsSource(text="figure: /share/a.png").to_sections()[0]

    assert section.key == "session_artifacts"
    assert section.tag == "session_artifacts"
    assert section.order == SectionOrder.SESSION_ARTIFACTS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
```

- [ ] **Step 3: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_ports.py \
  tests/matmaster/context/sources \
  -q
```

Expected: import errors for `matmaster.context.ports` and source modules.

- [ ] **Step 4: Implement `ports.py`**

Create `matmaster/context/ports.py` with these definitions:

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = (
    JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)
JsonObject: TypeAlias = Mapping[str, JsonValue]


@dataclass(frozen=True)
class UserInstructions:
    text: str
    hash: str
    truncated: bool = False


class UserInstructionsPort(Protocol):
    async def load_user_instructions(
        self,
        workspace_root: Path,
    ) -> UserInstructions:
        raise NotImplementedError


@dataclass(frozen=True)
class SessionEvent:
    """DB events row envelope for context assembly.

    `content` must preserve the raw DB payload shape after JSON parsing. For
    rows loaded through AppSessionEventsPort, nested lists are converted to
    tuples by `_freeze_json_object`; callers should not pass display-flattened
    User/query rows where files/images/workspace_paths were hoisted out.
    """

    id: int
    event_type: str
    source: str | None
    content: JsonObject
    task_id: str | None = None
    invocation_id: str | None = None
    spawn_id: str | None = None


@dataclass(frozen=True)
class SessionEventQuery:
    session_id: str
    spawn_id: str | None
    until_event_id: int | None = None
    event_types: tuple[str, ...] | None = None
    limit: int | None = None
    order: Literal["asc", "desc"] = "asc"


class SessionEventsPort(Protocol):
    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]:
        raise NotImplementedError


@dataclass(frozen=True)
class SessionJobs:
    active_jobs: tuple[JsonObject, ...] = ()

    @classmethod
    def empty(cls) -> SessionJobs:
        return cls(active_jobs=())


@dataclass(frozen=True)
class SessionJobsQuery:
    session_id: str


class SessionJobsPort(Protocol):
    async def load_session_jobs(
        self,
        query: SessionJobsQuery,
    ) -> SessionJobs:
        raise NotImplementedError


@dataclass(frozen=True)
class ContextAssemblyPorts:
    session_events: SessionEventsPort
    session_jobs: SessionJobsPort | None = None
```

- [ ] **Step 5: Implement simple sources**

Create `matmaster/context/sources/__init__.py`:

```python
"""Context source dataclasses.

Phase 2A contains only simple sources. Event-derived sources move in Phase 2B.
"""
```

Create `matmaster/context/sources/user_instructions.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class UserInstructionsSource:
    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.text.strip():
            return ()
        return (
            ContextSection(
                key="user_instructions",
                tag="user_instructions",
                content=self.text,
                order=SectionOrder.USER_INSTRUCTIONS,
                views=_VIEWS,
            ),
        )
```

Create `matmaster/context/sources/compacted_history.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class CompactedHistorySource:
    summary: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.summary.strip():
            return ()
        return (
            ContextSection(
                key="compacted_history",
                tag="compacted_history",
                content=self.summary,
                order=SectionOrder.COMPACTED_HISTORY,
                views=_VIEWS,
            ),
        )
```

Create `matmaster/context/sources/turn_input.py` using the current Phase 1 attachment shape:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlparse

from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.types.messages import ImageContentPart

_RUNTIME = frozenset({ContextView.RUNTIME})


def _display_name(value: str) -> str:
    parsed = urlparse(value)
    return PurePosixPath(parsed.path or value).name or value


@dataclass(frozen=True)
class TurnInstructionSource:
    user_text: str = ""
    deferred: bool = False

    def to_sections(self) -> tuple[ContextSection, ...]:
        text = self.user_text.strip()
        if not text:
            return ()
        order = (
            SectionOrder.TURN_INSTRUCTION_LAST
            if self.deferred
            else SectionOrder.TURN_INSTRUCTION
        )
        return (
            ContextSection(
                key="current_instruction",
                tag="current_instruction",
                content=text,
                order=order,
                views=_RUNTIME,
            ),
        )


@dataclass(frozen=True)
class TurnAttachmentsSource:
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    workspace_paths: tuple[str, ...] = ()

    def to_lines(self) -> tuple[str, ...]:
        lines = [
            *(f"file_{i} {_display_name(v)} {v}" for i, v in enumerate(self.files, 1)),
            *(f"workspace_{i} {v}" for i, v in enumerate(self.workspace_paths, 1)),
            *(f"image_{i} {_display_name(v)} {v}" for i, v in enumerate(self.images, 1)),
        ]
        return tuple(lines)

    def to_sections(self) -> tuple[ContextSection, ...]:
        lines = self.to_lines()
        if not lines:
            return ()
        return (
            ContextSection(
                key="turn_attachments",
                tag="turn_attachments",
                content="\n".join(lines),
                order=SectionOrder.TURN_ATTACHMENTS,
                views=_RUNTIME,
            ),
        )

    def images_as_parts(self) -> tuple[ImageContentPart, ...]:
        return tuple(ImageContentPart(url=url) for url in self.images)


@dataclass(frozen=True)
class TurnInput:
    instruction: TurnInstructionSource = field(default_factory=TurnInstructionSource)
    attachments: TurnAttachmentsSource = field(default_factory=TurnAttachmentsSource)
    pre_turn_history_event_id: int = 0

    def to_sections(
        self,
        *,
        split_attachments: bool = False,
    ) -> tuple[ContextSection, ...]:
        if split_attachments:
            return (
                *self.instruction.to_sections(),
                *self.attachments.to_sections(),
            )

        merged = self._merged_current_instruction_text()
        if not merged.strip():
            return ()
        return TurnInstructionSource(
            user_text=merged,
            deferred=self.instruction.deferred,
        ).to_sections()

    def has_effective_input(self) -> bool:
        return bool(
            self.instruction.user_text.strip()
            or self.attachments.files
            or self.attachments.images
            or self.attachments.workspace_paths
        )

    def _merged_current_instruction_text(self) -> str:
        lines: list[str] = []
        user_text = self.instruction.user_text.strip()
        if user_text:
            lines.append(user_text)
        attachment_lines = self.attachments.to_lines()
        if attachment_lines:
            if lines:
                lines.append("")
            lines.append("[Current attachments]")
            lines.extend(attachment_lines)
        return "\n".join(lines).strip()
```

Create `matmaster/context/sources/session_jobs.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class SessionJobsSource:
    """Placeholder renderer for active jobs.

    The JSON-line shape is intentionally temporary; the Bohrium job ledger phase
    will define stable fields and may replace this renderer without treating
    the Phase 2A string format as product contract.
    """

    lines: tuple[str, ...] = ()

    @classmethod
    def from_jobs(cls, jobs: SessionJobs) -> SessionJobsSource:
        return cls(
            lines=tuple(
                f"job_{index} {json.dumps(job, ensure_ascii=False, sort_keys=True)}"
                for index, job in enumerate(jobs.active_jobs, 1)
            )
        )

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.lines:
            return ()
        return (
            ContextSection(
                key="session_jobs",
                tag="session_jobs",
                content="\n".join(self.lines),
                order=SectionOrder.SESSION_JOBS,
                views=_VIEWS,
            ),
        )
```

Create `matmaster/context/sources/workspace.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class SessionWorkspaceSource:
    """Placeholder workspace source.

    Phase 2A uses a simple text field so composition wiring is testable. Real
    workspace fields are owned by the later workspace/artifact integration.
    """

    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.text.strip():
            return ()
        return (
            ContextSection(
                key="session_workspace",
                tag="session_workspace",
                content=self.text,
                order=SectionOrder.SESSION_WORKSPACE,
                views=_VIEWS,
            ),
        )
```

Create `matmaster/context/sources/artifacts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class SessionArtifactsSource:
    """Placeholder artifact source.

    Phase 2A uses a simple text field only; future artifact integration may
    replace this carrier with typed fields.
    """

    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.text.strip():
            return ()
        return (
            ContextSection(
                key="session_artifacts",
                tag="session_artifacts",
                content=self.text,
                order=SectionOrder.SESSION_ARTIFACTS,
                views=_VIEWS,
            ),
        )
```

- [ ] **Step 6: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_ports.py \
  tests/matmaster/context/sources \
  -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  matmaster/context/ports.py \
  matmaster/context/sources/__init__.py \
  matmaster/context/sources/turn_input.py \
  matmaster/context/sources/user_instructions.py \
  matmaster/context/sources/compacted_history.py \
  matmaster/context/sources/session_jobs.py \
  matmaster/context/sources/workspace.py \
  matmaster/context/sources/artifacts.py \
  tests/matmaster/context/test_ports.py \
  tests/matmaster/context/sources/test_turn_input.py \
  tests/matmaster/context/sources/test_user_instructions.py \
  tests/matmaster/context/sources/test_compacted_history.py \
  tests/matmaster/context/sources/test_session_jobs.py \
  tests/matmaster/context/sources/test_placeholder_sources.py && \
git commit -m "feat: add context ports and simple sources"
```

---

## Task 4: Add Context Compositions

**Files:**
- Create: `matmaster/context/compositions.py`
- Create: `tests/matmaster/context/test_compositions.py`

**Spec 依据:** DESIGN.md §6bis、§12、§14 Phase 2A、§16。

- [ ] **Step 1: Write failing composition tests**

Create `tests/matmaster/context/test_compositions.py`:

```python
from __future__ import annotations

from matmaster.context.compositions import (
    ANCHOR_COMPOSITION,
    COMPACTED_COMPOSITION,
    CONTINUATION_COMPOSITION,
    ContextCompositionInputs,
)
from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)


def _session_section() -> ContextSection:
    return ContextSection(
        key="session_tools",
        tag="session_tools",
        content="Bash, Read",
        order=SectionOrder.SESSION_TOOLS,
        views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
    )


class OverrideSource:
    def to_sections(self) -> tuple[ContextSection, ...]:
        return (
            ContextSection(
                key="session_attachments",
                tag="session_attachments",
                content="file_1 old.cif https://example.com/old.cif",
                order=SectionOrder.SESSION_ATTACHMENTS,
                views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
            ),
        )


def test_composition_inputs_defaults_are_empty() -> None:
    inputs = ContextCompositionInputs()

    assert inputs.user_instructions_text == ""
    assert inputs.compacted_history_summary == ""
    assert inputs.turn_input is None
    assert inputs.session_sections == ()
    assert inputs.session_jobs == SessionJobs.empty()
    assert inputs.session_attachments_override is None
    assert inputs.defer_turn_instruction is False


def test_anchor_composition_includes_instructions_session_turn_and_jobs() -> None:
    context = ANCHOR_COMPOSITION.apply(
        ContextCompositionInputs(
            user_instructions_text="Use SI units.",
            turn_input=TurnInput(
                attachments=TurnAttachmentsSource(images=("https://example.com/a.png",))
            ),
            session_sections=(_session_section(),),
            session_jobs=SessionJobs(active_jobs=({"id": "job-1"},)),
        )
    )

    assert [section.key for section in context.sections] == [
        "user_instructions",
        "session_tools",
        "current_instruction",
        "session_jobs",
    ]
    assert context.images[0].url == "https://example.com/a.png"


def test_continuation_composition_excludes_user_instructions_and_session_sections() -> None:
    context = CONTINUATION_COMPOSITION.apply(
        ContextCompositionInputs(
            user_instructions_text="Use SI units.",
            turn_input=TurnInput(attachments=TurnAttachmentsSource(files=("a.cif",))),
            session_sections=(_session_section(),),
            session_jobs=SessionJobs(active_jobs=({"id": "job-1"},)),
        )
    )

    assert [section.key for section in context.sections] == [
        "current_instruction",
        "session_jobs",
    ]


def test_compacted_composition_includes_compacted_history_and_override() -> None:
    context = COMPACTED_COMPOSITION.apply(
        ContextCompositionInputs(
            user_instructions_text="Use SI units.",
            compacted_history_summary="Earlier turns mention FeO.",
            turn_input=TurnInput(),
            session_sections=(_session_section(),),
            session_jobs=SessionJobs.empty(),
            session_attachments_override=OverrideSource(),
        )
    )

    assert [section.key for section in context.sections] == [
        "user_instructions",
        "compacted_history",
        "session_attachments",
        "session_tools",
    ]


def test_defer_turn_instruction_moves_instruction_to_last_order() -> None:
    context = COMPACTED_COMPOSITION.apply(
        ContextCompositionInputs(
            compacted_history_summary="Earlier turns mention FeO.",
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Continue the analysis.")
            ),
            session_attachments_override=None,
            defer_turn_instruction=True,
        )
    )

    turn_section = [
        section for section in context.sections if section.key == "current_instruction"
    ][0]
    assert turn_section.order == SectionOrder.TURN_INSTRUCTION_LAST
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_compositions.py -q
```

Expected: import error for `matmaster.context.compositions`.

- [ ] **Step 3: Implement `compositions.py`**

Create `matmaster/context/compositions.py`:

```python
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from matmaster.context.ports import SessionJobs
from matmaster.context.sections import ContextSection
from matmaster.context.sources.compacted_history import CompactedHistorySource
from matmaster.context.sources.session_jobs import SessionJobsSource
from matmaster.context.sources.turn_input import TurnInput
from matmaster.context.sources.user_instructions import UserInstructionsSource
from matmaster.context.turn_context import UserTurnContext


class SectionSource(Protocol):
    def to_sections(self) -> tuple[ContextSection, ...]:
        raise NotImplementedError


@dataclass(frozen=True)
class ContextCompositionInputs:
    user_instructions_text: str = ""
    compacted_history_summary: str = ""
    turn_input: TurnInput | None = None
    session_sections: tuple[ContextSection, ...] = ()
    session_jobs: SessionJobs = field(default_factory=SessionJobs.empty)
    session_attachments_override: SectionSource | None = None
    defer_turn_instruction: bool = False


CompositionStep = Callable[[ContextCompositionInputs], tuple[ContextSection, ...]]


@dataclass(frozen=True)
class ContextComposition:
    name: str
    steps: tuple[CompositionStep, ...]

    def apply(self, inputs: ContextCompositionInputs) -> UserTurnContext:
        section_groups = tuple(step(inputs) for step in self.steps)
        images = ()
        if inputs.turn_input is not None:
            images = inputs.turn_input.attachments.images_as_parts()
        return UserTurnContext.from_sources(*section_groups, images=images)


def _step_user_instructions(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    return UserInstructionsSource(text=inputs.user_instructions_text).to_sections()


def _step_compacted_history(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    return CompactedHistorySource(summary=inputs.compacted_history_summary).to_sections()


def _step_session_sections(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    return inputs.session_sections


def _step_session_attachments_override(
    inputs: ContextCompositionInputs,
) -> tuple[ContextSection, ...]:
    if inputs.session_attachments_override is None:
        return ()
    return inputs.session_attachments_override.to_sections()


def _step_turn_input(inputs: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    if inputs.turn_input is None:
        return ()
    turn_input = inputs.turn_input
    if inputs.defer_turn_instruction:
        turn_input = dataclasses.replace(
            turn_input,
            instruction=dataclasses.replace(
                turn_input.instruction,
                deferred=True,
            ),
        )
    return turn_input.to_sections()


def _step_session_jobs(inputs: ContextCompositionInputs) -> tuple[ContextSection, ...]:
    return SessionJobsSource.from_jobs(inputs.session_jobs).to_sections()


ANCHOR_COMPOSITION = ContextComposition(
    name="anchor",
    steps=(
        _step_user_instructions,
        _step_session_sections,
        _step_turn_input,
        _step_session_jobs,
    ),
)

CONTINUATION_COMPOSITION = ContextComposition(
    name="continuation",
    steps=(
        _step_turn_input,
        _step_session_jobs,
    ),
)

COMPACTED_COMPOSITION = ContextComposition(
    name="compacted",
    steps=(
        _step_user_instructions,
        _step_compacted_history,
        _step_session_attachments_override,
        _step_session_sections,
        _step_turn_input,
        _step_session_jobs,
    ),
)
```

- [ ] **Step 4: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_compositions.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  matmaster/context/compositions.py \
  tests/matmaster/context/test_compositions.py && \
git commit -m "feat: add context compositions"
```

---

## Task 5: Add Context Assembler With Mock-Testable Session Builder

**Files:**
- Create: `matmaster/context/assembly.py`
- Create: `tests/matmaster/context/test_assembly.py`

**Spec 依据:** DESIGN.md §4.2 #5-#12、§7bis.3、§12、§14 Phase 2A、§16。

- [ ] **Step 1: Write failing assembler tests**

Create `tests/matmaster/context/test_assembly.py`:

```python
from __future__ import annotations

import pytest

from matmaster.context.assembly import (
    AssemblyResult,
    CompactionAssemblyRequest,
    ContextAssembler,
    ContextAssemblyIntent,
    TurnAssemblyRequest,
)
from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionJobs,
    UserInstructions,
)
from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.sources.turn_input import TurnInput, TurnInstructionSource


class EventsPort:
    def __init__(self) -> None:
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        return (
            SessionEvent(
                id=1,
                event_type="skill_hit",
                source="System",
                content={"name": "vasp"},
            ),
        )


class JobsPort:
    def __init__(self) -> None:
        self.queries = []

    async def load_session_jobs(self, query):
        self.queries.append(query)
        return SessionJobs(active_jobs=({"id": "job-1"},))


def _session_builder(events, until_event_id, include_attachments):
    assert events[0].id == 1
    assert until_event_id == 12
    assert include_attachments is True
    return (
        ContextSection(
            key="session_tools",
            tag="session_tools",
            content="VASP",
            order=SectionOrder.SESSION_TOOLS,
            views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
        ),
    )


def _instructions() -> UserInstructions:
    return UserInstructions(text="Use SI units.", hash="sha256:abc")


@pytest.mark.asyncio
async def test_assemble_turn_anchor_loads_events_and_jobs() -> None:
    events_port = EventsPort()
    jobs_port = JobsPort()
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=events_port, session_jobs=jobs_port),
        _session_section_builder_for_tests=_session_builder,
    )

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.ANCHOR_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Explain FeO."),
                pre_turn_history_event_id=12,
            ),
            user_instructions=_instructions(),
        ),
    )

    assert isinstance(result, AssemblyResult)
    assert result.user_instructions_text == "Use SI units."
    assert result.user_instructions_hash == "sha256:abc"
    assert result.used_composition == "anchor"
    assert result.covered_until_event_id is None
    assert len(events_port.queries) == 1
    assert events_port.queries[0].until_event_id == 12
    assert len(jobs_port.queries) == 1
    assert result.user_turn_context.render(ContextView.RUNTIME).count(
        "<session_tools>"
    ) == 1


@pytest.mark.asyncio
async def test_assemble_turn_continuation_does_not_load_events() -> None:
    events_port = EventsPort()
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=events_port))

    result = await assembler.assemble_turn(
        ContextAssemblyIntent.CONTINUATION_TURN,
        TurnAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            turn_input=TurnInput(
                instruction=TurnInstructionSource(user_text="Continue."),
                pre_turn_history_event_id=99,
            ),
            user_instructions=_instructions(),
        ),
    )

    assert events_port.queries == []
    assert result.used_composition == "continuation"
    assert "<user_instructions>" not in result.user_turn_context.render(
        ContextView.RUNTIME
    )


@pytest.mark.asyncio
async def test_assemble_compaction_prefight_derives_covered_until_from_turn_input() -> None:
    events_port = EventsPort()
    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=events_port),
        _session_section_builder_for_tests=_session_builder,
    )

    result = await assembler.assemble_compaction(
        ContextAssemblyIntent.PREFLIGHT_COMPACTION,
        CompactionAssemblyRequest(
            session_id="sess-1",
            spawn_id=None,
            user_instructions=_instructions(),
            compacted_history_summary="Earlier turns mention FeO.",
            turn_input=TurnInput(pre_turn_history_event_id=12),
        ),
    )

    assert result.covered_until_event_id == 12
    runtime = result.user_turn_context.to_message(ContextView.RUNTIME)
    checkpoint = result.user_turn_context.to_message(ContextView.CHECKPOINT)
    assert "<compacted_history>" in runtime.content
    assert "<current_instruction>" not in checkpoint.content


@pytest.mark.asyncio
async def test_assemble_compaction_runtime_requires_explicit_boundary() -> None:
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=EventsPort()))

    with pytest.raises(ValueError, match="RUNTIME_COMPACTION requires explicit"):
        await assembler.assemble_compaction(
            ContextAssemblyIntent.RUNTIME_COMPACTION,
            CompactionAssemblyRequest(
                session_id="sess-1",
                spawn_id=None,
                user_instructions=_instructions(),
                compacted_history_summary="Earlier turns mention FeO.",
            ),
        )


@pytest.mark.asyncio
async def test_assemble_compaction_rejects_wrong_intent() -> None:
    assembler = ContextAssembler(ContextAssemblyPorts(session_events=EventsPort()))

    with pytest.raises(ValueError, match="assemble_compaction does not accept"):
        await assembler.assemble_compaction(
            ContextAssemblyIntent.ANCHOR_TURN,
            CompactionAssemblyRequest(
                session_id="sess-1",
                spawn_id=None,
                user_instructions=_instructions(),
                compacted_history_summary="Earlier turns mention FeO.",
                covered_until_event_id=1,
            ),
        )
```

- [ ] **Step 2: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_assembly.py -q
```

Expected: import error for `matmaster.context.assembly`.

- [ ] **Step 3: Implement `assembly.py`**

Create `matmaster/context/assembly.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from matmaster.context.compositions import (
    ANCHOR_COMPOSITION,
    COMPACTED_COMPOSITION,
    CONTINUATION_COMPOSITION,
    ContextComposition,
    ContextCompositionInputs,
    SectionSource,
)
from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    UserInstructions,
)
from matmaster.context.sections import ContextSection
from matmaster.context.sources.turn_input import TurnInput
from matmaster.context.turn_context import UserTurnContext


class ContextAssemblyIntent(str, Enum):
    ANCHOR_TURN = "anchor_turn"
    CONTINUATION_TURN = "continuation_turn"
    PREFLIGHT_COMPACTION = "preflight_compaction"
    RUNTIME_COMPACTION = "runtime_compaction"

    @property
    def is_anchor_turn(self) -> bool:
        return self == ContextAssemblyIntent.ANCHOR_TURN

    @property
    def is_compaction(self) -> bool:
        return self in {
            ContextAssemblyIntent.PREFLIGHT_COMPACTION,
            ContextAssemblyIntent.RUNTIME_COMPACTION,
        }


@dataclass(frozen=True)
class TurnAssemblyRequest:
    session_id: str
    spawn_id: str | None
    turn_input: TurnInput
    user_instructions: UserInstructions


@dataclass(frozen=True)
class CompactionAssemblyRequest:
    session_id: str
    spawn_id: str | None
    user_instructions: UserInstructions
    compacted_history_summary: str
    turn_input: TurnInput | None = None
    covered_until_event_id: int | None = None
    session_attachments_override: SectionSource | None = None


@dataclass(frozen=True)
class AssemblyResult:
    user_turn_context: UserTurnContext
    user_instructions_text: str
    user_instructions_hash: str
    used_composition: str
    covered_until_event_id: int | None = None


SessionSectionBuilder = Callable[
    [tuple[SessionEvent, ...], int, bool],
    tuple[ContextSection, ...],
]


def _empty_session_section_builder(
    events: tuple[SessionEvent, ...],
    until_event_id: int,
    include_attachments: bool,
) -> tuple[ContextSection, ...]:
    return ()


_INTENT_COMPOSITION_MAP: dict[ContextAssemblyIntent, ContextComposition] = {
    ContextAssemblyIntent.ANCHOR_TURN: ANCHOR_COMPOSITION,
    ContextAssemblyIntent.CONTINUATION_TURN: CONTINUATION_COMPOSITION,
    ContextAssemblyIntent.PREFLIGHT_COMPACTION: COMPACTED_COMPOSITION,
    ContextAssemblyIntent.RUNTIME_COMPACTION: COMPACTED_COMPOSITION,
}


class ContextAssembler:
    def __init__(
        self,
        ports: ContextAssemblyPorts,
        *,
        _session_section_builder_for_tests: SessionSectionBuilder | None = None,
    ) -> None:
        self._ports = ports
        # Phase 2A test-only seam: session.py does not exist yet. Phase 2B
        # replaces the default with SessionContextBuilder and keeps this path
        # out of production service wiring.
        self._session_section_builder = (
            _session_section_builder_for_tests or _empty_session_section_builder
        )

    async def assemble_turn(
        self,
        intent: ContextAssemblyIntent,
        request: TurnAssemblyRequest,
    ) -> AssemblyResult:
        if intent not in {
            ContextAssemblyIntent.ANCHOR_TURN,
            ContextAssemblyIntent.CONTINUATION_TURN,
        }:
            raise ValueError(f"assemble_turn does not accept intent {intent!r}")

        composition = _INTENT_COMPOSITION_MAP[intent]
        session_sections: tuple[ContextSection, ...] = ()
        jobs = await self._load_jobs_or_empty(request.session_id)

        if intent == ContextAssemblyIntent.ANCHOR_TURN:
            history_boundary = request.turn_input.pre_turn_history_event_id
            events = await self._ports.session_events.load_events(
                SessionEventQuery(
                    session_id=request.session_id,
                    spawn_id=request.spawn_id,
                    until_event_id=history_boundary,
                    order="asc",
                )
            )
            session_sections = self._session_section_builder(
                events,
                history_boundary,
                True,
            )

        user_turn_context = composition.apply(
            ContextCompositionInputs(
                user_instructions_text=request.user_instructions.text,
                turn_input=request.turn_input,
                session_sections=session_sections,
                session_jobs=jobs,
            )
        )
        return AssemblyResult(
            user_turn_context=user_turn_context,
            user_instructions_text=request.user_instructions.text,
            user_instructions_hash=request.user_instructions.hash,
            used_composition=composition.name,
        )

    async def assemble_compaction(
        self,
        intent: ContextAssemblyIntent,
        request: CompactionAssemblyRequest,
    ) -> AssemblyResult:
        if not intent.is_compaction:
            raise ValueError(f"assemble_compaction does not accept intent {intent!r}")

        if intent == ContextAssemblyIntent.RUNTIME_COMPACTION:
            if request.covered_until_event_id is None:
                raise ValueError(
                    "RUNTIME_COMPACTION requires explicit covered_until_event_id"
                )
            covered_until = request.covered_until_event_id
        elif request.covered_until_event_id is not None:
            covered_until = request.covered_until_event_id
        elif request.turn_input is not None:
            covered_until = request.turn_input.pre_turn_history_event_id
        else:
            raise ValueError(
                "PREFLIGHT_COMPACTION requires turn_input or explicit "
                "covered_until_event_id"
            )

        events = await self._ports.session_events.load_events(
            SessionEventQuery(
                session_id=request.session_id,
                spawn_id=request.spawn_id,
                until_event_id=covered_until,
                order="asc",
            )
        )
        session_sections = self._session_section_builder(
            events,
            covered_until,
            request.session_attachments_override is None,
        )
        jobs = await self._load_jobs_or_empty(request.session_id)
        composition = _INTENT_COMPOSITION_MAP[intent]
        user_turn_context = composition.apply(
            ContextCompositionInputs(
                user_instructions_text=request.user_instructions.text,
                compacted_history_summary=request.compacted_history_summary,
                turn_input=request.turn_input,
                session_sections=session_sections,
                session_jobs=jobs,
                session_attachments_override=request.session_attachments_override,
                defer_turn_instruction=True,
            )
        )
        return AssemblyResult(
            user_turn_context=user_turn_context,
            user_instructions_text=request.user_instructions.text,
            user_instructions_hash=request.user_instructions.hash,
            used_composition=composition.name,
            covered_until_event_id=covered_until,
        )

    async def _load_jobs_or_empty(self, session_id: str) -> SessionJobs:
        if self._ports.session_jobs is None:
            return SessionJobs.empty()
        return await self._ports.session_jobs.load_session_jobs(
            SessionJobsQuery(session_id=session_id)
        )
```

- [ ] **Step 4: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/context/test_assembly.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  matmaster/context/assembly.py \
  tests/matmaster/context/test_assembly.py && \
git commit -m "feat: add context assembler"
```

---

## Task 6: Add Turn Intent Pure Function And Service Resolver

**Files:**
- Create: `matmaster/context/turn_intent.py`
- Create: `src/services/context_turn_intent.py`
- Create: `tests/matmaster/context/test_turn_intent.py`
- Create: `tests/matmaster/services/test_context_turn_intent.py`

**Spec 依据:** DESIGN.md §7bis.5、§8.3、§14 Phase 2A、§16。

- [ ] **Step 1: Write failing pure function tests**

Create `tests/matmaster/context/test_turn_intent.py`:

```python
from __future__ import annotations

from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.turn_intent import decide_turn_context_intent


def test_decide_turn_context_intent_returns_anchor_when_no_latest_hash() -> None:
    assert (
        decide_turn_context_intent(
            current_hash="sha256:current",
            latest_anchor_hash=None,
        )
        == ContextAssemblyIntent.ANCHOR_TURN
    )


def test_decide_turn_context_intent_returns_anchor_when_hash_changed() -> None:
    assert (
        decide_turn_context_intent(
            current_hash="sha256:new",
            latest_anchor_hash="sha256:old",
        )
        == ContextAssemblyIntent.ANCHOR_TURN
    )


def test_decide_turn_context_intent_returns_continuation_when_hash_matches() -> None:
    assert (
        decide_turn_context_intent(
            current_hash="sha256:same",
            latest_anchor_hash="sha256:same",
        )
        == ContextAssemblyIntent.CONTINUATION_TURN
    )
```

- [ ] **Step 2: Write failing service resolver tests**

Create `tests/matmaster/services/test_context_turn_intent.py`:

```python
from __future__ import annotations

import pytest

from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.ports import SessionEvent
from src.services.context_turn_intent import (
    _latest_anchor_hash_from_events,
    resolve_turn_context_intent,
)


class EventsPort:
    def __init__(self, events):
        self.events = events
        self.queries = []

    async def load_events(self, query):
        self.queries.append(query)
        return tuple(self.events)


def _event(event_type: str, content: dict, event_id: int) -> SessionEvent:
    return SessionEvent(
        id=event_id,
        event_type=event_type,
        source="MatMaster",
        content=content,
    )


def test_latest_anchor_hash_stops_at_checkpoint_without_hash() -> None:
    events = (
        _event("history_checkpoint", {"covered_until_event_id": 30}, 31),
        _event(
            "user_turn_context",
            {"kind": "anchor", "user_instructions_hash": "sha256:old"},
            30,
        ),
    )

    assert _latest_anchor_hash_from_events(events) is None


def test_latest_anchor_hash_uses_checkpoint_hash_as_barrier_value() -> None:
    events = (
        _event(
            "history_checkpoint",
            {"covered_until_event_id": 30, "user_instructions_hash": "sha256:cp"},
            31,
        ),
        _event(
            "user_turn_context",
            {"kind": "anchor", "user_instructions_hash": "sha256:old"},
            30,
        ),
    )

    assert _latest_anchor_hash_from_events(events) == "sha256:cp"


def test_latest_anchor_hash_skips_continuation_until_anchor() -> None:
    events = (
        _event("user_turn_context", {"kind": "continuation"}, 33),
        _event(
            "user_turn_context",
            {"kind": "anchor", "user_instructions_hash": "sha256:anchor"},
            32,
        ),
    )

    assert _latest_anchor_hash_from_events(events) == "sha256:anchor"


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_queries_recent_internal_events() -> None:
    port = EventsPort(
        [
            _event(
                "user_turn_context",
                {"kind": "anchor", "user_instructions_hash": "sha256:same"},
                10,
            )
        ]
    )

    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:same",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
    )

    assert intent == ContextAssemblyIntent.CONTINUATION_TURN
    assert port.queries[0].session_id == "sess-1"
    assert port.queries[0].event_types == (
        "user_turn_context",
        "history_checkpoint",
    )
    assert port.queries[0].limit == 50
    assert port.queries[0].order == "desc"


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_returns_anchor_when_no_events() -> None:
    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:current",
        session_id="sess-1",
        spawn_id=None,
        events_port=EventsPort(()),
    )

    assert intent == ContextAssemblyIntent.ANCHOR_TURN


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_returns_anchor_when_hash_differs() -> None:
    port = EventsPort(
        [
            _event(
                "user_turn_context",
                {"kind": "anchor", "user_instructions_hash": "sha256:old"},
                10,
            )
        ]
    )

    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:new",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
    )

    assert intent == ContextAssemblyIntent.ANCHOR_TURN


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_uses_checkpoint_hash() -> None:
    port = EventsPort(
        [
            _event(
                "history_checkpoint",
                {"covered_until_event_id": 30, "user_instructions_hash": "sha256:cp"},
                31,
            )
        ]
    )

    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:cp",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
    )

    assert intent == ContextAssemblyIntent.CONTINUATION_TURN


@pytest.mark.asyncio
async def test_resolve_turn_context_intent_falls_back_to_anchor_without_recent_anchor() -> None:
    port = EventsPort(
        [
            _event("user_turn_context", {"kind": "continuation"}, event_id)
            for event_id in range(50, 0, -1)
        ]
    )

    intent = await resolve_turn_context_intent(
        instructions_hash="sha256:current",
        session_id="sess-1",
        spawn_id=None,
        events_port=port,
    )

    assert intent == ContextAssemblyIntent.ANCHOR_TURN
```

- [ ] **Step 3: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_turn_intent.py \
  tests/matmaster/services/test_context_turn_intent.py \
  -q
```

Expected: import errors for `matmaster.context.turn_intent` and `src.services.context_turn_intent`.

- [ ] **Step 4: Implement intent modules**

Create `matmaster/context/turn_intent.py`:

```python
from __future__ import annotations

from matmaster.context.assembly import ContextAssemblyIntent


def decide_turn_context_intent(
    *,
    current_hash: str,
    latest_anchor_hash: str | None,
) -> ContextAssemblyIntent:
    if latest_anchor_hash is None or latest_anchor_hash != current_hash:
        return ContextAssemblyIntent.ANCHOR_TURN
    return ContextAssemblyIntent.CONTINUATION_TURN
```

Create `src/services/context_turn_intent.py`:

```python
from __future__ import annotations

from matmaster.context.assembly import ContextAssemblyIntent
from matmaster.context.ports import SessionEvent, SessionEventQuery, SessionEventsPort
from matmaster.context.turn_intent import decide_turn_context_intent


async def resolve_turn_context_intent(
    *,
    instructions_hash: str,
    session_id: str,
    spawn_id: str | None,
    events_port: SessionEventsPort,
) -> ContextAssemblyIntent:
    events = await events_port.load_events(
        SessionEventQuery(
            session_id=session_id,
            spawn_id=spawn_id,
            event_types=("user_turn_context", "history_checkpoint"),
            limit=50,
            order="desc",
        )
    )
    latest_hash = _latest_anchor_hash_from_events(events)
    return decide_turn_context_intent(
        current_hash=instructions_hash,
        latest_anchor_hash=latest_hash,
    )


def _latest_anchor_hash_from_events(
    events: tuple[SessionEvent, ...],
) -> str | None:
    for event in events:
        if event.event_type == "user_turn_context":
            if event.content.get("kind") != "anchor":
                continue
            anchor_hash = event.content.get("user_instructions_hash")
            return anchor_hash if isinstance(anchor_hash, str) and anchor_hash else None
        if event.event_type == "history_checkpoint":
            checkpoint_hash = event.content.get("user_instructions_hash")
            return (
                checkpoint_hash
                if isinstance(checkpoint_hash, str) and checkpoint_hash
                else None
            )
    return None
```

- [ ] **Step 5: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context/test_turn_intent.py \
  tests/matmaster/services/test_context_turn_intent.py \
  -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  matmaster/context/turn_intent.py \
  src/services/context_turn_intent.py \
  tests/matmaster/context/test_turn_intent.py \
  tests/matmaster/services/test_context_turn_intent.py && \
git commit -m "feat: add context turn intent resolver"
```

---

## Task 7: Add Platform Context Assembly Ports

**Files:**
- Create: `src/services/context_assembly_ports.py`
- Modify: `src/dao/chat_events_table.py`
- Create: `tests/matmaster/services/test_context_assembly_ports.py`
- Modify: `tests/test_chat_events_history_checkpoint.py`

**Spec 依据:** DESIGN.md §7bis.4、§8.6、§14 Phase 2A、§16、AGENTS.md 异常处理约定。

- [ ] **Step 1: Write failing service port tests**

Create `tests/matmaster/services/test_context_assembly_ports.py`:

```python
from __future__ import annotations

import logging

import pytest

from matmaster.context.ports import (
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    UserInstructions,
)
from src.services.context_assembly_ports import (
    USER_INSTRUCTIONS_MAX_BYTES,
    AppSessionEventsPort,
    AppSessionJobsPort,
    AppUserInstructionsPort,
    _freeze_json_object,
    _hash_user_instructions,
)


@pytest.mark.asyncio
async def test_app_user_instructions_port_missing_file_returns_empty_bundle(tmp_path) -> None:
    result = await AppUserInstructionsPort().load_user_instructions(tmp_path)

    assert result == UserInstructions(
        text="",
        hash=_hash_user_instructions(""),
        truncated=False,
    )


@pytest.mark.asyncio
async def test_app_user_instructions_port_preserves_raw_trailing_newline(tmp_path) -> None:
    agent_file = tmp_path / ".matmaster" / "AGENT.md"
    agent_file.parent.mkdir()
    agent_file.write_text("Use SI units.\n", encoding="utf-8")

    result = await AppUserInstructionsPort().load_user_instructions(tmp_path)

    assert result.text == "Use SI units.\n"
    assert result.hash == _hash_user_instructions("Use SI units.\n")


@pytest.mark.asyncio
async def test_app_user_instructions_port_truncates_by_utf8_bytes(
    tmp_path,
    caplog,
) -> None:
    agent_file = tmp_path / ".matmaster" / "AGENT.md"
    agent_file.parent.mkdir()
    agent_file.write_text("a" * (USER_INSTRUCTIONS_MAX_BYTES + 10), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = await AppUserInstructionsPort().load_user_instructions(tmp_path)

    assert len(result.text.encode("utf-8")) == USER_INSTRUCTIONS_MAX_BYTES
    assert result.truncated is True
    assert result.hash == _hash_user_instructions(result.text)
    assert "AGENT.md exceeds" in caplog.text


class FakeEventsTable:
    def __init__(self, rows=None) -> None:
        self.calls = []
        self.rows = rows or [
            {
                "id": 3,
                "source": "MatMaster",
                "type": "user_turn_context",
                "content": {
                    "kind": "anchor",
                    "images": ["https://example.com/a.png"],
                },
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
                "spawn_id": None,
            }
        ]

    def query_context_events(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


@pytest.mark.asyncio
async def test_app_session_events_port_maps_rows_to_typed_events() -> None:
    table = FakeEventsTable()
    port = AppSessionEventsPort(table)

    events = await port.load_events(
        SessionEventQuery(
            session_id="sess-1",
            spawn_id=None,
            until_event_id=9,
            event_types=("user_turn_context", "history_checkpoint"),
            limit=50,
            order="desc",
        )
    )

    assert table.calls == [
        {
            "session_id": "sess-1",
            "spawn_id": None,
            "until_event_id": 9,
            "event_types": ("user_turn_context", "history_checkpoint"),
            "limit": 50,
            "order": "desc",
        }
    ]
    assert events[0].id == 3
    assert events[0].event_type == "user_turn_context"
    assert events[0].content["images"] == ("https://example.com/a.png",)
    assert events[0].invocation_id == "inv-1"


@pytest.mark.asyncio
async def test_app_session_events_port_preserves_raw_user_query_payload() -> None:
    table = FakeEventsTable(
        rows=[
            {
                "id": 4,
                "source": "User",
                "type": "query",
                "content": {
                    "content": "Explain FeO.",
                    "files": ["https://oss.example.com/input.cif"],
                    "images": ["https://oss.example.com/image.png"],
                    "workspace_paths": ["/share/result.xyz"],
                },
                "session_id": "sess-1",
                "task_id": "task-1",
                "invocation_id": "inv-1",
                "spawn_id": None,
            }
        ]
    )

    events = await AppSessionEventsPort(table).load_events(
        SessionEventQuery(session_id="sess-1", spawn_id=None)
    )

    assert events[0].event_type == "query"
    assert events[0].source == "User"
    assert events[0].content["content"] == "Explain FeO."
    assert events[0].content["files"] == ("https://oss.example.com/input.cif",)
    assert events[0].content["images"] == ("https://oss.example.com/image.png",)
    assert events[0].content["workspace_paths"] == ("/share/result.xyz",)


def test_freeze_json_object_rejects_non_json_schema_drift() -> None:
    with pytest.raises(TypeError, match="Unsupported JSON value type"):
        _freeze_json_object({"bad": object()})


@pytest.mark.asyncio
async def test_app_session_jobs_port_is_empty_placeholder() -> None:
    result = await AppSessionJobsPort().load_session_jobs(
        query=SessionJobsQuery(session_id="sess-1")
    )

    assert result == SessionJobs.empty()
```

- [ ] **Step 2: Add failing DAO SQL tests**

Append these tests to `tests/test_chat_events_history_checkpoint.py`:

```python
def test_query_context_events_builds_filtered_desc_query(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = [
        {
            "id": 10,
            "session_id": "sess-x",
            "source": "MatMaster",
            "type": "user_turn_context",
            "content": '{"kind": "anchor"}',
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "spawn_id": None,
            "created_at": None,
        }
    ]

    events = table.query_context_events(
        session_id="sess-x",
        spawn_id=None,
        until_event_id=20,
        event_types=("user_turn_context", "history_checkpoint"),
        limit=50,
        order="desc",
    )

    sql, params = cursor.execute.call_args[0]
    assert events[0]["id"] == 10
    assert events[0]["content"] == {"kind": "anchor"}
    assert "spawn_id IS NULL" in sql
    assert "id <= %s" in sql
    assert "type IN (%s, %s)" in sql
    assert "ORDER BY id DESC" in sql
    assert "LIMIT 50" in sql
    assert params == (
        "sess-x",
        20,
        "user_turn_context",
        "history_checkpoint",
    )


def test_query_context_events_supports_spawn_scope_and_ascending_order(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    events = table.query_context_events(
        session_id="sess-x",
        spawn_id="spawn-1",
        until_event_id=None,
        event_types=None,
        limit=None,
        order="asc",
    )

    sql, params = cursor.execute.call_args[0]
    assert events == []
    assert "spawn_id = %s" in sql
    assert "ORDER BY id ASC" in sql
    assert "type IN" not in sql
    assert "LIMIT" not in sql
    assert params == ("sess-x", "spawn-1")


def test_query_context_events_preserves_user_query_raw_payload(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = [
        {
            "id": 11,
            "session_id": "sess-x",
            "source": "User",
            "type": "query",
            "content": json.dumps(
                {
                    "content": "Explain FeO.",
                    "files": ["https://oss.example.com/input.cif"],
                    "images": ["https://oss.example.com/image.png"],
                    "workspace_paths": ["/share/result.xyz"],
                },
                ensure_ascii=False,
            ),
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "spawn_id": None,
            "created_at": None,
        }
    ]

    events = table.query_context_events(
        session_id="sess-x",
        spawn_id=None,
        until_event_id=None,
        event_types=None,
        limit=None,
        order="asc",
    )

    assert events[0]["content"]["content"] == "Explain FeO."
    assert events[0]["content"]["files"] == ["https://oss.example.com/input.cif"]
    assert events[0]["content"]["images"] == ["https://oss.example.com/image.png"]
    assert events[0]["content"]["workspace_paths"] == ["/share/result.xyz"]
    assert "files" not in events[0]
    assert "images" not in events[0]
```

The tests above use the existing `chat_events_table_with_mocks` fixture from `tests/conftest.py`, matching the current DAO test style in this repository.

- [ ] **Step 3: Verify tests are red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/test_chat_events_history_checkpoint.py::test_query_context_events_builds_filtered_desc_query \
  tests/test_chat_events_history_checkpoint.py::test_query_context_events_supports_spawn_scope_and_ascending_order \
  tests/test_chat_events_history_checkpoint.py::test_query_context_events_preserves_user_query_raw_payload \
  -q
```

Expected: import error for `src.services.context_assembly_ports` and missing `query_context_events`.

- [ ] **Step 4: Implement read-only DAO query**

Add this method to `src/dao/chat_events_table.py` near the existing Phase 1 context event read helpers:

```python
    @staticmethod
    def _row_to_context_event(row: dict) -> dict:
        """Parse DB event rows for context assembly without display flattening.

        Do not reuse `_row_to_event()` here: that helper intentionally flattens
        User/query payloads for frontend display, while ContextAssemblyPorts need
        the raw JSON payload shape for Phase 2B session source reconstruction.
        """
        try:
            content = json.loads(row['content'])
        except (json.JSONDecodeError, TypeError):
            content = row['content']

        event = {
            'id': row.get('id'),
            'source': row['source'],
            'type': row['type'],
            'content': content,
            'session_id': row['session_id'],
            'task_id': row.get('task_id'),
            'invocation_id': row.get('invocation_id'),
            'spawn_id': row.get('spawn_id'),
        }
        if row.get('created_at') is not None:
            event['created_at_ms'] = int(row['created_at'].timestamp() * 1000)
        return event

    def query_context_events(
        self,
        *,
        session_id: str,
        spawn_id: str | None,
        until_event_id: int | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int | None = None,
        order: str = "asc",
    ) -> list[dict]:
        """Read events for Phase 2 context assembly ports.

        This is a read-only helper. Phase 2A adds it for AppSessionEventsPort,
        but no runtime path calls the port until Phase 2C.
        """
        if order not in {"asc", "desc"}:
            raise ValueError("order must be 'asc' or 'desc'")

        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                params: tuple = (session_id,)
                if spawn_id is None:
                    spawn_filter = " AND spawn_id IS NULL"
                else:
                    spawn_filter = " AND spawn_id = %s"
                    params = (*params, spawn_id)

                boundary_filter = ""
                if until_event_id is not None:
                    boundary_filter = " AND id <= %s"
                    params = (*params, until_event_id)

                type_filter = ""
                if event_types:
                    placeholders = ", ".join(["%s"] * len(event_types))
                    type_filter = f" AND type IN ({placeholders})"
                    params = (*params, *event_types)

                order_sql = "DESC" if order == "desc" else "ASC"
                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      {spawn_filter}
                      {boundary_filter}
                      {type_filter}
                    ORDER BY id {order_sql}
                '''
                if limit:
                    sql += f' LIMIT {int(limit)}'
                cursor.execute(sql, params)
                return [
                    self._row_to_context_event(row)
                    for row in list(cursor.fetchall())
                ]
```

Keep DAO exception behavior unchanged: do not catch and swallow database errors.

- [ ] **Step 5: Implement `context_assembly_ports.py`**

Create `src/services/context_assembly_ports.py`:

```python
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from matmaster.context.ports import (
    JsonObject,
    JsonValue,
    SessionEvent,
    SessionEventQuery,
    SessionJobs,
    SessionJobsQuery,
    UserInstructions,
)

logger = logging.getLogger(__name__)
USER_INSTRUCTIONS_MAX_BYTES = 50 * 1024


def _hash_user_instructions(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def _freeze_json_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _freeze_json_value(inner)
            for key, inner in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json_value(inner) for inner in value)
    raise TypeError(
        f"Unsupported JSON value type in context event payload: {type(value)!r}"
    )


def _freeze_json_object(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        return {"value": _freeze_json_value(value)}
    return {
        str(key): _freeze_json_value(inner)
        for key, inner in value.items()
    }


class AppUserInstructionsPort:
    async def load_user_instructions(
        self,
        workspace_root: Path,
    ) -> UserInstructions:
        path = workspace_root / ".matmaster" / "AGENT.md"
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            return UserInstructions(
                text="",
                hash=_hash_user_instructions(""),
                truncated=False,
            )
        # Other IO / decoding errors intentionally propagate. Missing AGENT.md
        # is normal; unreadable or invalid files should not be silently ignored.

        text, truncated = _truncate_utf8(raw, USER_INSTRUCTIONS_MAX_BYTES)
        if truncated:
            logger.warning(
                "AGENT.md exceeds %d bytes; truncating user instructions",
                USER_INSTRUCTIONS_MAX_BYTES,
            )
        return UserInstructions(
            text=text,
            hash=_hash_user_instructions(text),
            truncated=truncated,
        )


class AppSessionEventsPort:
    def __init__(self, events_table: object) -> None:
        self._events_table = events_table

    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]:
        rows = await asyncio.to_thread(
            self._events_table.query_context_events,
            session_id=query.session_id,
            spawn_id=query.spawn_id,
            until_event_id=query.until_event_id,
            event_types=query.event_types,
            limit=query.limit,
            order=query.order,
        )
        return tuple(self._row_to_event(row) for row in rows)

    @staticmethod
    def _row_to_event(row: Mapping[str, Any]) -> SessionEvent:
        return SessionEvent(
            id=int(row.get("id") or 0),
            event_type=str(row.get("type") or row.get("event_type") or ""),
            source=row.get("source"),
            content=_freeze_json_object(row.get("content") or {}),
            task_id=row.get("task_id"),
            invocation_id=row.get("invocation_id"),
            spawn_id=row.get("spawn_id"),
        )


class AppSessionJobsPort:
    async def load_session_jobs(
        self,
        query: SessionJobsQuery,
    ) -> SessionJobs:
        return SessionJobs.empty()
```

- [ ] **Step 6: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/test_chat_events_history_checkpoint.py::test_query_context_events_builds_filtered_desc_query \
  tests/test_chat_events_history_checkpoint.py::test_query_context_events_supports_spawn_scope_and_ascending_order \
  tests/test_chat_events_history_checkpoint.py::test_query_context_events_preserves_user_query_raw_payload \
  -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  src/services/context_assembly_ports.py \
  src/dao/chat_events_table.py \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/test_chat_events_history_checkpoint.py && \
git commit -m "feat: add context assembly ports"
```

---

## Task 8: Phase Boundary Static Checks And Regression Verification

**Files:** no new source files

**Spec 依据:** DESIGN.md §4.2、§14 Phase 2A boundary、§16、附录 B「Phase 2A 改动」。

- [ ] **Step 1: Run Phase 2A unit test suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/context \
  tests/matmaster/services/test_context_assembly_ports.py \
  tests/matmaster/services/test_context_turn_intent.py \
  tests/test_chat_events_history_checkpoint.py \
  -q
```

Expected: all pass.

- [ ] **Step 2: Run Phase 1 regression tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  -q
```

Expected: all pass. This proves Phase 2A did not disturb Phase 1 user_turn_context runtime behavior.

- [ ] **Step 3: Compile new modules**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -m compileall -q matmaster/context src/services/context_assembly_ports.py src/services/context_turn_intent.py
```

Expected: command exits `0`.

- [ ] **Step 4: Verify runtime import boundary**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "matmaster\.context|from matmaster.context|import matmaster.context" src matmaster/core matmaster/manifests matmaster/types
```

Expected: matches are limited to:

```text
src/services/context_assembly_ports.py
src/services/context_turn_intent.py
```

There must be no matches in:

```text
src/services/agent_run_service.py
matmaster/core/agent.py
matmaster/core/context_compactor.py
matmaster/manifests/
matmaster/types/current_input.py
matmaster/types/context.py
```

- [ ] **Step 5: Verify Phase 2A excludes Phase 2B files**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && test ! -f matmaster/context/session.py && test ! -f matmaster/context/scanner.py && test ! -f matmaster/context/history_restore.py && test ! -f matmaster/context/sources/attachments.py && test ! -f matmaster/context/sources/skills.py && test ! -f matmaster/context/sources/tools.py
```

Expected: command exits `0`.

- [ ] **Step 6: Verify no legacy helper cleanup leaked into Phase 2A**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "COMPAT:legacy-runtime-injection-helper|_apply_user_instructions_to_initial_user_query" src/services/agent_run_instructions.py src/services/agent_run_service.py
```

Expected: matches still exist in `src/services/agent_run_instructions.py`. Phase 2C owns deleting this helper; Phase 2A must not delete it.

- [ ] **Step 7: Run broader focused suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/core/test_context_builder.py \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/manifests \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  -q
```

Expected: all pass. This checks that legacy builder, compactor, manifests, and checkpoint recovery remain untouched.

- [ ] **Step 8: Final status review**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git status --short
```

Expected: only Phase 2A files remain staged or committed according to the executor's commit strategy. The pre-existing `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, and `.planning/context-refactor/DESIGN.md` states must not be changed by Phase 2A execution.

This Task has no commit unless verification revealed a small fix. If a fix was needed, stage only the files changed by that fix and commit with `fix: stabilize context phase 2a tests`.

---

## Phase 2A Acceptance Checklist

- [ ] `matmaster/context/sections.py` enforces `ContextView.CHECKPOINT -> ContextView.RUNTIME`, non-empty key, and non-empty tag.
- [ ] `matmaster/context/rendering.py` is the only new module that knows XML-like tag wrapping and close-tag escape behavior.
- [ ] `UserTurnContext.from_sources()` raises on duplicate section keys.
- [ ] `TurnInput.to_sections()` defaults to the current Phase 1 prompt shape by merging attachments into `<current_instruction>`.
- [ ] `TurnInput.to_sections()` is pinned against `build_current_instruction_block(CurrentInputContext(...))` for Phase 1 prompt-shape equivalence.
- [ ] `TurnInput.to_sections(split_attachments=True)` keeps the future split attachment path testable.
- [ ] `UserInstructions.text` and `AppUserInstructionsPort` preserve raw trailing whitespace before hashing.
- [ ] `ContextComposition` constants are the only new declarations of anchor / continuation / compacted source selection.
- [ ] `ContextAssembler` only exposes the session-section builder as a test-only seam; production service wiring does not pass prebuilt `ContextSection` objects into assembler.
- [ ] `ContextAssembler.assemble_turn(CONTINUATION_TURN)` does not load session events.
- [ ] `ContextAssembler.assemble_compaction(RUNTIME_COMPACTION)` rejects missing `covered_until_event_id`.
- [ ] `resolve_turn_context_intent()` treats every `history_checkpoint` as a barrier, including checkpoints without `user_instructions_hash`.
- [ ] `resolve_turn_context_intent()` top-level tests cover no events, hash match, hash mismatch, checkpoint hash, and 50-event no-anchor fallback.
- [ ] `query_context_events()` preserves raw `User/query.content` payload and does not use `_row_to_event()` display flattening.
- [ ] `AppSessionEventsPort` maps DB rows into typed `SessionEvent` objects with frozen JSON lists converted to tuples.
- [ ] `AppSessionEventsPort` rejects unsupported non-JSON payload value types instead of silently stringifying schema drift.
- [ ] `AppSessionJobsPort` returns `SessionJobs.empty()` and does not inspect Bohrium configuration.
- [ ] `src/services/agent_run_service.py`, `matmaster/core/agent.py`, `matmaster/core/context_compactor.py`, and `matmaster/manifests/` have no `matmaster.context` import.
- [ ] Phase 2B files are absent after Phase 2A.

## Notes For Phase 2B

Phase 2B should replace the 2A empty default with the real `SessionContextBuilder(events=tuple[SessionEvent, ...])` implementation, then migrate `attachments.py` / `skills.py` / `tools.py` / `scanner.py` with golden master fixture comparisons against `matmaster/manifests/`. The `_session_section_builder_for_tests` seam is test-only; production service wiring must never pass prebuilt `ContextSection` objects into assembler. After the real builder is installed, keep the seam only if unit tests still need to inject synthetic sections, and do not expose it through platform ports.
