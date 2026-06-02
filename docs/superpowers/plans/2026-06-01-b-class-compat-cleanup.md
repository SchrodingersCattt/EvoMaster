# B Class Compatibility Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 `refactor/context` 读侧残留的 B 类内联兼容逻辑，让主代码只接受当前契约，不再自动读取旧键、旧事件类型、旧 history restore 路径或旧 LLM config schema。

**Architecture:** 按服务层 `src/` 与核心层 `matmaster/` 对称清理：先删低风险 config/docstring 与 queue wire 旧键，再收紧事件类型，最后改 history restore 控制流。旧会话数据直接丢弃；malformed v1 checkpoint 继续降级尝试更老有效 checkpoint；`covered_until_event_id is None` 作为损坏 checkpoint 抛专用异常并向上暴露。

**Tech Stack:** Python 3.11+ via `uv run`, Pydantic v2, pytest, Redis worker queue, MatMaster event history services.

---

## Preconditions

- 工作区可能已有用户改动。执行前先运行 `git status --short`，只编辑本计划列出的文件，不回退用户已有改动。
- spec 要求单一提交。每个 Task 跑定向测试，但只在 Task 6 统一 `git commit`。
- `#4 event_payloads` 前端兼容不清理。`matmaster/integration/event_payloads.py` 只补一段注释，说明 `finish` 是前端迁移前的暂留识别分支，后端不再产生。
- 所有 Python 命令使用 `uv run`。

## File Structure

Modify `matmaster/context/sources/turn_input.py`

- `TurnInput.from_payload()` 只读 `pre_turn_history_event_id`。
- 删除 `pre_query_scope_event_id` fallback。

Modify `src/services/stream_service.py`

- Redis job 只发送 `turn_input`，删除 `legacy_current_input_payload` 与 `current_input_context`。
- Redis stream close 判断只接受 `stream_closed`。

Modify `src/worker/agent_worker.py`

- Worker 只从 `payload["turn_input"]` 构造 `TurnInput`。

Modify `matmaster/config/llm.py`

- 删除 `_normalize_legacy_or_explicit_schema()`。
- 删除 `resolve_profile()`。
- 保留 `profiles/routes/default` schema 与 `resolve_route()`。

Modify `matmaster/config/loader.py`

- module docstring 改成 MatMaster 独立 typed config loader 说明。

Modify `matmaster/types/events.py`

- `RunResultEvent.type` 收紧为 `Literal["run_result"]`。
- `StreamClosedEvent.type` 收紧为 `Literal["stream_closed"]`。

Modify `matmaster/context/history_restore.py`

- 删除 `LegacyRestore`、`legacy_restore` 构造参数和 `_legacy_restore` 字段。
- 新增 `HistoryCheckpointCorruptedError` 与 `HistoryRestoreFailedError`。
- 旧会话无 v1 checkpoint 且无 `user_turn_context` 时返回 `[]`。
- v1 checkpoint `covered_until_event_id is None` 时抛 `HistoryCheckpointCorruptedError`。
- hybrid 模式只恢复 `user_turn_context` 锚定的新 turn，旧 raw `User/query` 及其 tail 整段丢弃。

Modify `src/services/model_history_restore_service.py`

- 删除 `_restore_legacy_untrimmed()` 与 legacy callback。
- `HistoryCheckpointCorruptedError` 直接向上抛。
- malformed base messages 继续尝试更老 v1 checkpoint。
- v1 checkpoint 全部因可恢复错误耗尽后抛 `HistoryRestoreFailedError`。

Modify `src/services/chat_history.py`

- `events_to_dialog_messages()` 的 terminal fallback 只接受 `run_result`。

Modify `src/services/stream_sse_filter.py`

- replay terminal dedupe 只识别 `run_result`。

Modify tests:

