# Phase 1 事件语义 Implementation Plan (v3.3 修订)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 DESIGN.md v3.3 Phase 1：每个真实用户请求写入 `user_turn_context` provider-facing 事实记录，恢复路径按"纯 v0 / hybrid v1 / 纯 v1"三分支分流，内部事件不进入 SSE，AGENT.md 修改通过 hash anchor 在下一轮立即生效。

**v3.3 修订关键点** (相对 v3.2 plan)：

1. **Hybrid restore** (DESIGN.md §2 #4 / §11.1 / §11.2.1): 无 v1 checkpoint 但 events 含 `user_turn_context` 时,
   `_restore_v1` 必须按 `invocation_id` 决定是否消费老 `User/query`, 保证 Phase 1 部署前
   的 raw user turn 不丢。Task 5 落地该算法 + 测试。
2. **`user_turn_context` 应用层 dedup** (DESIGN.md §4.1 #1): 写入前按
   `(session_id, invocation_id, type, spawn_id IS NULL)` 查重；命中时 payload 一致才
   幂等 skip, 不一致 fail-fast。Task 4 加 DAO 方法
   `query_user_turn_context_by_invocation` 与 `has_user_turn_context`, Task 3/5 调用。
3. **`history_checkpoint` 无条件 barrier** (DESIGN.md §7bis.5):
   `latest_anchor_user_instructions_hash` 遇到 checkpoint 时无论 hash 是否存在都返回,
   不能"穿越" checkpoint 找老 anchor。
4. **`invocation_id` 缺失 fail-fast** (DESIGN.md §3.6): `write_user_turn_context_event`
   缺关联键时 raise 而非 silent skip; service 用 try/except 包装。
5. **`UserInstructionsInfo.text` 不 strip** (DESIGN.md §7bis.4): hash 基于 raw text,
   保留尾随空白与行尾差异。

**Architecture:** Phase 1 不引入 `matmaster/context/` 内核，也不切 `ContextSection` / `ContextAssembler`。本阶段用一个 service-local 过渡模块渲染当前 provider-facing user message：沿用现有 `_render_user_instructions_block()` 与 `ContextBuilder.build_user_request()` 的 prompt 形态，先写 `user_turn_context`，再把 `rendered_runtime_task`（包含 AGENT.md wrap, 不含 attachment_text）交给现有 `Exp.run_stream()`。Kernel 内部仍会调 `ContextBuilder.build_user_request(task, attachment_text)` 合并 attachments, 因此 `user_turn_context.payload["message"]["content"]`（= service 端的 `build_user_request(rendered_runtime_task, attachment_text)`）等于 kernel 实际发给 LLM 的 user content, 二者通过共享 `attachment_text` 保证一致。`src/services/model_history_restore_service.py` 提供 schema-aware restore（三分支），旧 `history_restore_service.py` 作为兼容 shim 保留。

**Tech Stack:** Python 3.11+ / uv / pytest / Pydantic message models / MySQL chat events DAO / Redis Worker queue / MatMaster event fanout

**Spec 来源:** `.planning/context-refactor/DESIGN.md` §0.0 (v3.3 changelog)、§2 #4、§3.2、§3.6、§4.1 #1、§8、§11、§11.2.1、§14 Phase 1、§16、§17、附录 B「Phase 1 改动」。

---

## 全局约束

1. `User/query` 继续只保存 raw transcript；不要把 AGENT.md、available attachments、summary 等 provider-facing 内容塞回 `User/query.content`。
2. `user_turn_context` 与 `User/query` 通过 events 表顶层 `session_id + spawn_id + invocation_id` 关联；payload 内不得新增 `source_query_event_id`。
3. Phase 1 只写 root turn 的 `user_turn_context`：`spawn_id=None`。子 agent checkpoint 语义不扩展。
4. **`user_turn_context` 写入 fail-fast (v3.3)**: 关联键 `invocation_id` 缺失 = 写入失败; DAO 返回 false = 写入失败; 两者都必须 raise 而非 silent skip。`run_agent` 在主路径 try/except 把异常包装成既有 `(False, msg)` 二元 tuple, 不允许裸抛到 stream layer。
5. **`user_turn_context` 应用层 dedup (v3.3)**: 写入前必须查重, 命中已有记录时必须把已存 payload 与本轮将写入的 payload 做结构化比较；一致则返回 duplicate/幂等 skip 且本轮 LLM 仍照跑, 不一致则 fail-fast。dedup 失败 (DAO 查询异常) 视为写入失败, 走 fail-fast 路径。
6. `history_checkpoint` 写入失败仍保持 best-effort；本计划只扩 payload 字段与 codec marker，不改变 compactor fallback 策略。
7. **Restore 三分支 (v3.3)**: 纯 v0 (无 utc 也无 v1 checkpoint) / hybrid v1 (有 utc 无 v1 checkpoint) / 纯 v1 (有 v1 checkpoint)。Hybrid v1 必须按 `invocation_id` 决定是否消费老 `User/query`, 保留 Phase 1 部署前的 raw user turn。
8. **`UserInstructionsInfo` 不 strip (v3.3)**: text 保留 raw text (仅 utf-8 截断), hash 基于 raw text。尾随空白 / 行尾差异不能被 strip 掩盖；raw text 变化应触发 anchor 重生成。
9. Prompt 形态不改：本阶段继续使用现有 `<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">` wrapper 与 `[Available attachments]` 文本。Phase 2/3 再切到 `ContextSection` 的 `<user_instructions>` / `<current_instruction>` 渲染。
10. `_apply_user_instructions_to_initial_user_query` 函数体可保留给旧测试与 Phase 2C 清理，但 Phase 1 完成后 `AgentRunService.run_agent()` 主路径不得再调用它。保留处必须带 `COMPAT:legacy-runtime-injection-helper` 注释。
11. **`run_meta` 不扩张 (v3.3)**: `run_meta['user_instructions']` 字段沿用兼容老测试; **不**新增 `user_instructions_hash` / `user_instructions_truncated` 字段。typed `UserInstructionsInfo` 在 service 内部传递, Phase 3 compactor 迁移时按 DESIGN.md v3.1 §9.2 直接传 typed object。
12. 所有 Python 命令使用 `uv run pytest`、`uv run python` 这类仓库 uv 环境入口。不要用系统 Python。
13. 当前工作树已有 `.planning/PROJECT.md`、`.planning/REQUIREMENTS.md`、`.planning/STATE.md` 删除状态；执行本计划时不要恢复或改写这些与 Phase 1 无关的用户改动。

## File Structure

- Create: `src/services/user_turn_context_service.py`
  Phase 1 过渡 helper：AGENT.md hash/cap、anchor 判定、provider-facing current task 渲染、payload 构造、fail-fast event 写入。
- Create: `src/services/model_history_restore_service.py`
  新的 backend model restore 服务，实现 v0/v1 schema-aware 分流。
- Modify: `src/services/history_restore_service.py`
  改为兼容 shim，re-export `ModelHistoryRestoreService` 为 `HistoryRestoreService`。
- Modify: `src/services/agent_run_history_wiring.py`
  从 `ModelHistoryRestoreService` 恢复历史；保留 `HistoryWiringResult` 接口。
- Modify: `src/services/agent_run_bohrium_stage.py`
  AGENT.md 读取走 50KB cap + hash helper；返回 typed instructions info。
- Modify: `src/services/agent_run_service.py`
  写 `user_turn_context`，把渲染后的 current task 交给 `Exp.run_stream()`；移除 runtime 主路径的 `_apply_user_instructions_to_initial_user_query()` 调用。
- Modify: `src/services/stream_sse_filter.py`
  replay SSE 隐藏 `user_turn_context`。
- Modify: `matmaster/integration/sse_handler.py`
  live SSE 隐藏 `user_turn_context`。
- Modify: `src/services/history_checkpoint_service.py`
  checkpoint sink 从 payload 透传 `schema_version`、`render_version`、`user_instructions_text`、`user_instructions_hash`。
- Modify: `src/services/history_checkpoint_codec.py`
  接受 `<previous_session_summary>` 与 `<compacted_history>` 双 marker，标记 `COMPAT:v0-checkpoint-marker`。
- Modify: `src/dao/chat_events_table.py`
  新增最近 anchor 查询；扩展 `add_history_checkpoint()` 字段；必要时让 scope query 支持 `after_id=None`。
- Tests:
  - Modify: `tests/test_stream_replay_skill_hit.py`
  - Modify: `tests/matmaster/integration/test_sse_skill_hit.py`
  - Create: `tests/matmaster/services/test_user_turn_context_service.py`
  - Modify: `tests/matmaster/services/test_history_checkpoint_service.py`
  - Modify: `tests/matmaster/services/test_history_checkpoint_codec.py`
  - Create or heavily modify: `tests/matmaster/services/test_model_history_restore_service.py`
  - Modify: `tests/matmaster/services/test_history_restore_service.py` only to assert shim compatibility
  - Modify: `tests/matmaster/services/test_agent_run_stream.py`
  - Modify: `tests/matmaster/services/test_user_instructions_runtime_injection.py`

---

### Task 1: Baseline And Existing-Shape Inventory

**Files:** read-only

- [ ] **Step 1: Confirm uv environment and current dirty files**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python -V && git status --short
```

Expected:

```text
Python 3.11+ or Python 3.13.x
D  .planning/PROJECT.md
D  .planning/REQUIREMENTS.md
D  .planning/STATE.md
```

`.planning/` is ignored by `.gitignore`; if you run `git status --short --ignored .planning/context-refactor/PHASE-1-PLAN.md`, this plan may appear as `!! .planning/context-refactor/PHASE-1-PLAN.md`. If there are additional unrelated modified files, do not revert them. Note them before starting code changes.

- [ ] **Step 2: Run focused baseline tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/services/test_history_restore_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_user_instructions_runtime_injection.py \
  -q
```

Expected: all pass. If not, stop and report the baseline failure before implementing Phase 1.

- [ ] **Step 3: Confirm no enum blocks `user_turn_context` writes**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "event_type|type IN|Literal\\[|user_turn_context" src/dao src/services matmaster/types matmaster/integration
```

Expected:

- `ChatEventsTable.add_event()` accepts arbitrary `event_type` string.
- `matmaster/types/events.py` does not need a `UserTurnContextEvent` for Phase 1 because `user_turn_context` is written by service/DAO, not emitted by kernel fanout.
- `stream_sse_filter.py` docstring already mentions Phase 1 hiding `user_turn_context`; implementation still needs the filter line.

This Task has no commit.

---

### Task 2: Hide `user_turn_context` From Replay And Live SSE

**Files:**
- Modify: `src/services/stream_sse_filter.py`
- Modify: `matmaster/integration/sse_handler.py`
- Modify: `tests/test_stream_replay_skill_hit.py`
- Modify: `tests/matmaster/integration/test_sse_skill_hit.py`

- [ ] **Step 1: Add failing replay filter test**

Append this test to `tests/test_stream_replay_skill_hit.py`:

```python
def test_should_not_emit_user_turn_context() -> None:
    from src.services.stream_service import _should_emit_event_to_sse

    event = {
        "type": "user_turn_context",
        "source": "MatMaster",
        "content": {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": {"role": "user", "content": "provider-facing"},
            "render_version": "user_context_render.v1",
        },
    }

    assert _should_emit_event_to_sse(event) is False
```

- [ ] **Step 2: Add failing live handler test**

Append this test to `tests/matmaster/integration/test_sse_skill_hit.py`:

```python
from types import SimpleNamespace


def test_should_skip_user_turn_context() -> None:
    from matmaster.integration.sse_handler import SSEHandler

    handler = SSEHandler(
        send_cb=lambda payload: None,
        session_id="s-1",
        task_id="t-1",
        invocation_id="inv-1",
        mode="direct",
    )
    event = SimpleNamespace(type="user_turn_context", source="MatMaster")

    assert handler._should_skip(event) is True
```

The `SimpleNamespace` is deliberate: Phase 1 does not add a Pydantic bus event class, but `_should_skip()` already uses `getattr(event, "type", "")`.

- [ ] **Step 3: Verify red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py::test_should_not_emit_user_turn_context \
  tests/matmaster/integration/test_sse_skill_hit.py::test_should_skip_user_turn_context \
  -q
```

Expected: both fail because `user_turn_context` is still emitted.

- [ ] **Step 4: Implement filters**

In `src/services/stream_sse_filter.py`, add `user_turn_context` to the internal hidden type set:

```python
    if t in {'assistant_state', 'skill_hit', 'user_turn_context'}:
        return False
```

Keep the existing `log_line` and checkpoint branches. Do not hide `tool_call` or `tool_result`.

In `matmaster/integration/sse_handler.py`, update the top docstring and add the live filter:

```python
        # user_turn_context is provider-facing restore metadata, never frontend SSE.
        if event_type == 'user_turn_context':
            return True
```

Place it next to the `assistant_state` / `skill_hit` internal-only checks.

- [ ] **Step 5: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py \
  tests/matmaster/integration/test_sse_handler_mode_filter.py \
  -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  src/services/stream_sse_filter.py \
  matmaster/integration/sse_handler.py \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py && \
git commit -m "fix: hide user turn context events from SSE"
```

---

### Task 3: Add Phase-1 User Turn Context Helper

**Files:**
- Create: `src/services/user_turn_context_service.py`
- Create: `tests/matmaster/services/test_user_turn_context_service.py`
- Modify: `src/services/agent_run_instructions.py`

**Spec 依据:** DESIGN.md §3.2、§8.2、§8.3、§8.6、§14 Phase 1b。

- [ ] **Step 1: Write failing helper tests**

Create `tests/matmaster/services/test_user_turn_context_service.py`:

```python
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, Mock

import pytest

from src.services.user_turn_context_service import (
    USER_INSTRUCTIONS_MAX_BYTES,
    UserInstructionsInfo,
    build_user_turn_context_payload,
    decide_user_turn_context_kind,
    hash_user_instructions,
    latest_anchor_user_instructions_hash,
    load_user_instructions_from_session,
    render_runtime_task_for_user_turn_context,
    write_user_turn_context_event,
)


def test_hash_user_instructions_uses_sha256_prefix() -> None:
    assert hash_user_instructions("").startswith("sha256:")
    assert hash_user_instructions("abc") == hash_user_instructions("abc")
    assert hash_user_instructions("abc") != hash_user_instructions("abcd")


def test_hash_user_instructions_does_not_strip_whitespace() -> None:
    """v3.3: raw text hash, 不能在 strip 后才 hash。

    理由 (DESIGN.md §7bis.4): 尾随空白 / 行尾差异不能被 strip 掩盖;
    raw text 变化应触发 anchor 重生成。strip 后 hash 会让
    `"Use SI units."` 与 `"Use SI units.\\n"` hash 相同, 但 anchor 冻结的
    文本不一致, 下次比对失败。
    """
    assert hash_user_instructions("Use SI units.") != hash_user_instructions("Use SI units.\n")
    assert hash_user_instructions(" abc ") != hash_user_instructions("abc")


def test_load_user_instructions_missing_file_returns_empty_hash() -> None:
    session = Mock()
    session.read_file.side_effect = FileNotFoundError("missing")

    info = load_user_instructions_from_session(session)

    assert info.text == ""
    assert info.hash == hash_user_instructions("")
    assert info.truncated is False


def test_load_user_instructions_truncates_by_utf8_bytes(caplog: pytest.LogCaptureFixture) -> None:
    session = Mock()
    session.read_file.return_value = "a" * (USER_INSTRUCTIONS_MAX_BYTES + 10)

    with caplog.at_level(logging.WARNING):
        info = load_user_instructions_from_session(session)

    assert len(info.text.encode("utf-8")) == USER_INSTRUCTIONS_MAX_BYTES
    assert info.truncated is True
    assert info.hash == hash_user_instructions(info.text)
    assert "AGENT.md exceeds" in caplog.text


def test_load_user_instructions_preserves_trailing_newline() -> None:
    """v3.3: raw text 进 info.text, 不 strip。"""
    session = Mock()
    session.read_file.return_value = "Use SI units.\n"

    info = load_user_instructions_from_session(session)

    assert info.text == "Use SI units.\n"
    assert info.hash == hash_user_instructions("Use SI units.\n")


def test_latest_anchor_hash_prefers_latest_user_turn_anchor() -> None:
    events = [
        {
            "type": "user_turn_context",
            "content": {
                "kind": "continuation",
                "user_instructions_hash": None,
            },
        },
        {
            "type": "user_turn_context",
            "content": {
                "kind": "anchor",
                "user_instructions_hash": "sha256:new",
            },
        },
        {
            "type": "history_checkpoint",
            "content": {"user_instructions_hash": "sha256:old"},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) == "sha256:new"


def test_latest_anchor_hash_uses_history_checkpoint_when_no_anchor_event() -> None:
    events = [
        {"type": "response", "content": "ignored"},
        {
            "type": "history_checkpoint",
            "content": {"user_instructions_hash": "sha256:checkpoint"},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) == "sha256:checkpoint"


def test_latest_anchor_hash_returns_none_when_checkpoint_lacks_hash() -> None:
    """v3.3: history_checkpoint 是无条件 barrier。

    Phase 1 期间 checkpoint 仍写 v0 marker (无 user_instructions_hash);
    扫到这样的 checkpoint 应当返回 None, 不能"穿越"它继续找老 anchor。
    否则 AGENT.md 改动在 compaction 后会失效。
    """
    events = [
        # 倒序: 最新是无 hash 的 checkpoint, 更老处有 matching anchor
        {
            "type": "history_checkpoint",
            "content": {"covered_until_event_id": 100, "base_messages": []},
        },
        {
            "type": "user_turn_context",
            "content": {
                "kind": "anchor",
                "user_instructions_hash": "sha256:same-as-current",
            },
        },
    ]

    # checkpoint barrier → None → 调用方判定 anchor (保守正确)
    assert latest_anchor_user_instructions_hash(events) is None


def test_latest_anchor_hash_returns_none_when_only_continuation_present() -> None:
    """continuation 不携带 hash, 不应错误返回 None 但扫到它时继续找老 anchor。"""
    events = [
        {
            "type": "user_turn_context",
            "content": {"kind": "continuation", "user_instructions_hash": None},
        },
    ]

    assert latest_anchor_user_instructions_hash(events) is None


def test_decide_kind_anchor_for_missing_or_changed_hash() -> None:
    assert decide_user_turn_context_kind("sha256:a", None) == "anchor"
    assert decide_user_turn_context_kind("sha256:a", "sha256:b") == "anchor"
    assert decide_user_turn_context_kind("sha256:a", "sha256:a") == "continuation"


def test_render_runtime_task_adds_instructions_only_for_anchor() -> None:
    info = UserInstructionsInfo(
        text="Prefer concise answers.",
        hash=hash_user_instructions("Prefer concise answers."),
    )

    anchor = render_runtime_task_for_user_turn_context(
        user_prompt="Explain FeO.",
        user_instructions=info,
        kind="anchor",
    )
    continuation = render_runtime_task_for_user_turn_context(
        user_prompt="Explain FeO.",
        user_instructions=info,
        kind="continuation",
    )

    assert anchor.startswith('<matmaster-user-instructions source="/personal/.matmaster/AGENT.md">')
    assert "Prefer concise answers." in anchor
    assert anchor.endswith("Explain FeO.")
    assert continuation == "Explain FeO."


def test_build_payload_freezes_user_message_and_anchor_hash() -> None:
    info = UserInstructionsInfo(text="Use SI units.", hash="sha256:abc")

    payload = build_user_turn_context_payload(
        kind="anchor",
        rendered_message_content="provider-facing content",
        images=[{"url": "https://oss.example.com/chat/current.png", "detail": "auto"}],
        user_instructions=info,
        transform="raw",
    )

    assert payload["schema_version"] == "user_turn_context.v1"
    assert payload["kind"] == "anchor"
    assert payload["message"]["role"] == "user"
    assert payload["message"]["content"] == "provider-facing content"
    assert payload["message"]["images"][0]["url"].endswith("current.png")
    assert payload["user_instructions_hash"] == "sha256:abc"
    assert payload["transform"] == "raw"
    assert payload["render_version"] == "user_context_render.v1"


def test_build_payload_omits_hash_for_continuation() -> None:
    info = UserInstructionsInfo(text="Use SI units.", hash="sha256:abc")

    payload = build_user_turn_context_payload(
        kind="continuation",
        rendered_message_content="current only",
        images=[],
        user_instructions=info,
        transform="raw",
    )

    assert payload["kind"] == "continuation"
    assert payload["user_instructions_hash"] is None


@pytest.mark.asyncio
async def test_write_user_turn_context_raises_when_invocation_id_missing() -> None:
    """v3.3 (DESIGN.md §3.6): 缺 invocation_id 视为关联键失败, fail-fast。

    silent skip 会让 hybrid restore 找不到该事件对应的 User/query, LLM 视图错位。
    """
    events_table = Mock()
    events_table.add_event = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(return_value=None)

    with pytest.raises(RuntimeError, match="requires invocation_id"):
        await write_user_turn_context_event(
            events_table=events_table,
            session_id="s1",
            task_id="t1",
            invocation_id=None,
            spawn_id=None,
            payload={"schema_version": "user_turn_context.v1"},
        )

    events_table.add_event.assert_not_called()


@pytest.mark.asyncio
async def test_write_user_turn_context_idempotent_skip_on_duplicate() -> None:
    """v3.3 (DESIGN.md §4.1 #1): 应用层 dedup; payload 一致才幂等 skip。

    Worker retry / 客户端重发场景常见, 但同一 invocation 的 provider-facing
    事实必须一致; payload drift 要 fail-fast。
    """
    payload = {"schema_version": "user_turn_context.v1"}
    events_table = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(
        return_value={"id": 42, "type": "user_turn_context", "content": payload}
    )
    events_table.add_event = Mock()

    status = await write_user_turn_context_event(
        events_table=events_table,
        session_id="s1",
        task_id="t1",
        invocation_id="inv-1",
        spawn_id=None,
        payload=payload,
    )

    events_table.query_user_turn_context_by_invocation.assert_called_once_with(
        "s1", "inv-1", None
    )
    assert status == "duplicate"
    events_table.add_event.assert_not_called()


@pytest.mark.asyncio
async def test_write_user_turn_context_raises_when_duplicate_payload_differs() -> None:
    """dedup 命中但 payload 不一致时 fail-fast, 防止同 invocation 事实漂移。"""
    events_table = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(
        return_value={
            "id": 42,
            "type": "user_turn_context",
            "content": {"schema_version": "user_turn_context.v1", "message": "old"},
        }
    )
    events_table.add_event = Mock()

    with pytest.raises(RuntimeError, match="payload differs"):
        await write_user_turn_context_event(
            events_table=events_table,
            session_id="s1",
            task_id="t1",
            invocation_id="inv-1",
            spawn_id=None,
            payload={"schema_version": "user_turn_context.v1", "message": "new"},
        )

    events_table.add_event.assert_not_called()


@pytest.mark.asyncio
async def test_write_user_turn_context_writes_when_no_duplicate() -> None:
    """正常路径: dedup miss → add_event 被调用。"""
    events_table = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(return_value=None)
    events_table.add_event = Mock(return_value=True)

    status = await write_user_turn_context_event(
        events_table=events_table,
        session_id="s1",
        task_id="t1",
        invocation_id="inv-1",
        spawn_id=None,
        payload={"schema_version": "user_turn_context.v1"},
    )

    assert status == "written"
    events_table.add_event.assert_called_once()


@pytest.mark.asyncio
async def test_write_user_turn_context_raises_when_add_event_returns_false() -> None:
    events_table = Mock()
    events_table.query_user_turn_context_by_invocation = Mock(return_value=None)
    events_table.add_event = Mock(return_value=False)

    with pytest.raises(RuntimeError, match="returned false"):
        await write_user_turn_context_event(
            events_table=events_table,
            session_id="s1",
            task_id="t1",
            invocation_id="inv-1",
            spawn_id=None,
            payload={"schema_version": "user_turn_context.v1"},
        )
```

- [ ] **Step 2: Verify red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_user_turn_context_service.py -q
```

Expected: import failure because `src/services/user_turn_context_service.py` does not exist.

- [ ] **Step 3: Mark legacy helper as compat**

In `src/services/agent_run_instructions.py`, update the module docstring with this sentence:

```text
COMPAT:legacy-runtime-injection-helper -- Phase 1 removes this helper from the
AgentRunService runtime main path, but the function stays importable for old
tests and for Phase 2C cleanup.
```

Do not change helper behavior in this step.

- [ ] **Step 4: Implement `src/services/user_turn_context_service.py`**

Create the module with these exact public names. **v3.3 修订点**已在内联注释标注。

```python
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

from matmaster.core.context_builder import ContextBuilder
from matmaster.types.messages import ImageContentPart, UserMessage
from src.services.agent_run_instructions import (
    _USER_INSTRUCTIONS_PATH,
    _render_user_instructions_block,
)

logger = logging.getLogger(__name__)

USER_INSTRUCTIONS_MAX_BYTES = 50 * 1024
USER_TURN_CONTEXT_SCHEMA_VERSION = "user_turn_context.v1"
USER_CONTEXT_RENDER_VERSION = "user_context_render.v1"
DEFAULT_TURN_TRANSFORM = "raw"  # Phase 1: 永远 raw; preflight / oversized 留给 Phase 3+
UserTurnContextKind = Literal["anchor", "continuation"]
UserTurnContextTransform = Literal["raw", "preflight_compacted", "oversized_summary"]
UserTurnContextWriteStatus = Literal["written", "duplicate"]


@dataclass(frozen=True)
class UserInstructionsInfo:
    text: str
    hash: str
    truncated: bool = False


def hash_user_instructions(text: str) -> str:
    """v3.3: raw text → sha256, 不做任何 normalize / strip (DESIGN.md §7bis.4)。"""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    truncated = raw[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True


def make_user_instructions_info(text: str | None, *, truncated: bool = False) -> UserInstructionsInfo:
    """v3.3: 不做 .strip(); raw text 直接进 UserInstructionsInfo, hash 基于 raw text。

    理由 (DESIGN.md §7bis.4): 尾随空白 / 行尾差异不能被 strip 掩盖;
    raw text 变化应触发 anchor 重生成。如果先 strip 再 hash,
    `"Use SI units."` 与 `"Use SI units.\\n"` hash 相同, 但 anchor 冻结
    的文本可能与文件实际内容不一致, 下次比对失败。
    """
    raw_text = text if text is not None else ""
    return UserInstructionsInfo(
        text=raw_text,
        hash=hash_user_instructions(raw_text),
        truncated=truncated,
    )


def load_user_instructions_from_session(session: Any | None) -> UserInstructionsInfo:
    if session is None:
        return make_user_instructions_info("")
    try:
        raw = session.read_file(_USER_INSTRUCTIONS_PATH)
    except Exception:
        return make_user_instructions_info("")

    text, truncated = _truncate_utf8(str(raw), USER_INSTRUCTIONS_MAX_BYTES)
    if truncated:
        logger.warning(
            "AGENT.md exceeds %d bytes, truncating user instructions",
            USER_INSTRUCTIONS_MAX_BYTES,
        )
    return make_user_instructions_info(text, truncated=truncated)


def latest_anchor_user_instructions_hash(events: list[dict[str, Any]]) -> str | None:
    """v3.3: history_checkpoint 是无条件 barrier (DESIGN.md §7bis.5)。

    遍历倒序事件流, 遇到任何 history_checkpoint 都终止扫描:
    - hash 存在: 返回 hash
    - hash 不存在 (Phase 1 v0 marker 期 / legacy checkpoint): 返回 None,
      调用方据此判定当前轮为 anchor (保守正确)。

    禁止"checkpoint hash 缺失就继续扫更老 anchor"的实现 - 那会让 AGENT.md
    改动在 compaction 后失效。
    """
    for event in events:
        event_type = (event.get("type") or "").strip()
        content = event.get("content") or {}
        if not isinstance(content, dict):
            continue
        if event_type == "user_turn_context":
            if content.get("kind") == "anchor":
                value = content.get("user_instructions_hash")
                return value if isinstance(value, str) and value else None
            # continuation: 不携带 hash, 继续向前扫
            continue
        if event_type == "history_checkpoint":
            # v3.3: barrier; 无 hash 时返回 None (调用方处理为 anchor)
            value = content.get("user_instructions_hash")
            return value if isinstance(value, str) and value else None
    return None


def decide_user_turn_context_kind(
    current_hash: str,
    latest_anchor_hash: str | None,
) -> UserTurnContextKind:
    if latest_anchor_hash is None or latest_anchor_hash != current_hash:
        return "anchor"
    return "continuation"


def render_runtime_task_for_user_turn_context(
    *,
    user_prompt: str,
    user_instructions: UserInstructionsInfo,
    kind: UserTurnContextKind,
) -> str:
    prompt = (user_prompt or "").strip()
    if kind == "anchor" and user_instructions.text.strip():
        return _render_user_instructions_block(
            user_instructions=user_instructions.text,
            user_query=prompt,
        )
    return prompt


def build_user_turn_context_payload(
    *,
    kind: UserTurnContextKind,
    rendered_message_content: str,
    images: list[dict[str, Any]],
    user_instructions: UserInstructionsInfo,
    transform: UserTurnContextTransform = DEFAULT_TURN_TRANSFORM,
) -> dict[str, Any]:
    image_parts = [ImageContentPart.model_validate(image) for image in images]
    message = UserMessage(
        content=rendered_message_content,
        images=image_parts,
    )
    return {
        "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
        "kind": kind,
        "message": message.model_dump(mode="json"),
        "user_instructions_hash": (
            user_instructions.hash if kind == "anchor" else None
        ),
        "transform": transform,
        "render_version": USER_CONTEXT_RENDER_VERSION,
    }


def render_provider_facing_current_message_content(
    *,
    rendered_runtime_task: str,
    attachment_text: str,
) -> str:
    return ContextBuilder().build_user_request(
        user_text=rendered_runtime_task,
        attachments=attachment_text,
    )


async def write_user_turn_context_event(
    *,
    events_table: Any,
    session_id: str,
    task_id: str | None,
    invocation_id: str | None,
    spawn_id: str | None,
    payload: dict[str, Any],
) -> UserTurnContextWriteStatus:
    """写 user_turn_context 事件 (v3.3)。

    硬约束 (DESIGN.md §3.6 / §4.1 #1):
    - `invocation_id` 缺失视为关联键失败, 立即 raise RuntimeError
      (silent skip 会让 hybrid restore 找不到该事件对应的 User/query, 模型视图错位)
    - 写入前必须做 Phase 1 应用层 dedup: 按
      `(session_id, invocation_id, type='user_turn_context', spawn_id IS NULL)` 查重,
      命中已有且 payload 完全一致则幂等 skip (不写, 返回 "duplicate")
      命中已有但 payload 不一致则 fail-fast, 防止同一 invocation 的 provider-facing
      事实漂移
    - 实际 events_table.add_event 返回 false → raise RuntimeError
    """
    if not invocation_id:
        raise RuntimeError(
            f"user_turn_context requires invocation_id "
            f"(session_id={session_id} task_id={task_id})"
        )

    # Phase 1 应用层 dedup: Worker retry / 客户端重发场景幂等。
    # query_user_turn_context_by_invocation 在 Task 4 Step 8 加到 ChatEventsTable。
    existing = await asyncio.to_thread(
        events_table.query_user_turn_context_by_invocation,
        session_id,
        invocation_id,
        spawn_id,
    )
    if existing is not None:
        existing_payload = existing.get("content") if isinstance(existing, dict) else None
        if existing_payload != payload:
            raise RuntimeError(
                "existing user_turn_context payload differs for invocation "
                f"(session_id={session_id} invocation_id={invocation_id} spawn_id={spawn_id})"
            )
        logger.warning(
            "user_turn_context already exists for this invocation with matching payload; "
            "idempotent skip session_id=%s invocation_id=%s spawn_id=%s",
            session_id,
            invocation_id,
            spawn_id,
        )
        return "duplicate"

    ok = await asyncio.to_thread(
        events_table.add_event,
        session_id,
        "MatMaster",
        "user_turn_context",
        payload,
        task_id=task_id,
        invocation_id=invocation_id,
        spawn_id=spawn_id,
    )
    if not ok:
        raise RuntimeError("user_turn_context write returned false")
    return "written"
```

**v3.3 修订摘要**（相对原 plan）：

1. `make_user_instructions_info`: 删 `.strip()`，hash 基于 raw text（DESIGN.md §7bis.4）
2. `latest_anchor_user_instructions_hash`: `history_checkpoint` 无条件 barrier（DESIGN.md §7bis.5）
3. `write_user_turn_context_event`:
   - 缺 `invocation_id` 从 silent return 改成 raise（DESIGN.md §3.6）
   - 新增应用层 dedup 调用 `query_user_turn_context_by_invocation`（Task 4 提供，DESIGN.md §4.1 #1）
   - dedup 命中时比较已存 payload 与本轮 payload；一致返回 `"duplicate"`，不一致 fail-fast
4. 引入 `DEFAULT_TURN_TRANSFORM = "raw"` 常量替代硬编码字符串
5. 删除原"`invocation_id` guard 是给直接测试用"那条不准确的注释

- [ ] **Step 5: Verify helper tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_user_turn_context_service.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  src/services/user_turn_context_service.py \
  src/services/agent_run_instructions.py \
  tests/matmaster/services/test_user_turn_context_service.py && \
git commit -m "feat: add user turn context event helpers"
```

---

### Task 4: Extend ChatEventsTable And History Checkpoint Payloads

**Files:**
- Modify: `src/dao/chat_events_table.py`
- Modify: `src/services/history_checkpoint_service.py`
- Modify: `src/services/history_checkpoint_codec.py`
- Modify: `tests/test_chat_events_history_checkpoint.py`
- Modify: `tests/matmaster/services/test_history_checkpoint_service.py`
- Modify: `tests/matmaster/services/test_history_checkpoint_codec.py`

**Spec 依据:** DESIGN.md §3.3、§8.3、§11.5、§14 Phase 1c。

- [ ] **Step 1: Add failing codec test for v1 marker**

Append to `tests/matmaster/services/test_history_checkpoint_codec.py`:

```python
def test_validate_base_messages_accepts_compacted_history_marker() -> None:
    validate_base_messages(
        [
            UserMessage(
                content=(
                    "<user_instructions>\nUse SI units.\n</user_instructions>"
                    "\n\n<compacted_history>\nsummary\n</compacted_history>"
                )
            )
        ]
    )
```

- [ ] **Step 2: Add failing checkpoint payload passthrough test**

Append to `tests/matmaster/services/test_history_checkpoint_service.py`:

```python
    async def test_checkpoint_sink_passes_v1_payload_metadata(self) -> None:
        from src.services.history_checkpoint_service import HistoryCheckpointService

        events_table = Mock()
        events_table.add_history_checkpoint.return_value = True
        fanout = Mock()
        fanout.flush_persistence_barrier = AsyncMock()
        sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
            fanout=fanout,
            session_id="s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
        )
        base_messages = _compact_base_messages("summary")

        await sink(
            payload={
                "durability": "durable",
                "strategy": "summary",
                "covered_until_event_id": 41,
                "schema_version": "history_checkpoint.v1",
                "render_version": "user_context_render.v1",
                "user_instructions_text": "Use SI units.",
                "user_instructions_hash": "sha256:abc",
            },
            base_messages=base_messages,
        )

        events_table.add_history_checkpoint.assert_called_once_with(
            "s1",
            task_id="t1",
            invocation_id="i1",
            spawn_id=None,
            covered_until_event_id=41,
            base_messages=base_messages,
            reason="summary",
            schema_version="history_checkpoint.v1",
            render_version="user_context_render.v1",
            user_instructions_text="Use SI units.",
            user_instructions_hash="sha256:abc",
        )
```

- [ ] **Step 3: Add failing DAO payload test**

Extend `tests/test_chat_events_history_checkpoint.py::_call_add_history_checkpoint()` to pass the new kwargs in one test path:

```python
        schema_version="history_checkpoint.v1",
        render_version="user_context_render.v1",
        user_instructions_text="Use SI units.",
        user_instructions_hash="sha256:abc",
```

Then assert the row content includes:

```python
    assert row["content"]["schema_version"] == "history_checkpoint.v1"
    assert row["content"]["render_version"] == "user_context_render.v1"
    assert row["content"]["user_instructions_text"] == "Use SI units."
    assert row["content"]["user_instructions_hash"] == "sha256:abc"
```

If the current test helper makes this awkward, add a new test named `test_add_history_checkpoint_writes_v1_metadata_fields()` with the same fake cursor setup as the existing param-count tests.

- [ ] **Step 4: Verify red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_history_checkpoint_codec.py::test_validate_base_messages_accepts_compacted_history_marker \
  tests/matmaster/services/test_history_checkpoint_service.py::TestHistoryCheckpointService::test_checkpoint_sink_passes_v1_payload_metadata \
  tests/test_chat_events_history_checkpoint.py \
  -q
```

Expected: failures for marker and unexpected kwargs.

- [ ] **Step 5: Implement dual marker validation**

In `src/services/history_checkpoint_codec.py`, add marker constants near the top:

```python
MARKERS_V0 = {"<previous_session_summary>"}
MARKERS_V1 = {"<compacted_history>"}


def _has_acceptable_marker(content: str) -> bool:
    # COMPAT:v0-checkpoint-marker -- keep accepting v0 marker until Phase 4.
    return any(marker in content for marker in MARKERS_V0 | MARKERS_V1)
```

Replace the final marker check with:

```python
    first_content = (messages[0].content or "").strip()
    if not _has_acceptable_marker(first_content):
        raise ValueError(
            "checkpoint base_messages[0] must contain compact context bundle marker"
        )
```

- [ ] **Step 6: Extend checkpoint sink**

In `src/services/history_checkpoint_service.py`, update the `add_history_checkpoint` call:

```python
                schema_version=payload.get("schema_version"),
                render_version=payload.get("render_version"),
                user_instructions_text=payload.get("user_instructions_text"),
                user_instructions_hash=payload.get("user_instructions_hash"),
```

Do not require these fields yet; Phase 3 is when writes switch to v1 marker.

- [ ] **Step 7: Extend DAO checkpoint payload**

In `src/dao/chat_events_table.py`, extend `add_history_checkpoint()` signature:

```python
        schema_version: str | None = None,
        render_version: str | None = None,
        user_instructions_text: str | None = None,
        user_instructions_hash: str | None = None,
```

Build payload as:

```python
        checkpoint_content = {
            'covered_until_event_id': covered_until_event_id,
            'base_messages': base_messages,
            'reason': reason,
        }
        if schema_version is not None:
            checkpoint_content['schema_version'] = schema_version
        if render_version is not None:
            checkpoint_content['render_version'] = render_version
        if user_instructions_text is not None:
            checkpoint_content['user_instructions_text'] = user_instructions_text
        if user_instructions_hash is not None:
            checkpoint_content['user_instructions_hash'] = user_instructions_hash
```

Also extend `add_checkpoint_pair()` to accept and pass the same four kwargs.

- [ ] **Step 8: Add recent context-anchor DAO query**

Add this method to `ChatEventsTable`. Phase 1 仅 root spawn 写入 `user_turn_context` (DESIGN.md §4.1 #1), `spawn_id IS NULL` 是固定语义; Phase 2A 起改为按真实 spawn_id 查询时统一替换为 `SessionEventsPort`。

```python
    def get_recent_context_anchor_events(
        self,
        session_id: str,
        spawn_id: str | None,
        limit: int = 50,
    ) -> list[dict]:
        """Phase 1 临时方法; Phase 2A 由 SessionEventsPort 取代。"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params = (session_id,)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (session_id, spawn_id)

                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND type IN ('user_turn_context', 'history_checkpoint')
                      {spawn_filter}
                    ORDER BY id DESC
                '''
                if limit:
                    sql += f' LIMIT {int(limit)}'
                cursor.execute(sql, params)
                return [self._row_to_event(row) for row in list(cursor.fetchall())]
```

- [ ] **Step 8.5: Add user_turn_context dedup and EXISTS queries (v3.3)**

Add another method to `ChatEventsTable`. 用于 Phase 1 应用层 dedup (DESIGN.md §4.1 #1)：

```python
    def query_user_turn_context_by_invocation(
        self,
        session_id: str,
        invocation_id: str,
        spawn_id: str | None,
    ) -> dict | None:
        """Phase 1 dedup 查询: 按 (session_id, invocation_id, spawn_id) 找已写的
        user_turn_context 事件。命中返回 row dict, 否则 None。

        Phase 1 仅 root spawn 写入, 实际查询固定 spawn_id IS NULL; Phase 2A 起放开。
        DB 层 unique index 留待 Phase 1.5 / 2A 通过 migration 添加。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params = (session_id, invocation_id)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (session_id, invocation_id, spawn_id)

                sql = f'''
                    SELECT id, session_id, source, type, content, task_id, invocation_id, spawn_id, created_at
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND invocation_id = %s
                      AND type = 'user_turn_context'
                      {spawn_filter}
                    ORDER BY id ASC
                    LIMIT 1
                '''
                cursor.execute(sql, params)
                row = cursor.fetchone()
                return self._row_to_event(row) if row else None

    def has_user_turn_context(
        self,
        session_id: str,
        spawn_id: str | None,
    ) -> bool:
        """Phase 1 restore 分流查询: session/scope 内是否存在 user_turn_context。

        不要用 get_session_events(limit=N) 做探测；该 DAO 返回最早的 N 条事件，
        长 session 会漏掉后续 Phase 1 写入的 user_turn_context。
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params = (session_id,)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (session_id, spawn_id)

                sql = f'''
                    SELECT 1
                    FROM {self.table_name}
                    WHERE session_id = %s
                      AND type = 'user_turn_context'
                      {spawn_filter}
                    LIMIT 1
                '''
                cursor.execute(sql, params)
                return cursor.fetchone() is not None
```

Add unit test in `tests/test_chat_events_history_checkpoint.py` (or a new dedicated test file):

```python
def test_query_user_turn_context_by_invocation_returns_existing_row(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    """v3.3 dedup query: 已写入的 user_turn_context 能被查到。"""
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "id": 42,
        "session_id": "sess-x",
        "source": "MatMaster",
        "type": "user_turn_context",
        "content": '{"schema_version": "user_turn_context.v1"}',
        "task_id": "task-1",
        "invocation_id": "inv-1",
        "spawn_id": None,
        "created_at": None,
    }

    event = table.query_user_turn_context_by_invocation("sess-x", "inv-1", None)

    assert event is not None
    assert event["id"] == 42
    assert event["type"] == "user_turn_context"
    assert event["invocation_id"] == "inv-1"
    assert event["content"] == {"schema_version": "user_turn_context.v1"}
    sql, params = cursor.execute.call_args[0]
    assert "type = 'user_turn_context'" in sql
    assert "spawn_id IS NULL" in sql
    assert params == ("sess-x", "inv-1")


def test_query_user_turn_context_by_invocation_returns_none_when_missing(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    """没有匹配行时返回 None。"""
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = None

    assert table.query_user_turn_context_by_invocation("sess-x", "inv-1", None) is None


def test_has_user_turn_context_uses_exists_query(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    """restore 三分支探测必须用 EXISTS, 不能用 earliest-N get_session_events。"""
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {"1": 1}

    assert table.has_user_turn_context("sess-x", None) is True

    sql, params = cursor.execute.call_args[0]
    assert "SELECT 1" in sql
    assert "type = 'user_turn_context'" in sql
    assert "spawn_id IS NULL" in sql
    assert params == ("sess-x",)


def test_has_user_turn_context_returns_false_when_missing(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = None

    assert table.has_user_turn_context("sess-x", None) is False
```

These tests should also flow into Task 9's broader suite.

- [ ] **Step 9: Verify green**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_chat_events_history_checkpoint.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  -q
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  src/dao/chat_events_table.py \
  src/services/history_checkpoint_service.py \
  src/services/history_checkpoint_codec.py \
  tests/test_chat_events_history_checkpoint.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py && \
git commit -m "feat: extend checkpoint metadata for context restore"
```

---

### Task 5: Implement `ModelHistoryRestoreService` v0/v1 Split

**Files:**
- Create: `src/services/model_history_restore_service.py`
- Modify: `src/services/history_restore_service.py`
- Modify: `src/services/agent_run_history_wiring.py`
- Create: `tests/matmaster/services/test_model_history_restore_service.py`
- Modify: `tests/matmaster/services/test_history_restore_service.py`
- Modify: `tests/matmaster/integration/test_history_checkpoint_recovery.py`

**Spec 依据:** DESIGN.md §11、§14 Phase 1d、§16 restore tests。

- [ ] **Step 1: Write failing v1 restore tests**

Create `tests/matmaster/services/test_model_history_restore_service.py`:

```python
from __future__ import annotations

from matmaster.types.messages import AssistantMessage, ToolCallData, ToolMessage, UserMessage
from src.services.model_history_restore_service import ModelHistoryRestoreService


def _utc(content: str, *, event_id: int, task_id: str | None = None) -> dict:
    return {
        "id": event_id,
        "source": "MatMaster",
        "type": "user_turn_context",
        "task_id": task_id,
        "spawn_id": None,
        "content": {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(content=content).model_dump(mode="json"),
            "user_instructions_hash": "sha256:abc",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
    }


def _assistant_state(*, event_id: int, call_id: str = "call-1") -> dict:
    return {
        "id": event_id,
        "source": "MatMaster",
        "type": "assistant_state",
        "task_id": "task-old",
        "spawn_id": None,
        "content": {
            "state": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "name": "bash",
                        "arguments": {"cmd": "pwd"},
                    }
                ],
            }
        },
    }


def _tool_result(*, event_id: int, call_id: str = "call-1") -> dict:
    return {
        "id": event_id,
        "source": "MatMaster",
        "type": "tool_result",
        "task_id": "task-old",
        "spawn_id": None,
        "content": {
            "call_id": call_id,
            "tool_name": "bash",
            "result": "ok",
        },
    }


def _response(content: str, *, event_id: int, task_id: str | None = "task-old") -> dict:
    return {
        "id": event_id,
        "source": "MatMaster",
        "type": "response",
        "task_id": task_id,
        "spawn_id": None,
        "content": {"content": content, "reasoning_content": "because"},
    }


class FakeEventsTable:
    def __init__(
        self,
        *,
        checkpoints: list[dict] | None = None,
        scope_events: list[dict] | None = None,
        session_events: list[dict] | None = None,
    ) -> None:
        self.checkpoints = checkpoints or []
        self.scope_events = scope_events or []
        self.session_events = session_events or []
        self.calls: list[tuple] = []

    def get_history_checkpoints(self, session_id: str, spawn_id: str | None, limit: int = 5) -> list[dict]:
        self.calls.append(("get_history_checkpoints", session_id, spawn_id, limit))
        return list(self.checkpoints)

    def get_scope_events_after_id(self, session_id: str, spawn_id: str | None, after_id: int | None, limit: int | None = None) -> list[dict]:
        self.calls.append(("get_scope_events_after_id", session_id, spawn_id, after_id, limit))
        return list(self.scope_events)

    def get_session_events(self, session_id: str, limit: int | None = None, include_spawn: bool = False) -> list[dict]:
        self.calls.append(("get_session_events", session_id, limit, include_spawn))
        return list(self.session_events)

    def has_user_turn_context(self, session_id: str, spawn_id: str | None) -> bool:
        self.calls.append(("has_user_turn_context", session_id, spawn_id))
        return any(
            event.get("type") == "user_turn_context"
            and (
                (spawn_id is None and event.get("spawn_id") is None)
                or (spawn_id is not None and event.get("spawn_id") == spawn_id)
            )
            for event in self.session_events
        )


def test_no_checkpoint_without_user_turn_context_uses_legacy_restore() -> None:
    table = FakeEventsTable(
        session_events=[
            {"source": "User", "type": "query", "content": "raw question", "task_id": "old"},
            {"source": "MatMaster", "type": "response", "content": "raw answer", "task_id": "old"},
        ]
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    assert [message.role for message in history] == ["user", "assistant"]


def test_no_checkpoint_with_user_turn_context_uses_v1_restore() -> None:
    table = FakeEventsTable(
        session_events=[
            _utc("provider question", event_id=10),
            _response("provider answer", event_id=11),
        ],
        scope_events=[
            _utc("provider question", event_id=10),
            _response("provider answer", event_id=11),
        ],
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    assert isinstance(history[0], UserMessage)
    assert history[0].content == "provider question"
    assert isinstance(history[1], AssistantMessage)
    assert history[1].content == "provider answer"


def test_v1_checkpoint_restores_base_messages_then_tail_events() -> None:
    checkpoint = {
        "id": 7,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": 7,
            "base_messages": [UserMessage(content="<compacted_history>\nold\n</compacted_history>").model_dump(mode="json")],
        },
    }
    table = FakeEventsTable(
        checkpoints=[checkpoint],
        scope_events=[_utc("tail question", event_id=8), _response("tail answer", event_id=9)],
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    assert [message.role for message in history] == ["user", "user", "assistant"]
    assert "old" in (history[0].content or "")
    assert history[1].content == "tail question"


def test_v1_restore_consumes_assistant_state_and_tool_result() -> None:
    table = FakeEventsTable(
        session_events=[_utc("run tool", event_id=1), _assistant_state(event_id=2), _tool_result(event_id=3)],
        scope_events=[_utc("run tool", event_id=1), _assistant_state(event_id=2), _tool_result(event_id=3)],
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    assert isinstance(history[1], AssistantMessage)
    assert history[1].tool_calls is not None
    assert history[1].tool_calls[0].id == "call-1"
    assert isinstance(history[2], ToolMessage)
    assert history[2].tool_call_id == "call-1"


def test_v1_restore_excludes_current_task_events() -> None:
    table = FakeEventsTable(
        session_events=[
            _utc("old question", event_id=1, task_id="old-task"),
            _response("old answer", event_id=2, task_id="old-task"),
            _utc("current question", event_id=3, task_id="current-task"),
        ],
        scope_events=[
            _utc("old question", event_id=1, task_id="old-task"),
            _response("old answer", event_id=2, task_id="old-task"),
            _utc("current question", event_id=3, task_id="current-task"),
        ],
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id="current-task",
    )

    assert [message.content for message in history if isinstance(message, UserMessage)] == ["old question"]


def test_v1_checkpoint_with_null_boundary_falls_back_to_legacy() -> None:
    table = FakeEventsTable(
        checkpoints=[
            {
                "id": 1,
                "content": {
                    "schema_version": "history_checkpoint.v1",
                    "covered_until_event_id": None,
                    "base_messages": [UserMessage(content="<compacted_history>\nbad\n</compacted_history>").model_dump(mode="json")],
                },
            }
        ],
        session_events=[
            {"source": "User", "type": "query", "content": "legacy question", "task_id": "old"},
        ],
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    assert len(history) == 1
    assert isinstance(history[0], UserMessage)
    assert history[0].content == "legacy question"


def test_hybrid_v1_keeps_pre_phase1_user_query_without_utc() -> None:
    """v3.3 hybrid (DESIGN.md §2 #4, §11.2.1):

    混合 session - 部署前有 raw User/query, 部署后追加 user_turn_context。
    Hybrid v1 必须保留老 User/query 而不是跳过, 否则模型视图丢失老用户输入。
    """
    table = FakeEventsTable(
        # 没有 v1 checkpoint
        session_events=[
            # 部署前: 老 User/query 没有对应 user_turn_context
            {
                "source": "User", "type": "query",
                "content": "old raw question",
                "task_id": "old-task", "invocation_id": "inv-old",
                "spawn_id": None, "id": 1,
            },
            _response("old answer", event_id=2, task_id="old-task"),
            # 部署后: User/query + user_turn_context 都有
            {
                "source": "User", "type": "query",
                "content": "new raw question",
                "task_id": "new-task", "invocation_id": "inv-new",
                "spawn_id": None, "id": 3,
            },
            {
                "id": 4, "source": "MatMaster", "type": "user_turn_context",
                "task_id": "new-task", "invocation_id": "inv-new", "spawn_id": None,
                "content": {
                    "schema_version": "user_turn_context.v1",
                    "kind": "anchor",
                    "message": UserMessage(content="new rendered question with instructions").model_dump(mode="json"),
                    "user_instructions_hash": "sha256:new",
                    "transform": "raw",
                    "render_version": "user_context_render.v1",
                },
            },
            _response("new answer", event_id=5, task_id="new-task"),
        ],
        scope_events=[],  # populated below
    )
    table.scope_events = list(table.session_events)

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    # 期望: 老 User/query 走 legacy → UserMessage; 新 User/query 被 utc 覆盖, 跳过
    user_messages = [m for m in history if isinstance(m, UserMessage)]
    assert len(user_messages) == 2
    assert user_messages[0].content == "old raw question"
    assert user_messages[1].content == "new rendered question with instructions"


def test_hybrid_v1_skips_covered_user_query() -> None:
    """v3.3 hybrid: 有对应 user_turn_context 的 User/query 必须跳过, 否则重复消费。"""
    table = FakeEventsTable(
        session_events=[
            {
                "source": "User", "type": "query",
                "content": "raw form",
                "task_id": "t1", "invocation_id": "inv-1",
                "spawn_id": None, "id": 1,
            },
            {
                "id": 2, "source": "MatMaster", "type": "user_turn_context",
                "task_id": "t1", "invocation_id": "inv-1", "spawn_id": None,
                "content": {
                    "schema_version": "user_turn_context.v1",
                    "kind": "anchor",
                    "message": UserMessage(content="rendered form with instructions").model_dump(mode="json"),
                    "user_instructions_hash": "sha256:abc",
                    "transform": "raw",
                    "render_version": "user_context_render.v1",
                },
            },
        ],
    )
    table.scope_events = list(table.session_events)

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    user_messages = [m for m in history if isinstance(m, UserMessage)]
    assert len(user_messages) == 1
    assert user_messages[0].content == "rendered form with instructions"


def test_hybrid_v1_keeps_pre_phase1_user_query_without_invocation_id() -> None:
    """老 session 可能完全没有 invocation_id 字段 (Phase 1 之前)。

    应当也按 legacy 转 UserMessage (保守消费), 不应因为缺少 invocation_id 就丢失。
    """
    table = FakeEventsTable(
        session_events=[
            {
                "source": "User", "type": "query",
                "content": "very old question",
                "task_id": "ancient",
                "id": 1,
                # 缺 invocation_id, 缺 spawn_id
            },
            {
                "id": 2, "source": "MatMaster", "type": "user_turn_context",
                "task_id": "new-task", "invocation_id": "inv-new", "spawn_id": None,
                "content": {
                    "schema_version": "user_turn_context.v1",
                    "kind": "anchor",
                    "message": UserMessage(content="new question").model_dump(mode="json"),
                    "user_instructions_hash": "sha256:abc",
                    "transform": "raw",
                    "render_version": "user_context_render.v1",
                },
            },
        ],
    )
    table.scope_events = list(table.session_events)

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    user_messages = [m for m in history if isinstance(m, UserMessage)]
    assert len(user_messages) == 2
    assert user_messages[0].content == "very old question"
    assert user_messages[1].content == "new question"
```

注：旧 plan Step 1 中的 `test_v1_restore_excludes_current_task_events` 测试覆盖的 task_id 过滤场景独立于 hybrid 判定，应当与上述 hybrid 测试**共存**：task_id 过滤排除 Worker retry / 测试 fixture 残留事件，hybrid 判定决定 User/query 是否消费。两者正交。

- [ ] **Step 2: Verify red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest tests/matmaster/services/test_model_history_restore_service.py -q
```

Expected: import failure because `model_history_restore_service.py` does not exist.

- [ ] **Step 3: Implement model restore service (v3.3 hybrid)**

Create `src/services/model_history_restore_service.py` with:

- `class ModelHistoryRestoreService`
- public method `restore_history(session_id, spawn_id, task_id, raw_limit=None) -> list[Message]`
- helper `_restore_legacy`
- helper `_restore_v1` （含 hybrid 分支）
- helper `_event_to_v1_message`
- helper `_session_has_user_turn_context(session_id, spawn_id)`（调用 DAO 的 EXISTS 查询）

Required behavior (与 DESIGN.md §2 #4 / §11.1 / §11.2.1 一致 - 三分支分流):

```text
1. Load latest checkpoints via events_table.get_history_checkpoints(session_id, spawn_id, limit=5).
2. 三分支判定:
   a. 存在 v1 checkpoint (schema_version == "history_checkpoint.v1"):
      → 纯 v1 路径 (checkpoint != None, hybrid_mode=False)
   b. 无 v1 checkpoint 但 events 中存在 user_turn_context:
      → hybrid v1 路径 (checkpoint=None, hybrid_mode=True)
   c. 无 v1 checkpoint 且无 user_turn_context:
      → legacy 路径 (COMPAT:v0-restore, 委托 ChatHistoryConverter)
3. 判定 (b) 使用 `events_table.has_user_turn_context(session_id, spawn_id)`；
   不要用 `get_session_events(limit=raw_limit)` 探测，因为现有 DAO 返回最早的 N 条事件，
   长 session 会漏掉后续 Phase 1 写入的 `user_turn_context`。
4. v1 路径 (含 hybrid) 调 get_scope_events_after_id(session_id, spawn_id, after_id); 当 DAO 支持
   after_id=None 时直接传 None (Step 6 让 DAO 支持)。
5. v1 restore 算法 (与 DESIGN.md §11.1 _restore_v1 对齐):
   - 如果有 v1 checkpoint, 先 deserialize base_messages;
   - 如果 v1 checkpoint 的 covered_until_event_id is None, 视为 checkpoint 损坏, fall back legacy
     (COMPAT:v0-restore);
   - 如果 hybrid_mode=True, 先扫一遍事件流收集 covered_invocation_ids
     (所有 user_turn_context 的 invocation_id 集合);
   - 过滤掉 task_id 等于 current task_id 的事件 (Worker retry / 测试 fixture 场景, 与 hybrid
     covered_invocation_ids 判定独立);
   - 遍历事件:
     * user_turn_context → 追加 UserMessage(message)
     * source == "User" and type == "query":
         - 纯 v1 模式 (hybrid_mode=False): 跳过 (covered_until_event_id 之前的已在
           base_messages, 之后的必然有对应 user_turn_context)
         - hybrid 模式且 invocation_id ∈ covered_invocation_ids: 跳过 (被 utc 覆盖)
         - hybrid 模式且 invocation_id ∉ covered_invocation_ids: 按 legacy 转 UserMessage
           (保留 Phase 1 部署前的 raw user turn, COMPAT:hybrid-restore)
     * assistant_state → restore_persisted_assistant_state()
     * response/run_result/finish → AssistantMessage
     * tool_result → ToolMessage
     * thought/skill_hit/compaction/history_checkpoint/context_compaction → skip
6. Return trim_history_images(history).
```

Use existing imports from `src/services/history_restore_service.py`, plus:

```python
from matmaster.types.message_normalization import restore_persisted_assistant_state
from matmaster.types.messages import AssistantMessage, ImageContentPart, Message, ToolMessage, UserMessage
```

When converting `tool_result`, support the current persisted public payload:

```python
ToolMessage(
    content=str(payload.get("result", "")),
    tool_call_id=str(payload.get("call_id") or payload.get("id") or ""),
    tool_name=str(payload.get("tool_name") or payload.get("name") or ""),
)
```

When converting `response` / `run_result` / `finish`, support both string content and dict content:

```python
if isinstance(payload, dict):
    content = payload.get("content") or payload.get("final_content") or payload.get("text") or ""
    reasoning = payload.get("reasoning_content")
else:
    content = str(payload or "")
    reasoning = None
```

When converting hybrid-mode `User/query` to UserMessage (COMPAT:hybrid-restore):

```python
# v3.3 hybrid: 仅当 hybrid_mode=True 且 invocation_id 不在 covered_invocation_ids 时执行
if isinstance(payload, dict):
    user_text = payload.get("content") or ""
    image_urls = event.get("images")
    if image_urls is None:
        image_urls = payload.get("images") or []
else:
    user_text = str(payload or "")
    image_urls = event.get("images") or []
history.append(UserMessage(
    content=str(user_text),
    images=[ImageContentPart(url=u) for u in image_urls if u],
))
```

实施者注意：

- **请先 grep 确认现行代码用的是 `events_to_dialog_messages` 还是 `events_to_messages`**：DESIGN.md §11.3 写的是 `events_to_dialog_messages`，但原 plan Step 3 描述写了 `events_to_messages`。落地时以现行代码为准，两处之一是 spec/plan 笔误。
- `ChatHistoryConverter` 的 import path 沿用现行 `history_restore_service.py` 内部的写法。

- [ ] **Step 4: Add old-service shim**

Replace `src/services/history_restore_service.py` with a thin compatibility module:

```python
from __future__ import annotations

from src.services.model_history_restore_service import ModelHistoryRestoreService

HistoryRestoreService = ModelHistoryRestoreService

__all__ = ["HistoryRestoreService", "ModelHistoryRestoreService"]
```

This keeps existing imports working while making the new name available.

- [ ] **Step 5: Update history wiring import**

In `src/services/agent_run_history_wiring.py`, replace:

```python
from src.services.history_restore_service import HistoryRestoreService
```

with:

```python
from src.services.model_history_restore_service import ModelHistoryRestoreService
```

and replace the call:

```python
ModelHistoryRestoreService(events_table).restore_history(
```

- [ ] **Step 6: Make DAO scope query accept `after_id=None`**

In `src/dao/chat_events_table.py`, change `get_scope_events_after_id()` signature:

```python
        after_id: int | None,
```

Build the SQL boundary condition conditionally:

```python
                after_filter = ''
                if after_id is not None:
                    after_filter = ' AND id > %s'
```

Then set params as:

```python
                if spawn_id is None:
                    spawn_filter = ' AND spawn_id IS NULL'
                    params = (session_id,) if after_id is None else (session_id, after_id)
                else:
                    spawn_filter = ' AND spawn_id = %s'
                    params = (
                        (session_id, spawn_id)
                        if after_id is None
                        else (session_id, spawn_id, after_id)
                    )
```

Include `{after_filter}` in the SQL after the spawn filter. Keep the existing `type NOT IN ('history_checkpoint', 'compaction', 'context_compaction')`.

- [ ] **Step 7: Verify restore tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_history_restore_service.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  -q
```

Expected: all pass. Existing history checkpoint recovery tests should keep passing through the shim.

- [ ] **Step 8: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  src/services/model_history_restore_service.py \
  src/services/history_restore_service.py \
  src/services/agent_run_history_wiring.py \
  src/dao/chat_events_table.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_history_restore_service.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py && \
git commit -m "feat: restore model history from user turn context"
```

---

### Task 6: Load AGENT.md Once With Cap And Return Instructions Info

**Files:**
- Modify: `src/services/agent_run_bohrium_stage.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`
- Modify: `tests/matmaster/services/test_user_instructions_runtime_injection.py`

**Spec 依据:** DESIGN.md §4.1 #7、§8.2、§8.6。

- [ ] **Step 1: Update `BohriumStageResult`**

In `src/services/agent_run_bohrium_stage.py`, import:

```python
from src.services.user_turn_context_service import (
    UserInstructionsInfo,
    load_user_instructions_from_session,
)
```

Change dataclass field:

```python
    user_instructions: UserInstructionsInfo
```

For abort result, return:

```python
            user_instructions=load_user_instructions_from_session(None),
```

For normal path, replace the direct `read_file()` block with:

```python
    _ui_session = (
        bohrium_result.execution_session if bohrium_result else None
    ) or pg_ctx.session
    user_instructions = load_user_instructions_from_session(_ui_session)
```

- [ ] **Step 2: Update `AgentRunService` current usage**

In `src/services/agent_run_service.py`, after the `run_bohrium_stage` call assigns `stage_result`, keep `user_instructions` as the typed `UserInstructionsInfo` object:

```python
            user_instructions = stage_result.user_instructions
```

Then update existing run_meta write — **v3.3 修订**：保持 `run_meta['user_instructions']` 兼容老测试与下游代码，但**不**再往 `run_meta` 塞 hash / truncated 字段。理由：

- Phase 1 没有任何 kernel-side 消费者需要 `user_instructions_hash`（compactor 迁移留给 Phase 3）
- DESIGN.md §17.9 明确 `run_meta` 是 god bag，未来要 typed 化；Phase 1 不应反向扩张
- service 内自己持有的 typed `UserInstructionsInfo` 已足够，无需通过 `pg_ctx.run_meta` 转发

```python
            pg_ctx = pg_ctx.with_run_meta(
                figure_upload_config=figure_upload_config,
                user_instructions=user_instructions.text,
            )
            # 注: hash / truncated 不进 run_meta; Phase 3 compactor 迁移时通过
            # UserInstructions typed object 直接传 (DESIGN.md v3.1 §9.2)
```

- [ ] **Step 3: Update tests that assume string stage result**

In `tests/matmaster/services/test_user_instructions_runtime_injection.py`, existing assertions should keep checking `run_meta['user_instructions'] == 'Prefer concise answers.'`.

**v3.3 修订**：原 plan 在此处建议加 `assert run_meta["user_instructions_hash"].startswith("sha256:")` 这条断言；Step 2 已不再往 `run_meta` 塞 hash 字段，因此 **删除该断言**。Hash 的等价性测试已经在 `tests/matmaster/services/test_user_turn_context_service.py::test_hash_user_instructions_*` 与 `test_run_agent_writes_user_turn_context_*`（Task 7 Step 1）中覆盖。

In `tests/matmaster/services/test_agent_run_stream.py`, if `_patched_service()` creates a fake `bohrium_result` without `runtime_snapshot`, no change should be needed because `run_bohrium_stage()` now calls `load_user_instructions_from_session(pg_ctx.session)`, and the fake session already supports `read_file()`.

- [ ] **Step 4: Verify**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_user_instructions_runtime_injection.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  -q
```

Expected: all pass before runtime cutover in Task 7.

- [ ] **Step 5: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  src/services/agent_run_bohrium_stage.py \
  src/services/agent_run_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_user_instructions_runtime_injection.py && \
git commit -m "feat: hash user instructions during agent setup"
```

---

### Task 7: Write `user_turn_context` Before `Exp.run_stream`

**Files:**
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`
- Modify: `tests/matmaster/services/test_user_instructions_runtime_injection.py`

**Spec 依据:** DESIGN.md §3.2、§3.4、§3.6、§8.2、§14 Phase 1b。

- [ ] **Step 1: Add failing test for event write and runtime task (v3.3 修订)**

Append to `tests/matmaster/services/test_agent_run_stream.py`. **v3.3 修订要点**:

- 原断言 `payload["message"]["content"] == svc._test_fake_exp.last_task` 只在空附件下成立 (kernel 内部会再调 `build_user_request(task, attachment_text)`)。改为分别断言:
  - `payload["message"]["content"]` 是经 `build_user_request` 包装过的完整 provider-facing content
  - `svc._test_fake_exp.last_task` 是不含附件的 rendered_runtime_task
  - 二者通过显式 `build_user_request` 调用建立联系
- 必须新增 `query_user_turn_context_by_invocation.return_value = None` 才能走真正 write 路径 (默认 Mock 行为返回 Mock 实例而非 None, dedup 会误判命中)

```python
@pytest.mark.asyncio
async def test_run_agent_writes_user_turn_context_and_passes_same_runtime_task():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_pg_ctx.session.read_file.return_value = "Prefer concise answers."
        svc._test_events_table.get_recent_context_anchor_events.return_value = []
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = None

        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="first question",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-1",
        )

    assert ok is True
    svc._test_events_table.add_event.assert_any_call(
        "sess-1",
        "MatMaster",
        "user_turn_context",
        ANY,
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )
    payload = [
        call.args[3]
        for call in svc._test_events_table.add_event.call_args_list
        if call.args[2] == "user_turn_context"
    ][0]
    assert payload["schema_version"] == "user_turn_context.v1"
    assert payload["kind"] == "anchor"
    # rendered_runtime_task 进 Exp (含 AGENT.md wrap, 不含 attachments)
    assert "Prefer concise answers." in svc._test_fake_exp.last_task
    # payload.message.content 进 event (含 AGENT.md wrap, 可能含 attachments)
    # 空 attachments 场景: 二者相等
    assert payload["message"]["content"] == svc._test_fake_exp.last_task
    assert payload["user_instructions_hash"].startswith("sha256:")


@pytest.mark.asyncio
async def test_run_agent_user_turn_context_records_full_provider_facing_with_attachments():
    """v3.3 (DESIGN.md §3.2): user_turn_context.message 是 provider-facing UserMessage
    的事实记录, 含附件/图片完整字段。

    本测试覆盖非空 attachments 场景, 防止"`payload.content == last_task`"这种只在
    空附件下成立的脆弱断言。
    """
    from matmaster.core.context_builder import ContextBuilder
    from matmaster.types.current_input import CurrentInputContext

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_pg_ctx.session.read_file.return_value = "Use SI units."
        svc._test_events_table.get_recent_context_anchor_events.return_value = []
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = None
        current_input_context = CurrentInputContext.from_values(
            user_text="Compare FeO vs Fe2O3 from these files",
            files=["s3://input/feo.cif", "s3://input/fe2o3.cif"],
            images=["s3://input/struct1.png"],
            workspace_paths=["/workspace/notes.md"],
        )

        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="Compare FeO vs Fe2O3 from these files",
            images=["s3://input/struct1.png"],
            current_input_context=current_input_context,
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-att",
            invocation_id="inv-att",
        )

    assert ok is True
    payload = [
        call.args[3]
        for call in svc._test_events_table.add_event.call_args_list
        if call.args[2] == "user_turn_context"
    ][0]

    # 1. content 含 user_instructions + user_text + attachment_text 全套
    assert "Use SI units." in payload["message"]["content"]
    assert "feo.cif" in payload["message"]["content"]
    assert "notes.md" in payload["message"]["content"]
    # 2. images 完整保留
    image_urls = [img["url"] for img in payload["message"]["images"]]
    assert "s3://input/struct1.png" in image_urls
    # 3. kernel 实际拿到的 task 仅包含 instructions wrap (不含 attachment_text)
    assert "Use SI units." in svc._test_fake_exp.last_task
    assert "feo.cif" not in svc._test_fake_exp.last_task
    # 4. payload.message.content 可由 build_user_request(last_task, attachment_text) 重现
    #    这里直接断言 attachment_text 出现在 content 中即可; 完整等价由 kernel 路径保证
    expected_content = ContextBuilder().build_user_request(
        user_text=svc._test_fake_exp.last_task,
        attachments=svc._test_fake_exp.last_attachment_text,
    )
    assert payload["message"]["content"] == expected_content
```

注：本测试要求 `_FakeExp` 暴露 `last_attachment_text`。如果当前 fake 没有这个 attribute，应在 fake exp 上加：

```python
self.last_attachment_text: str = ""

# run_stream 里, self.last_ctx 赋值之后:
self.last_attachment_text = (
    (getattr(self.last_ctx, "run_meta", {}) or {}).get("attachment_manifest", "")
)
```

- [ ] **Step 2: Add failing continuation test**

Append to the same file. **v3.3 修订**: 加 `query_user_turn_context_by_invocation` mock。

```python
@pytest.mark.asyncio
async def test_run_agent_writes_continuation_when_instruction_hash_matches():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_pg_ctx.session.read_file.return_value = "Stable preference."

        from src.services.user_turn_context_service import hash_user_instructions

        svc._test_events_table.get_recent_context_anchor_events.return_value = [
            {
                "type": "user_turn_context",
                "content": {
                    "kind": "anchor",
                    "user_instructions_hash": hash_user_instructions("Stable preference."),
                },
            }
        ]
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = None

        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="follow up",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-2",
            invocation_id="inv-2",
        )

    assert ok is True
    payload = [
        call.args[3]
        for call in svc._test_events_table.add_event.call_args_list
        if call.args[2] == "user_turn_context"
    ][0]
    assert payload["kind"] == "continuation"
    assert payload["user_instructions_hash"] is None
    assert svc._test_fake_exp.last_task == "follow up"
```

- [ ] **Step 3: Add failing fail-fast test (v3.3 修订)**

Append to the same file. **v3.3 修订要点**：

- 不再用 `add_event.return_value = False`（那只测 DAO 写失败）；改用 `query_user_turn_context_by_invocation` 不命中 + `add_event.return_value = False` 模拟"应用层 dedup 通过但 DAO 写失败"
- 测试 fail-fast 包装为 `(False, msg)` 二元 tuple 的语义需要 `run_agent` 有相应的 try/except 包装（见 Step 6 实现）

```python
@pytest.mark.asyncio
async def test_run_agent_aborts_when_user_turn_context_write_fails():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_pg_ctx.session.read_file.return_value = "Use SI units."
        svc._test_events_table.get_recent_context_anchor_events.return_value = []
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = None
        svc._test_events_table.add_event.return_value = False

        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="first question",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-1",
        )

    # run_agent 返回 (ok, elapsed_ms); 失败时 ok = (False, error_msg) 二元 tuple
    assert ok[0] is False
    assert "user_turn_context write returned false" in ok[1]
    assert svc._test_fake_exp.last_task is None


@pytest.mark.asyncio
async def test_run_agent_aborts_when_invocation_id_missing():
    """v3.3 (DESIGN.md §3.6): invocation_id 缺失 → user_turn_context 不能写 → fail-fast。

    silent skip 会让 hybrid restore 找不到该事件对应的 User/query。
    """
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_pg_ctx.session.read_file.return_value = "Use SI units."
        svc._test_events_table.get_recent_context_anchor_events.return_value = []
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = None

        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="first question",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id=None,
        )

    assert ok[0] is False
    assert "invocation_id" in ok[1]
    # Exp.run_stream 不能被调用 - LLM 不能在没写 utc 的情况下跑
    assert svc._test_fake_exp.last_task is None


@pytest.mark.asyncio
async def test_run_agent_idempotent_skip_when_user_turn_context_already_exists():
    """v3.3 (DESIGN.md §4.1 #1): Worker retry 同 invocation 命中 dedup, 不写新 event,
    但本轮 LLM 调用仍然进行 (kernel render 与之前一致, 与已写入的 message.content 等价)。
    """
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_pg_ctx.session.read_file.return_value = "Use SI units."
        svc._test_events_table.get_recent_context_anchor_events.return_value = []
        # 已有同 invocation_id 的 user_turn_context (上次 Worker 写过, 现在 retry)。
        # payload 必须与本轮渲染结果一致, write helper 才允许幂等 skip。
        from src.services.user_turn_context_service import (
            DEFAULT_TURN_TRANSFORM,
            build_user_turn_context_payload,
            make_user_instructions_info,
            render_provider_facing_current_message_content,
            render_runtime_task_for_user_turn_context,
        )

        info = make_user_instructions_info("Use SI units.")
        rendered_task = render_runtime_task_for_user_turn_context(
            user_prompt="first question",
            user_instructions=info,
            kind="anchor",
        )
        existing_payload = build_user_turn_context_payload(
            kind="anchor",
            rendered_message_content=render_provider_facing_current_message_content(
                rendered_runtime_task=rendered_task,
                attachment_text="",
            ),
            images=[],
            user_instructions=info,
            transform=DEFAULT_TURN_TRANSFORM,
        )
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = {
            "id": 99,
            "type": "user_turn_context",
            "invocation_id": "inv-1",
            "content": existing_payload,
        }

        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="first question",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-1",
        )

    assert ok is True
    # dedup 命中 → add_event 不应被以 user_turn_context 类型调用
    utc_calls = [
        c for c in svc._test_events_table.add_event.call_args_list
        if c.args[2] == "user_turn_context"
    ]
    assert utc_calls == []
    # Exp.run_stream 仍然以 rendered task 跑
    assert svc._test_fake_exp.last_task is not None
    assert "Use SI units." in svc._test_fake_exp.last_task
```

- [ ] **Step 4: Update old runtime-injection tests to new semantics**

In `tests/matmaster/services/test_user_instructions_runtime_injection.py`:

- Keep pure helper tests unchanged.
- Rename runtime tests to mention `user_turn_context` runtime task.
- For first-turn runtime test, assert `last_task` starts with `_USER_INSTRUCTIONS_START`, as before.
- For restored-history test, change expectation: the current runtime task is the anchor when hash changed or no anchor found, rather than rewriting the first historical user message.

Use this replacement for the second async test body:

```python
    assert ok is True
    assert svc._test_fake_exp.last_task.startswith(_USER_INSTRUCTIONS_START)
    assert "Prefer concise answers." in svc._test_fake_exp.last_task
    sent_history = svc._test_fake_exp.last_run_kwargs["history"]
    assert sent_history == restored_history
    assert restored_history[1].content == "first question"
```

- [ ] **Step 5: Verify red**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_writes_user_turn_context_and_passes_same_runtime_task \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_user_turn_context_records_full_provider_facing_with_attachments \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_writes_continuation_when_instruction_hash_matches \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_aborts_when_user_turn_context_write_fails \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_aborts_when_invocation_id_missing \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_idempotent_skip_when_user_turn_context_already_exists \
  tests/matmaster/services/test_user_instructions_runtime_injection.py \
  -q
```

Expected: new tests fail because `AgentRunService` does not write `user_turn_context` yet.

- [ ] **Step 6: Implement run_agent cutover (v3.3 修订)**

In `src/services/agent_run_service.py`, import:

```python
from src.services.user_turn_context_service import (
    DEFAULT_TURN_TRANSFORM,
    build_user_turn_context_payload,
    decide_user_turn_context_kind,
    latest_anchor_user_instructions_hash,
    render_provider_facing_current_message_content,
    render_runtime_task_for_user_turn_context,
    write_user_turn_context_event,
)
```

Remove `_apply_user_instructions_to_initial_user_query` from the runtime main path. Keep its import with `# noqa: F401` only if tests still import it through `agent_run_service.py`. **测试只应通过 `src.services.agent_run_instructions` 直接 import** `_USER_INSTRUCTIONS_START` 等私有常量 (见 Task 7 Step 4)。

**插入点 A：在现有 vision handling 前初始化 `current_user_images_payload`，并在 image branch 内赋值。**

```python
            current_user_images_payload: list[dict[str, Any]] = []
            current_images = list(images or [])
            if current_images:
                # ... existing vision model selection ...
                image_parts: list[dict[str, Any]] = []
                for image_url in current_images:
                    image_part: dict[str, Any] = {"url": image_url}
                    if selected_profile.vision_detail is not None:
                        image_part["detail"] = selected_profile.vision_detail
                    image_parts.append(image_part)
                current_user_images_payload = image_parts
                pg_ctx = pg_ctx.with_run_meta(current_user_images=image_parts)
```

**插入点 B：在 Stage 5 history wiring 已经产出 `history` 和 `attachment_text` 之后、旧 Stage 5b runtime injection 调用的位置。** 删除旧 `_apply_user_instructions_to_initial_user_query(...)` 调用，用下面的 cutover block 替代：

```python
            # === v3.3 user_turn_context cutover (Phase 1) ===
            recent_context_events = []
            try:
                recent_context_events = events_table.get_recent_context_anchor_events(
                    session_id,
                    None,  # Phase 1: spawn_id IS NULL (DESIGN.md §4.1 #1)
                    limit=50,
                )
            except Exception:
                logger.warning(
                    "user_turn_context: latest anchor query failed; treating current turn as anchor",
                    exc_info=True,
                )
            latest_hash = latest_anchor_user_instructions_hash(recent_context_events)
            user_turn_kind = decide_user_turn_context_kind(
                user_instructions.hash,
                latest_hash,
            )
            rendered_runtime_task = render_runtime_task_for_user_turn_context(
                user_prompt=user_prompt,
                user_instructions=user_instructions,
                kind=user_turn_kind,
            )
            rendered_message_content = render_provider_facing_current_message_content(
                rendered_runtime_task=rendered_runtime_task,
                attachment_text=attachment_text,
            )
            user_turn_payload = build_user_turn_context_payload(
                kind=user_turn_kind,
                rendered_message_content=rendered_message_content,
                images=current_user_images_payload,
                user_instructions=user_instructions,
                transform=DEFAULT_TURN_TRANSFORM,
            )
            # v3.3 (DESIGN.md §3.6): write_user_turn_context_event 可能 raise:
            #   - RuntimeError("requires invocation_id"): 缺关联键
            #   - RuntimeError("returned false"): DAO 写失败
            # 必须按 run_agent 既有失败返回模式包装为 (False, msg) 二元 tuple,
            # 不允许异常裸抛到 stream layer (会让 SSE 客户端只看到连接 drop)。
            try:
                await write_user_turn_context_event(
                    events_table=events_table,
                    session_id=session_id,
                    task_id=task_id,
                    invocation_id=invocation_id,
                    spawn_id=None,  # Phase 1 仅 root spawn
                    payload=user_turn_payload,
                )
            except RuntimeError as e:
                logger.exception(
                    "user_turn_context write failed; aborting turn session_id=%s invocation_id=%s",
                    session_id,
                    invocation_id,
                )
                return ((False, str(e)), _elapsed_ms())

            # 即使 dedup 命中 (write helper 已确认 payload 一致并返回 duplicate),
            # 也要把 user_prompt 重赋值为 rendered_runtime_task, 让 kernel 拿到的 task
            # 与已存 utc.message.content 保持同一渲染来源 (减去 attachments 的差异)。
            user_prompt = rendered_runtime_task
```

Delete the old Stage 5b call:

```python
            user_prompt, history = _apply_user_instructions_to_initial_user_query(
                user_prompt=user_prompt,
                user_instructions=user_instructions,
                history=history,
            )
```

Do not delete the helper function yet; that is Phase 2C cleanup.

**对返回签名 `(False, str)` 的额外说明**: 现有 `run_agent` 返回结构为 `tuple[bool | tuple[bool, str], int]`（核查 `agent_run_service.py:625,629,638`）。成功时 first slot 是 `True`；失败时是 `(False, error_msg)` 二元 tuple。本 Step 的失败返回沿用既有模式，并使用入口附近已有的 `_elapsed_ms()` helper。

- [ ] **Step 7: Verify tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_user_instructions_runtime_injection.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  src/services/agent_run_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_user_instructions_runtime_injection.py && \
git commit -m "feat: record user turn context before agent runs"
```

---

### Task 8: Integration And Compatibility Coverage

**Files:**
- Modify: `tests/matmaster/integration/test_history_checkpoint_recovery.py`
- Modify: `tests/test_chat_stream_direct.py`
- Modify: `tests/test_chat_stream_reply_events.py`

- [ ] **Step 1: Add restore roundtrip integration case**

In `tests/matmaster/integration/test_history_checkpoint_recovery.py`, add a test using the existing fake table style:

```python
def test_restore_v1_roundtrip_from_user_turn_context_event():
    table = FakeEventsTable()
    table.add_event(
        "sess-v1",
        "MatMaster",
        "user_turn_context",
        {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(content="provider-facing question").model_dump(mode="json"),
            "user_instructions_hash": "sha256:abc",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )
    table.add_event(
        "sess-v1",
        "MatMaster",
        "response",
        {"content": "answer"},
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="sess-v1",
        spawn_id=None,
        task_id=None,
    )

    assert [message.role for message in history] == ["user", "assistant"]
    assert history[0].content == "provider-facing question"
```

If `FakeEventsTable` in that file does not have `add_event`, use its existing event append helper and preserve the same payload shape.

- [ ] **Step 1b: Add dedup roundtrip integration case (v3.3)**

In the same file, add a test that verifies repeating the same invocation doesn't produce two user messages in restore:

```python
def test_restore_v1_dedup_keeps_single_user_message_on_worker_retry():
    """v3.3 (DESIGN.md §4.1 #1): Worker retry 同 invocation 应当幂等; restore 时
    模型视图不应看到两条相同 user message。

    本测试模拟 service 层 dedup 已经生效的状态 (events 表里只有一条 utc),
    验证 restore 行为正确。dedup 本身的单元测试在
    test_user_turn_context_service.py::test_write_user_turn_context_idempotent_skip_on_duplicate。
    """
    table = FakeEventsTable()
    # 同 invocation_id 只写一条 (dedup 已生效)
    table.add_event(
        "sess-dup",
        "MatMaster",
        "user_turn_context",
        {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(content="single question").model_dump(mode="json"),
            "user_instructions_hash": "sha256:abc",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )
    table.add_event(
        "sess-dup",
        "MatMaster",
        "response",
        {"content": "single answer"},
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="sess-dup",
        spawn_id=None,
        task_id=None,
    )

    user_messages = [m for m in history if isinstance(m, UserMessage)]
    assert len(user_messages) == 1
    assert user_messages[0].content == "single question"


def test_restore_v1_hybrid_mixed_session_preserves_pre_phase1_user_query():
    """v3.3 (DESIGN.md §2 #4, §11.2.1): 混合 session 不应丢失 Phase 1 部署前的
    raw User/query。

    这是 plan review P1.1 的核心场景: 老 raw User/query + 新 user_turn_context
    共存时, restore 必须保留两者, 否则 LLM 视图丢失老用户输入。
    """
    table = FakeEventsTable()
    # 部署前: 老 User/query + response, 没有对应 user_turn_context
    table.add_event(
        "sess-mix",
        "User",
        "query",
        {"content": "old raw question"},
        task_id="old-task",
        invocation_id="inv-old",
        spawn_id=None,
    )
    table.add_event(
        "sess-mix",
        "MatMaster",
        "response",
        {"content": "old answer"},
        task_id="old-task",
        invocation_id="inv-old",
        spawn_id=None,
    )
    # 部署后: User/query 仍写, 同时新增 user_turn_context
    table.add_event(
        "sess-mix",
        "User",
        "query",
        {"content": "new raw question"},
        task_id="new-task",
        invocation_id="inv-new",
        spawn_id=None,
    )
    table.add_event(
        "sess-mix",
        "MatMaster",
        "user_turn_context",
        {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(content="new rendered question with instructions").model_dump(mode="json"),
            "user_instructions_hash": "sha256:new",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        task_id="new-task",
        invocation_id="inv-new",
        spawn_id=None,
    )
    table.add_event(
        "sess-mix",
        "MatMaster",
        "response",
        {"content": "new answer"},
        task_id="new-task",
        invocation_id="inv-new",
        spawn_id=None,
    )

    history = ModelHistoryRestoreService(table).restore_history(
        session_id="sess-mix",
        spawn_id=None,
        task_id=None,
    )

    user_messages = [m for m in history if isinstance(m, UserMessage)]
    # 老 raw User/query 走 legacy → "old raw question"
    # 新 User/query 被 user_turn_context 覆盖 → "new rendered question with instructions"
    assert len(user_messages) == 2
    assert user_messages[0].content == "old raw question"
    assert user_messages[1].content == "new rendered question with instructions"
```

- [ ] **Step 2: Assert queued payload still carries raw input context**

In `tests/test_chat_stream_direct.py`, keep `current_input_context` assertions raw:

```python
assert ctx.current_input_context.user_text == "analyze current"
assert ctx.user_msg["content"] == "analyze current"
```

Add one assertion that `user_msg` does not contain `user_turn_context` content:

```python
assert "schema_version" not in ctx.user_msg
```

This protects invariant #1: `User/query` remains raw.

- [ ] **Step 3: Verify Worker payload still serializes current input**

In `tests/test_chat_stream_reply_events.py`, keep the existing `current_input_context` field-name test. Add:

```python
assert "current_input_context" in field_names
assert "invocation_id" in field_names
```

This matters because `user_turn_context` association depends on Worker receiving the same `invocation_id`.

- [ ] **Step 4: Run integration set**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_reply_events.py \
  -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git add \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_reply_events.py && \
git commit -m "test: cover user turn context integration"
```

---

### Task 9: Full Focused Verification And Compat Markers

**Files:** all changed files

- [ ] **Step 1: Check compat marker inventory (v3.3 修订)**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && rg -n "COMPAT:" src matmaster tests
```

Expected output includes:

```text
COMPAT:legacy-runtime-injection-helper
COMPAT:v0-checkpoint-marker
COMPAT:v0-restore
COMPAT:hybrid-restore
```

If `COMPAT:v0-restore` is missing, add a comment in `src/services/model_history_restore_service.py` at the legacy fallback branch:

```python
# COMPAT:v0-restore -- old sessions without user_turn_context or v1 checkpoint
# still restore through ChatHistoryConverter until Phase 4.
```

If `COMPAT:hybrid-restore` is missing, add a comment in `src/services/model_history_restore_service.py` at the hybrid `User/query` consumption branch:

```python
# COMPAT:hybrid-restore -- in hybrid mode (no v1 checkpoint but utc exists),
# consume pre-Phase-1 User/query whose invocation_id is not covered by user_turn_context.
# Retires together with COMPAT:v0-restore in Phase 4.
```

- [ ] **Step 2: Run focused Phase 1 suite**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/test_stream_replay_skill_hit.py \
  tests/matmaster/integration/test_sse_skill_hit.py \
  tests/matmaster/integration/test_sse_handler_mode_filter.py \
  tests/test_chat_events_history_checkpoint.py \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/services/test_history_checkpoint_codec.py \
  tests/matmaster/services/test_user_turn_context_service.py \
  tests/matmaster/services/test_model_history_restore_service.py \
  tests/matmaster/services/test_history_restore_service.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/matmaster/services/test_user_instructions_runtime_injection.py \
  tests/matmaster/integration/test_history_checkpoint_recovery.py \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_reply_events.py \
  -q
```

Expected: all pass.

- [ ] **Step 3: Run broader affected service tests**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run pytest \
  tests/matmaster/services \
  tests/matmaster/integration/test_events_to_messages.py \
  tests/matmaster/integration/test_tool_protocol_guardrails.py \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_planner.py \
  -q
```

Expected: all pass.

- [ ] **Step 4: Run lint-like import smoke**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && uv run python - <<'PY'
from src.services.user_turn_context_service import UserInstructionsInfo
from src.services.model_history_restore_service import ModelHistoryRestoreService
from src.services.history_restore_service import HistoryRestoreService
from src.services.agent_run_history_wiring import build_history_wiring

assert HistoryRestoreService is ModelHistoryRestoreService
assert UserInstructionsInfo(text="", hash="sha256:test").text == ""
assert callable(build_history_wiring)
print("phase1 imports ok")
PY
```

Expected:

```text
phase1 imports ok
```

- [ ] **Step 5: Final status check**

Run:

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo && git status --short
```

Expected: clean except the pre-existing `.planning/*.md` deletions if they were not part of this branch, and no uncommitted Phase 1 source/test files. This plan lives under ignored `.planning/`, so it may not appear in normal `git status`.

This Task has no commit unless Step 1 required adding missing `COMPAT:` comments.

---

## Self-Review Checklist

- [ ] DESIGN.md §3.1: `user_turn_context` event exists as DB event, not kernel bus event.
- [ ] DESIGN.md §3.2: payload has `schema_version`, `kind`, `message`, `user_instructions_hash`, `transform`, `render_version`.
- [ ] DESIGN.md §3.2: continuation 写入时 `user_instructions_hash` 字段为 `None` (不能误传值导致与 anchor hash 漂移)。
- [ ] DESIGN.md §3.4: service writes `user_turn_context` before `Exp.run_stream()` starts.
- [ ] DESIGN.md §3.5: replay and live SSE both hide `user_turn_context`.
- [ ] DESIGN.md §3.6 (v3.3): `user_turn_context` 写入 fail-fast 包括三种情况: `invocation_id` 缺失 / DAO 返回 false / dedup 查询异常; 三者都通过 try/except 包装成 `(False, msg)` 而不是裸抛。
- [ ] DESIGN.md §4.1 #1 (v3.3): 应用层 dedup 调用 `query_user_turn_context_by_invocation`, 命中时比较 payload；一致才幂等 skip 且 LLM 仍以 rendered task 跑，不一致 fail-fast。
- [ ] DESIGN.md §11.1 (v3.3): hybrid 判定调用 `has_user_turn_context` EXISTS 查询，不用 `get_session_events(limit=N)` 的 earliest-N 窗口探测。
- [ ] DESIGN.md §7bis.4 (v3.3): `UserInstructionsInfo.text` 是 raw text (无 `.strip()`); hash 基于 raw text。
- [ ] DESIGN.md §7bis.5 (v3.3): `latest_anchor_user_instructions_hash` 遇 `history_checkpoint` 时无论 hash 是否存在都 return (无条件 barrier)。
- [ ] DESIGN.md §8.3: latest anchor hash scans recent `user_turn_context` anchor and `history_checkpoint` events.
- [ ] DESIGN.md §8.6: hash format is `sha256:<hex>`, including empty AGENT.md.
- [ ] DESIGN.md §11.2.1 (v3.3): hybrid v1 restore 保留 Phase 1 部署前的 raw User/query (按 invocation_id 判定是否被 utc 覆盖)。
- [ ] DESIGN.md §11: restore path has `COMPAT:v0-restore` and v1 consumption of `user_turn_context`, `assistant_state`, `response`/`run_result`/`finish`, `tool_result`; hybrid 分支额外消费未被覆盖的 `User/query` 标记 `COMPAT:hybrid-restore`。
- [ ] DESIGN.md §11.5: checkpoint codec accepts v0 and v1 markers with `COMPAT:v0-checkpoint-marker`.
- [ ] DESIGN.md §14 Phase 1: no `matmaster/context/` kernel introduced; no Case 3; no prompt-shape A/B; no compactor migration.
- [ ] No payload field named `source_query_event_id` was added.
- [ ] No `User/query` content includes provider-facing instructions.
- [ ] `rg "_apply_user_instructions_to_initial_user_query\\(" src/services/agent_run_service.py` shows no runtime caller after imports.
- [ ] `run_meta` 不新增 `user_instructions_hash` / `user_instructions_truncated` 字段 (DESIGN.md §17.9 god bag 不扩张原则)。
- [ ] `user_turn_context.message.content` 在有附件场景下 = `build_user_request(rendered_runtime_task, attachment_text)`, 与 kernel 实际发给 LLM 的 user content 一致 (Task 7 Step 1 第二个测试覆盖)。
- [ ] `rg "COMPAT:hybrid-restore" src/services` 至少命中一处 (在 `model_history_restore_service.py` 的 hybrid 分支)。

## Execution Handoff

Plan complete and saved to `.planning/context-refactor/PHASE-1-PLAN.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh worker per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batching with checkpoints.