- `tests/matmaster/context/sources/test_turn_input.py`
- `tests/test_chat_stream_direct.py`
- `tests/matmaster/worker/test_redis_bridge.py`
- `tests/matmaster/config/test_llm.py`
- `tests/matmaster/config/test_loader.py`
- `tests/matmaster/types/test_events.py`
- `tests/matmaster/integration/test_events_to_messages.py`
- `tests/matmaster/context/test_history_restore.py`
- `tests/matmaster/services/test_model_history_restore_service.py`
- `tests/matmaster/services/test_model_history_restore_service_tail.py`
- `tests/matmaster/integration/test_history_checkpoint_recovery.py`
- `tests/matmaster/integration/test_e2e_mat_master.py`

---

### Task 1: Remove TurnInput Legacy Wire Keys

**Files:**

- Modify: `tests/matmaster/context/sources/test_turn_input.py`
- Modify: `tests/test_chat_stream_direct.py`
- Modify: `tests/matmaster/worker/test_redis_bridge.py`
- Modify: `matmaster/context/sources/turn_input.py`
- Modify: `src/services/stream_service.py`
- Modify: `src/worker/agent_worker.py`

- [ ] **Step 1: Add failing assertions**

Append to `tests/matmaster/context/sources/test_turn_input.py`:

```python
def test_turn_input_ignores_removed_pre_query_scope_event_id() -> None:
    turn_input = TurnInput.from_payload(
        {"user_text": "hi", "pre_query_scope_event_id": 42}
    )

    assert turn_input is not None
    assert turn_input.pre_turn_history_event_id == 0
```

In `tests/test_chat_stream_direct.py`, inside `_lpush_agent_run_job()` in `test_generate_send_stream_subscribes_before_enqueue`, add:

```python
        assert 'turn_input' in _job
        assert 'current_input_context' not in _job
        assert 'pre_query_scope_event_id' not in json.dumps(_job, ensure_ascii=False)
```

In `tests/matmaster/worker/test_redis_bridge.py`, extend `test_run_worker_loop_passes_cancel_token_and_cleans_up_controller` payload with:

```python
            "current_input_context": {
                "user_text": "legacy only",
                "pre_query_scope_event_id": 99,
            },
```

and add:

```python
        assert observed["turn_input"] is None
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/context/sources/test_turn_input.py::test_turn_input_ignores_removed_pre_query_scope_event_id tests/test_chat_stream_direct.py::test_generate_send_stream_subscribes_before_enqueue tests/matmaster/worker/test_redis_bridge.py::TestAgentWorkerCancellationIntegration::test_run_worker_loop_passes_cancel_token_and_cleans_up_controller -q
```

Expected: FAIL because old payload compatibility still exists.

- [ ] **Step 3: Implement minimal cleanup**

In `matmaster/context/sources/turn_input.py`:

```python
        raw_boundary = payload.get("pre_turn_history_event_id", 0)
```

In `src/services/stream_service.py`, delete the `legacy_current_input_payload` block and remove the job key:

```python
                'current_input_context': legacy_current_input_payload,
```

In `src/worker/agent_worker.py`:

```python
        turn_input = TurnInput.from_payload(payload.get('turn_input'))
```

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/matmaster/context/sources/test_turn_input.py tests/test_chat_stream_direct.py::test_generate_send_stream_subscribes_before_enqueue tests/matmaster/worker/test_redis_bridge.py::TestAgentWorkerCancellationIntegration -q
```

Expected: PASS.

---

### Task 2: Remove LLM Config Legacy Schema And Loader Docstring

**Files:**

- Modify: `tests/matmaster/config/test_llm.py`
- Modify: `tests/matmaster/config/test_loader.py`
- Modify: `matmaster/config/llm.py`
- Modify: `matmaster/config/loader.py`

- [ ] **Step 1: Replace old tests**

In `tests/matmaster/config/test_llm.py`, replace `TestLLMConfigModelValidator.test_flat_yaml_dict`:

```python
    def test_flat_yaml_dict_is_rejected(self) -> None:
        raw = {
            "opus": {"provider": "openai", "model": "claude-opus-4-6"},
            "sonnet": {"provider": "openai", "model": "claude-sonnet-4-6"},
            "default": "opus",
        }

        with pytest.raises(ValueError, match="default profile 'opus' not found"):
            LLMConfig.model_validate(raw)
```

Delete `TestResolveProfile`.

In `tests/matmaster/config/test_loader.py`, convert every flat `llm` fixture to normalized form:

```yaml
llm:
  profiles:
    p1:
      model: "m1"
      api_key: "${TEST_API_KEY}"
  default: "p1"
```

For `_YAML_CONTENT`, put `opus` and `sonnet` under `llm.profiles`.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/config/test_llm.py tests/matmaster/config/test_loader.py -q
```

Expected: FAIL while flat schema is still normalized.

- [ ] **Step 3: Remove config compatibility**

In `matmaster/config/llm.py`, delete:

```python
    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_or_explicit_schema(cls, data: Any) -> Any:
```

Delete `LLMConfig.resolve_profile()`.

In `matmaster/config/loader.py`, replace the module docstring:

```python
"""Typed config accessors for matmaster.

These helpers load MatMaster YAML files or pre-loaded dictionaries and validate
them into typed config models.

Usage::

    llm = load_llm_config("config/llm_config.yaml")
    exp = load_exp_config("direct")
"""
```

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/matmaster/config/test_llm.py tests/matmaster/config/test_loader.py -q
```

Expected: PASS.

---

### Task 3: Remove Legacy `finish` / `end` Event Type Acceptance

**Files:**

- Modify: `tests/matmaster/types/test_events.py`
- Modify: `tests/matmaster/integration/test_events_to_messages.py`
- Modify: `matmaster/types/events.py`
- Modify: `src/services/stream_sse_filter.py`
- Modify: `src/services/chat_history.py`
- Modify: `src/services/stream_service.py`
- Modify: `matmaster/integration/event_payloads.py`

- [ ] **Step 1: Add failing tests**

In `tests/matmaster/types/test_events.py`, add:

```python
    def test_rejects_legacy_finish_type(self) -> None:
        with pytest.raises(ValidationError):
            RunResultEvent.model_validate({"type": "finish", "source": "agent"})

    def test_stream_closed_rejects_legacy_end_type(self) -> None:
        with pytest.raises(ValidationError):
            StreamClosedEvent.model_validate({"type": "end", "source": "system"})
```

In `tests/matmaster/integration/test_events_to_messages.py`, replace the legacy finish test:

```python
    def test_finish_events_no_longer_map_to_assistant_messages(self):
        events = [
            _user_event("legacy question"),
            {"source": "MatMaster", "type": "finish", "content": "legacy answer"},
        ]

        result = ChatHistoryConverter.events_to_messages(events)

        assert len(result) == 1
        assert isinstance(result[0], UserMessage)
        assert result[0].content == "legacy question"
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py::TestRunResultEvent::test_rejects_legacy_finish_type tests/matmaster/types/test_events.py::TestSystemEvents::test_stream_closed_rejects_legacy_end_type tests/matmaster/integration/test_events_to_messages.py::TestEventsToMessages::test_finish_events_no_longer_map_to_assistant_messages -q
```

Expected: FAIL because aliases are still accepted.

- [ ] **Step 3: Tighten models and active consumers**

In `matmaster/types/events.py`:

```python
class RunResultEvent(EventBase):
    """Business terminal event for a run outcome."""

    type: Literal["run_result"] = "run_result"
```

```python
class StreamClosedEvent(EventBase):
    """Transport-level marker indicating the live SSE stream can close."""

    type: Literal["stream_closed"] = "stream_closed"
```

In `src/services/stream_sse_filter.py`, replace terminal checks with:

```python
event_type == 'run_result'
```

In `src/services/chat_history.py`, use:

```python
            if _is_matmaster_source(source) and typ == 'run_result':
```

In `src/services/stream_service.py`, use:

```python
payload.get('type') == 'stream_closed'
```

In `matmaster/integration/event_payloads.py`, keep the set but add:

```python
# ``finish`` is retained only for the #4 frontend migration hold. Backend event
# models and active consumers no longer produce or accept it as a live terminal
# event type.
_TERMINAL_EVENT_TYPES = frozenset({'run_result', 'finish'})
```

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/matmaster/types/test_events.py tests/matmaster/integration/test_events_to_messages.py tests/test_chat_stream_direct.py::test_generate_send_stream_skips_current_task_in_history_replay tests/test_chat_stream_direct.py::test_generate_send_stream_replay_prefers_response_over_run_result tests/test_chat_stream_direct.py::test_generate_subscribe_stream_replay_prefers_response_over_run_result -q
```

Expected: PASS.

---

### Task 4: Rewrite Core History Restorer Without Legacy Restore

**Files:**

- Modify: `tests/matmaster/context/test_history_restore.py`
- Modify: `matmaster/context/history_restore.py`

- [ ] **Step 1: Update core tests**

In `tests/matmaster/context/test_history_restore.py`, import:

```python
import pytest
from matmaster.context.history_restore import (
    HistoryCheckpointCorruptedError,
    ModelHistoryRestorer,
)
```

In `_build()`, remove `legacy`, `calls["legacy"]`, `legacy_restore()`, and the constructor argument `legacy_restore=legacy_restore`.

Update helper terminal conversion:

```python
            elif etype in {"response", "run_result"}:
```

Replace old expectations:

```python
# pure v0 / no UTC:
assert result == []
assert calls["events_after"] == []
assert calls["has_utc"] == [("sess-1", None)]

# hybrid raw old turn before UTC:
assert [m.content for m in result] == ["rendered new turn", "new response"]

# null covered_until:
with pytest.raises(HistoryCheckpointCorruptedError, match="covered_until_event_id"):
    restorer.restore("sess-1")

# spawn id:
assert result == []
assert calls["has_utc"] == [("sess-1", "spawn-A")]
```

For the hybrid test fixture, include this event order:

```python
[
    {"id": 5, "type": "query", "source": "User", "content": {"content": "pre-Phase-1 turn"}, "invocation_id": "inv-old"},
    {"id": 6, "type": "response", "content": {"content": "old response"}, "invocation_id": "inv-old"},
    {"id": 7, "type": "user_turn_context", "invocation_id": "inv-new", "content": {"message": {"role": "user", "content": "rendered new turn"}}},
    {"id": 8, "type": "response", "content": {"content": "new response"}, "invocation_id": "inv-new"},
]
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/context/test_history_restore.py -q
```

Expected: FAIL because core still requires `legacy_restore` and preserves old hybrid raw turns.

- [ ] **Step 3: Implement core restorer contract**

In `matmaster/context/history_restore.py`, add:

```python
class HistoryCheckpointCorruptedError(RuntimeError):
    """A v1 history checkpoint exists but its boundary is structurally invalid."""


class HistoryRestoreFailedError(RuntimeError):
    """No usable v1 history checkpoint could be restored."""
```

Delete `LegacyRestore`, `legacy_restore`, and `_legacy_restore`.

Use this no-v1 branch:

```python
        if not schema_v1:
            if not self._has_user_turn_context(session_id, spawn_id):
                return []
            return self._restore_v1(
                session_id=session_id,
                spawn_id=spawn_id,
                checkpoint=None,
            )
```

Use this null boundary branch:

```python
        if covered is None:
            checkpoint_id = checkpoint.get("id")
            logger.warning(
                "history_checkpoint.v1 has null covered_until_event_id; "
                "aborting restore session_id=%s spawn_id=%s checkpoint_id=%s",
                session_id,
                spawn_id,
                checkpoint_id,
            )
            raise HistoryCheckpointCorruptedError(
                "history_checkpoint.v1 covered_until_event_id is null "
                f"session_id={session_id} spawn_id={spawn_id} checkpoint_id={checkpoint_id}"
            )
```

Replace hybrid tail scanning:

```python
        compatible_tail_events: list[dict[str, Any]] = []
        hybrid_turn_active = not hybrid_mode
        for event in events:
            if hybrid_mode:
                etype = str(event.get("type") or "").strip()
                source = str(event.get("source") or "").strip()
                if etype == "user_turn_context":
                    hybrid_turn_active = True
                elif source == "User" and etype == "query":
                    hybrid_turn_active = False
                    continue
                elif not hybrid_turn_active and etype in {
                    "assistant_state",
                    "response",
                    "run_result",
                    "tool_call",
                    "tool_result",
                }:
                    continue

            compatible = self._event_to_v1_compatible_event(event)
            if compatible is not None:
                compatible_tail_events.append(compatible)
```

Change `_event_to_v1_compatible_event()` to accept only `event`, return `None` for raw `User/query`, remove `finish`, and keep:

```python
        if etype == "tool_result":
            return self._normalize_tool_result_event(event)
        if etype in {"assistant_state", "response", "run_result", "tool_call"}:
            return event
```

- [ ] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/matmaster/context/test_history_restore.py -q
```

Expected: PASS.

---

### Task 5: Rewrite Service-Layer History Restore Control Flow

**Files:**

- Modify: `tests/matmaster/services/test_model_history_restore_service.py`
- Modify: `tests/matmaster/services/test_model_history_restore_service_tail.py`
- Modify: `tests/matmaster/integration/test_history_checkpoint_recovery.py`
- Modify: `tests/matmaster/integration/test_e2e_mat_master.py`
- Modify: `src/services/model_history_restore_service.py`

- [ ] **Step 1: Update service tests**

In `tests/matmaster/services/test_model_history_restore_service.py`, import:

```python
from matmaster.context.history_restore import (
    HistoryCheckpointCorruptedError,
    HistoryRestoreFailedError,
)
```

Change old expectations:

```python
# no checkpoint, no UTC:
assert history == []
assert ("has_user_turn_context", "sess-1", None) in events_table.calls
assert not any(call[0] == "get_scope_events_after_id" for call in events_table.calls)
assert not any(call[0] == "get_session_events" for call in events_table.calls)

# v1 checkpoint with null boundary:
with pytest.raises(HistoryCheckpointCorruptedError, match="covered_until_event_id"):
    ModelHistoryRestoreService(events_table).restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )
assert not any(call[0] == "get_session_events" for call in events_table.calls)

# hybrid old raw tail:
assert [message.content for message in history] == [
    "phase 1 question",
    "phase 1 answer",
]

# raw user without invocation:
assert [message.content for message in history] == ["new context"]

# delegate construction:
assert "legacy_restore" not in constructed[0]
```

Add all-v1-failed coverage:

```python
def test_all_recoverable_v1_checkpoints_failed_raises_restore_failed(monkeypatch) -> None:
    events_table = FakeEventsTable(
        checkpoints=[
            _v1_checkpoint(checkpoint_id=2, covered_until_event_id=20),
            _v1_checkpoint(checkpoint_id=1, covered_until_event_id=10),
        ],
        has_utc=True,
    )

    def fail_deserialize(_raw):
        raise ValueError("bad base messages")

    monkeypatch.setattr(restore_module, "deserialize_base_messages", fail_deserialize)

    with pytest.raises(HistoryRestoreFailedError, match="no usable"):
        ModelHistoryRestoreService(events_table).restore_history(
            session_id="sess-1",
            spawn_id=None,
            task_id=None,
        )
```

- [ ] **Step 2: Update tail and integration tests**

In `tests/matmaster/services/test_model_history_restore_service_tail.py`, import `ImageContentPart`, update `_utc_event()` to build `UserMessage(images=[ImageContentPart(url=url) for url in images or []])`, and make `has_user_turn_context()` return:

```python
return any(event.get("type") == "user_turn_context" for event in self.scope_events)
```

Then replace raw legacy assertions:

```python
# no checkpoint and no UTC:
assert history == []
assert not any(call[0] == "get_session_events" for call in events_table.calls)

# image trimming now uses scope_events with UTC messages:
events_table = FakeEventsTable(
    scope_events=[
        _utc_event("img 1", images=["https://oss.example.com/chat/1.png"]),
        _utc_event("text only"),
        _utc_event("img 2", images=["https://oss.example.com/chat/2.png"]),
        _utc_event("img 3", images=["https://oss.example.com/chat/3.png"]),
        _utc_event("img 4", images=["https://oss.example.com/chat/4.png"]),
    ]
)
assert [len(getattr(message, "images", [])) for message in history] == [0, 0, 1, 1, 1]
assert "[历史图片已裁剪: 1.png]" in history[0].content
```

In `tests/matmaster/integration/test_history_checkpoint_recovery.py`, rename the mixed hybrid test to `test_restore_v1_hybrid_mixed_session_discards_pre_phase1_raw_turn` and assert:

```python
assert [m.content for m in user_messages] == ["new rendered question with instructions"]
assert [m.content for m in assistant_messages] == ["new answer"]
```

In `tests/matmaster/integration/test_e2e_mat_master.py`, replace old raw `User/query + finish` fixture with `user_turn_context + run_result`, set:

```python
mock_events_table.has_user_turn_context.return_value = True
mock_events_table.get_scope_events_after_id.return_value = raw_events
```

and keep expected LLM contents:

```python
assert [m["content"] for m in llm_messages[1:]] == [
    "old question",
    "old answer",
    "<current_instruction>\nnew question\n</current_instruction>",
]
```

- [ ] **Step 3: Verify red**

Run:

```bash
uv run pytest tests/matmaster/services/test_model_history_restore_service.py tests/matmaster/services/test_model_history_restore_service_tail.py tests/matmaster/integration/test_history_checkpoint_recovery.py::test_restore_v1_hybrid_mixed_session_discards_pre_phase1_raw_turn tests/matmaster/integration/test_e2e_mat_master.py::TestMatMasterE2EPipeline::test_run_agent_excludes_current_task_query_from_history -q
```

Expected: FAIL because service still falls back to legacy and swallows null-boundary corruption.

- [ ] **Step 4: Rewrite service control flow**

In `src/services/model_history_restore_service.py`, import:

```python
from matmaster.context.history_restore import (
    HistoryCheckpointCorruptedError,
    HistoryRestoreFailedError,
    ModelHistoryRestorer,
)
```

Change `restore_history()` to this control flow:

```python
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
                    checkpoint=v1_checkpoint,
                )
                return trim_history_images(messages)
            except HistoryCheckpointCorruptedError:
                logger.warning(
                    "model_history_restore: v1 checkpoint has null boundary; aborting "
                    "session_id=%s spawn_id=%s checkpoint_id=%s",
                    session_id,
                    spawn_id,
                    v1_checkpoint.get("id"),
                    exc_info=True,
                )
                raise
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
            raise HistoryRestoreFailedError(
                "no usable history_checkpoint.v1 could be restored "
                f"session_id={session_id} spawn_id={spawn_id}"
            )

        messages = self._delegate_v1_restore(
            session_id=session_id,
            spawn_id=spawn_id,
            task_id=task_id,
            checkpoint=None,
        )
        return trim_history_images(messages)
```

Remove `raw_limit` from `_delegate_v1_restore()`, delete the nested `legacy_restore()`, construct `ModelHistoryRestorer` without `legacy_restore`, and delete `_restore_legacy_untrimmed()`.

- [ ] **Step 5: Verify green**

Run:

```bash
uv run pytest tests/matmaster/services/test_model_history_restore_service.py tests/matmaster/services/test_model_history_restore_service_tail.py tests/matmaster/integration/test_history_checkpoint_recovery.py::test_restore_v1_hybrid_mixed_session_discards_pre_phase1_raw_turn tests/matmaster/integration/test_e2e_mat_master.py::TestMatMasterE2EPipeline::test_run_agent_excludes_current_task_query_from_history -q
```

Expected: PASS.

---

### Task 6: Final Residual Scan, Full Tests, Single Commit

**Files:**

- Verify: `matmaster/`
- Verify: `src/`
- Verify: `tests/`

- [ ] **Step 1: Run targeted tests**

Run:

```bash
uv run pytest tests/matmaster/context/sources/test_turn_input.py tests/test_chat_stream_direct.py::test_generate_send_stream_subscribes_before_enqueue tests/matmaster/worker/test_redis_bridge.py::TestAgentWorkerCancellationIntegration tests/matmaster/config/test_llm.py tests/matmaster/config/test_loader.py tests/matmaster/types/test_events.py tests/matmaster/integration/test_events_to_messages.py tests/matmaster/context/test_history_restore.py tests/matmaster/services/test_model_history_restore_service.py tests/matmaster/services/test_model_history_restore_service_tail.py tests/matmaster/integration/test_history_checkpoint_recovery.py tests/matmaster/integration/test_e2e_mat_master.py::TestMatMasterE2EPipeline::test_run_agent_excludes_current_task_query_from_history -q
```

Expected: PASS.

- [ ] **Step 2: Run full tests**

Run:

```bash
uv run pytest
```

Expected: PASS.

- [ ] **Step 3: Run residual grep**

Run:

```bash
rg -n 'type: Literal\[[^]]*("finish"|"end")' matmaster/types/events.py
rg -n "payload\.get\('type'\) in \{[^}]*'end'" src/services/stream_service.py
rg -n "('run_result', 'finish'|\{'run_result', 'finish'\}|type['\"]?\s*:\s*['\"]finish['\"]|_public_content_for_event\(['\"]finish)" matmaster src tests --glob '!matmaster/integration/event_payloads.py' --glob '!tests/matmaster/integration/test_event_payloads.py'
rg -n "(payload\.get\('type'\) in \{[^}]*'end'|type['\"]?\s*:\s*['\"]end['\"]|event_type == ['\"]end['\"])" matmaster src tests
rg -n 'pre_query_scope_event_id|current_input_context|legacy_current_input_payload' matmaster src tests
rg -n '_legacy_restore|LegacyRestore|_restore_legacy_untrimmed' matmaster src tests
rg -n 'resolve_profile|_normalize_legacy_or_explicit_schema' matmaster src tests
```

Expected:

- first, second, fifth, sixth, seventh commands: no output.
- third command: no output outside the explicit #4 exclusions.
- fourth command: no event-type `end`; `stream_state == "end"` and provider `finish_reason` references are allowed.

- [ ] **Step 4: Check file length guard**

Run:

```bash
wc -l matmaster/context/history_restore.py src/services/model_history_restore_service.py src/worker/agent_worker.py src/services/stream_service.py src/services/chat_history.py matmaster/config/llm.py docs/superpowers/plans/2026-06-01-b-class-compat-cleanup.md
```

Expected: every listed file is under 1000 lines.

- [ ] **Step 5: Review diff and commit once**

Run:

```bash
git diff --check
git diff --stat
git add matmaster/context/sources/turn_input.py src/services/stream_service.py src/worker/agent_worker.py matmaster/config/llm.py matmaster/config/loader.py matmaster/types/events.py matmaster/context/history_restore.py src/services/model_history_restore_service.py src/services/chat_history.py src/services/stream_sse_filter.py matmaster/integration/event_payloads.py tests/matmaster/context/sources/test_turn_input.py tests/test_chat_stream_direct.py tests/matmaster/worker/test_redis_bridge.py tests/matmaster/config/test_llm.py tests/matmaster/config/test_loader.py tests/matmaster/types/test_events.py tests/matmaster/integration/test_events_to_messages.py tests/matmaster/context/test_history_restore.py tests/matmaster/services/test_model_history_restore_service.py tests/matmaster/services/test_model_history_restore_service_tail.py tests/matmaster/integration/test_history_checkpoint_recovery.py tests/matmaster/integration/test_e2e_mat_master.py
git commit -m "refactor: remove context compatibility fallbacks"
```

Expected: one commit containing the implementation. Do not stage unrelated spec edits or unrelated user test changes.

---

## Self-Review

Spec coverage:

- `#1 turn_input` covered by Task 1 source edits and Redis worker tests.
- `#2 history_restore legacy` covered by Task 4 and Task 5, including null boundary raise, malformed older-checkpoint fallback, no-checkpoint old-session empty history, and hybrid raw-tail discard.
- `#3 finish/end` covered by Task 3 event model, SSE filter, chat history, stream service, and event-to-message tests.
- `#4 event_payloads` intentionally excluded except the explanatory comment.
- `#5 config/llm` covered by Task 2, including flat schema rejection and `resolve_profile` deletion.
- `#6 loader docstring` covered by Task 2.
- Single-commit requirement covered by Task 6.

Plan hygiene:

- No placeholder steps remain.
- Commands use `uv run`.
- File length guard is part of final verification.
