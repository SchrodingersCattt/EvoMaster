# Preflight Current Input Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When preflight compaction triggers on a newly submitted turn, summarize only previous history and append the new turn as `<current_instruction>` without feeding its text or newly attached files into the summary LLM.

**Architecture:** Capture a runtime-only `CurrentInputContext` before the current User/query is persisted, snapshot the previous scope event id, pass that context through Worker and `AgentRunService` into kernel `spec.meta`, and use it only for preflight compaction. Runtime compaction keeps the existing path. Durable checkpoints get an optional `covered_until_event_id` override so preflight checkpoints cover events before the current query.

**Tech Stack:** Python 3.11+, uv, pytest, Pydantic message models, Redis worker jobs, PostgreSQL chat events, MatMaster `AgentKernel` / `ContextCompactor`.

---

## File Structure

- Create `matmaster/types/current_input.py`: `CurrentInputContext`, payload round-trip, tuple normalization, `<current_instruction>` formatting.
- Modify `matmaster/types/runtime_ports.py`: optional `covered_until_event_id` in `CompactionCheckpointPayload`.
- Modify `src/services/events_service.py`: wrapper for `get_latest_scope_event_id()`.
- Modify `src/services/stream_service.py`: capture `CurrentInputContext`, store it on `SendStreamContext`, enqueue it in Worker job payload.
- Modify `src/worker/agent_worker.py`: parse job `current_input_context`, pass it to `run_agent()`.
- Modify `src/services/agent_run_service.py`: accept `current_input_context`, inject it into `pg_ctx.run_meta`.
- Modify `matmaster/core/agent.py`: derive effective current context after prompt rewrite, skip first-turn split, pass context to preflight compactor, include checkpoint override in sink payload.
- Modify `matmaster/core/context_compactor.py`: accept optional current context, summarize history only, construct separate runtime/checkpoint messages.
- Modify `src/services/history_checkpoint_service.py`: prefer payload override, fallback to current latest-scope behavior.
- Tests: `tests/matmaster/types/test_current_input.py`, `tests/matmaster/types/test_runtime_ports.py`, `tests/test_chat_stream_direct.py`, `tests/test_chat_stream_reply_events.py`, `tests/matmaster/services/test_agent_run_stream.py`, `tests/matmaster/services/test_history_checkpoint_service.py`, `tests/matmaster/core/test_agent_kernel_compaction.py`, `tests/matmaster/core/test_context_compactor.py`, `tests/matmaster/manifests/test_rehydrator.py`.

---

### Task 1: Add Current Input Metadata Type

**Files:**
- Create: `matmaster/types/current_input.py`
- Modify: `matmaster/types/runtime_ports.py`
- Create: `tests/matmaster/types/test_current_input.py`
- Modify: `tests/matmaster/types/test_runtime_ports.py`

- [ ] **Step 1: Write failing tests**

Create `tests/matmaster/types/test_current_input.py` with these cases:

```python
from matmaster.types.current_input import (
    CurrentInputContext,
    build_current_instruction_block,
)


def test_current_input_context_round_trips_payload() -> None:
    ctx = CurrentInputContext.from_values(
        user_text="  analyze  ",
        files=["https://oss.example.com/chat/a.cif", ""],
        images=["https://oss.example.com/chat/fig.png"],
        workspace_paths=[" /share/case/POSCAR "],
        pre_query_scope_event_id=42,
    )

    assert ctx.user_text == "analyze"
    assert ctx.files == ("https://oss.example.com/chat/a.cif",)
    assert ctx.images == ("https://oss.example.com/chat/fig.png",)
    assert ctx.workspace_paths == ("/share/case/POSCAR",)
    assert CurrentInputContext.from_payload(ctx.to_payload()) == ctx
    assert ctx.has_effective_input() is True


def test_build_current_instruction_block_lists_only_current_inputs() -> None:
    ctx = CurrentInputContext.from_values(
        user_text="Use only the new file",
        files=["https://oss.example.com/chat/new.cif"],
        images=["https://oss.example.com/chat/current.png"],
        workspace_paths=["/share/current/POSCAR"],
        pre_query_scope_event_id=12,
    )

    block = build_current_instruction_block(ctx)

    assert block.startswith("<current_instruction>")
    assert "Use only the new file" in block
    assert "file_1 new.cif https://oss.example.com/chat/new.cif" in block
    assert "workspace_1 /share/current/POSCAR" in block
    assert "image_1 current.png https://oss.example.com/chat/current.png" in block
    assert "old.cif" not in block
    assert block.endswith("</current_instruction>")


def test_attachment_only_and_empty_current_instruction() -> None:
    with_file = CurrentInputContext.from_values(
        user_text="",
        files=["https://oss.example.com/chat/only-file.cif"],
    )
    empty = CurrentInputContext()

    assert "file_1 only-file.cif https://oss.example.com/chat/only-file.cif" in (
        build_current_instruction_block(with_file)
    )
    assert empty.has_effective_input() is False
    assert build_current_instruction_block(empty) == ""
```

Modify `tests/matmaster/types/test_runtime_ports.py::test_checkpoint_sink_protocol_signature()` so payload includes the optional override:

```python
payload={
    "durability": "durable",
    "strategy": "summary",
    "covered_until_event_id": 41,
}
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/types/test_current_input.py tests/matmaster/types/test_runtime_ports.py::test_checkpoint_sink_protocol_signature -q
```

- [ ] **Step 3: Implement type and formatter**

Create `matmaster/types/current_input.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse


def _clean_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        text
        for value in values
        if isinstance(value, str) and (text := value.strip())
    )


def _display_name(value: str) -> str:
    parsed = urlparse(value)
    return PurePosixPath(parsed.path or value).name or value


@dataclass(frozen=True)
class CurrentInputContext:
    user_text: str = ""
    files: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    workspace_paths: tuple[str, ...] = ()
    pre_query_scope_event_id: int | None = None

    @classmethod
    def from_values(
        cls,
        *,
        user_text: str | None = None,
        files: Any = None,
        images: Any = None,
        workspace_paths: Any = None,
        pre_query_scope_event_id: int | None = None,
    ) -> CurrentInputContext:
        return cls(
            user_text=(user_text or "").strip(),
            files=_clean_tuple(files),
            images=_clean_tuple(images),
            workspace_paths=_clean_tuple(workspace_paths),
            pre_query_scope_event_id=pre_query_scope_event_id,
        )

    @classmethod
    def from_payload(cls, payload: Any) -> CurrentInputContext | None:
        if not isinstance(payload, dict):
            return None
        raw = payload.get("pre_query_scope_event_id")
        try:
            boundary = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            boundary = None
        return cls.from_values(
            user_text=payload.get("user_text"),
            files=payload.get("files"),
            images=payload.get("images"),
            workspace_paths=payload.get("workspace_paths"),
            pre_query_scope_event_id=boundary,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_text": self.user_text,
            "files": list(self.files),
            "images": list(self.images),
            "workspace_paths": list(self.workspace_paths),
            "pre_query_scope_event_id": self.pre_query_scope_event_id,
        }

    def has_effective_input(self) -> bool:
        return bool(
            self.user_text.strip()
            or self.files
            or self.images
            or self.workspace_paths
        )


def build_current_instruction_block(context: CurrentInputContext) -> str:
    if not context.has_effective_input():
        return ""
    lines: list[str] = []
    if context.user_text.strip():
        lines.append(context.user_text.strip())
    attachment_lines = [
        *(f"file_{i} {_display_name(v)} {v}" for i, v in enumerate(context.files, 1)),
        *(f"workspace_{i} {v}" for i, v in enumerate(context.workspace_paths, 1)),
        *(f"image_{i} {_display_name(v)} {v}" for i, v in enumerate(context.images, 1)),
    ]
    if attachment_lines:
        if lines:
            lines.append("")
        lines.append("[Current attachments]")
        lines.extend(attachment_lines)
    return "<current_instruction>\n" + "\n".join(lines).strip() + "\n</current_instruction>"
```

Modify `matmaster/types/runtime_ports.py`:

```python
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable


class CompactionCheckpointPayload(TypedDict):
    durability: str
    strategy: str
    covered_until_event_id: NotRequired[int]
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/types/test_current_input.py tests/matmaster/types/test_runtime_ports.py::test_checkpoint_sink_protocol_signature -q
```

Commit:

```bash
git add matmaster/types/current_input.py matmaster/types/runtime_ports.py tests/matmaster/types/test_current_input.py tests/matmaster/types/test_runtime_ports.py
git commit -m "feat(compaction): add current input context type"
```

---

### Task 2: Capture Context At Send Boundary And Worker Queue

**Files:**
- Modify: `src/services/events_service.py`
- Modify: `src/services/stream_service.py`
- Modify: `src/worker/agent_worker.py`
- Modify: `tests/test_chat_stream_direct.py`
- Modify: `tests/test_chat_stream_reply_events.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_stream_direct.py`:

```python
def test_prepare_send_message_captures_current_input_context_before_user_event():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.get_session.return_value = {"session_directory": None}
    sessions_service.try_acquire_session_run.return_value = (True, None)
    events_service = MagicMock()
    events_service.get_latest_scope_event_id.return_value = 77
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        agent_run_service=MagicMock(),
        deploy_state_service=MagicMock(),
    )
    req = ChatSendRequest(
        content="analyze current",
        files=["https://oss.example.com/chat/new.cif"],
        images=["https://oss.example.com/chat/current.png"],
        workspace_paths=["/share/current/POSCAR"],
    )

    with (
        patch("src.services.stream_service.REDIS_URL", "redis://test"),
        patch("src.services.stream_service.get_redis_dao", return_value=MagicMock()),
    ):
        ctx = service.prepare_send_message("sess-1", req, user_id="user-1")

    assert ctx.current_input_context.user_text == "analyze current"
    assert ctx.current_input_context.files == ("https://oss.example.com/chat/new.cif",)
    assert ctx.current_input_context.images == ("https://oss.example.com/chat/current.png",)
    assert ctx.current_input_context.workspace_paths == ("/share/current/POSCAR",)
    assert ctx.current_input_context.pre_query_scope_event_id == 77
    events_service.get_latest_scope_event_id.assert_called_once_with("sess-1", None)
    events_service.add_history_event.assert_called_once()
```

Update `tests/test_chat_stream_reply_events.py::test_send_stream_context_does_not_carry_unused_reply_queue()`:

```python
assert "reply_queue" not in field_names
assert "current_input_context" in field_names
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/test_chat_stream_direct.py::test_prepare_send_message_captures_current_input_context_before_user_event tests/test_chat_stream_reply_events.py::test_send_stream_context_does_not_carry_unused_reply_queue -q
```

- [ ] **Step 3: Implement capture and queue serialization**

Add to `src/services/events_service.py`:

```python
    def get_latest_scope_event_id(
        self,
        session_id: str,
        spawn_id: str | None = None,
    ) -> int:
        if not self.table:
            return 0
        return self.table.get_latest_scope_event_id(session_id, spawn_id)
```

In `src/services/stream_service.py`, import `CurrentInputContext`, add
`current_input_context: CurrentInputContext | None = None` to `SendStreamContext`,
and add:

```python
    def _get_pre_query_scope_event_id(self, session_id: str) -> int | None:
        try:
            value = self._events_service.get_latest_scope_event_id(session_id, None)
        except Exception:
            logger.warning(
                "failed to snapshot pre-query scope event id session_id=%s",
                session_id,
                exc_info=True,
            )
            return None
        return value if isinstance(value, int) else None
```

Immediately before `add_history_event()` in `prepare_send_message()`:

```python
pre_query_scope_event_id = self._get_pre_query_scope_event_id(sid)
current_input_context = CurrentInputContext.from_values(
    user_text=user_content,
    files=req.files,
    images=req.images,
    workspace_paths=req.workspace_paths,
    pre_query_scope_event_id=pre_query_scope_event_id,
)
```

Pass `current_input_context=current_input_context` into `SendStreamContext(...)`.

In queued `job`, add:

```python
"current_input_context": (
    ctx.current_input_context.to_payload()
    if ctx.current_input_context is not None
    else None
),
```

In `src/worker/agent_worker.py`, parse and pass through:

```python
from matmaster.types.current_input import CurrentInputContext

current_input_context = CurrentInputContext.from_payload(
    payload.get("current_input_context")
)

current_input_context=current_input_context,
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/test_chat_stream_direct.py::test_prepare_send_message_captures_current_input_context_before_user_event tests/test_chat_stream_reply_events.py::test_send_stream_context_does_not_carry_unused_reply_queue -q
```

Commit:

```bash
git add src/services/events_service.py src/services/stream_service.py src/worker/agent_worker.py tests/test_chat_stream_direct.py tests/test_chat_stream_reply_events.py
git commit -m "feat(compaction): capture current input context at send boundary"
```

---

### Task 3: Inject Context Into Agent Run Metadata

**Files:**
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`

- [ ] **Step 1: Write failing metadata test**

Append to `tests/matmaster/services/test_agent_run_stream.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_injects_current_input_context_into_pg_ctx_run_meta():
    from matmaster.types.current_input import CurrentInputContext

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")
    current_input_context = CurrentInputContext.from_values(
        user_text="current prompt",
        files=["https://oss.example.com/chat/current.cif"],
        pre_query_scope_event_id=21,
    )

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="current prompt",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            current_input_context=current_input_context,
        )

    assert ok is True
    assert (
        svc._test_fake_exp.last_ctx.run_meta["current_input_context"]
        == current_input_context
    )
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream.py::test_run_agent_injects_current_input_context_into_pg_ctx_run_meta -q
```

- [ ] **Step 3: Implement run_meta injection**

In `src/services/agent_run_service.py`, import `CurrentInputContext`, add this parameter to `run_agent()`:

```python
current_input_context: CurrentInputContext | None = None,
```

Before `Exp` execution, add:

```python
if current_input_context is not None:
    pg_ctx = pg_ctx.model_copy(
        update={
            "run_meta": {
                **pg_ctx.run_meta,
                "current_input_context": current_input_context,
            }
        }
    )
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_stream.py::test_run_agent_injects_current_input_context_into_pg_ctx_run_meta -q
```

Commit:

```bash
git add src/services/agent_run_service.py tests/matmaster/services/test_agent_run_stream.py
git commit -m "feat(compaction): pass current input context into run metadata"
```

---

### Task 4: Add Checkpoint Boundary Override

**Files:**
- Modify: `matmaster/core/context_compactor.py`
- Modify: `matmaster/core/agent.py`
- Modify: `src/services/history_checkpoint_service.py`
- Modify: `tests/matmaster/core/test_agent_kernel_compaction.py`
- Modify: `tests/matmaster/services/test_history_checkpoint_service.py`

- [ ] **Step 1: Write failing override tests**

Add to `tests/matmaster/services/test_history_checkpoint_service.py`:

```python
async def test_checkpoint_sink_uses_payload_boundary_override(self) -> None:
    from src.services.history_checkpoint_service import HistoryCheckpointService

    events_table = Mock()
    events_table.get_latest_scope_event_id.return_value = 99
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

    covered = await sink(
        payload={
            "durability": "durable",
            "strategy": "summary",
            "covered_until_event_id": 41,
        },
        base_messages=base_messages,
    )

    assert covered == 41
    events_table.get_latest_scope_event_id.assert_not_called()
    events_table.add_history_checkpoint.assert_called_once()
    assert (
        events_table.add_history_checkpoint.call_args.kwargs["covered_until_event_id"]
        == 41
    )
```

Add to `tests/matmaster/core/test_agent_kernel_compaction.py` a fake compactor returning `checkpoint_covered_until_event_id=41`, then assert checkpoint sink receives:

```python
{
    "durability": "durable",
    "strategy": "summary",
    "covered_until_event_id": 41,
}
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/services/test_history_checkpoint_service.py::test_checkpoint_sink_uses_payload_boundary_override tests/matmaster/core/test_agent_kernel_compaction.py::test_kernel_passes_checkpoint_covered_until_override_to_sink -q
```

- [ ] **Step 3: Implement result field, kernel payload, sink override**

In `matmaster/core/context_compactor.py`:

```python
checkpoint_covered_until_event_id: int | None = None
```

as the final defaulted field of `CompactionResult`.

In `matmaster/core/agent.py` checkpoint call:

```python
payload = {"durability": result.durability, "strategy": result.strategy}
if result.checkpoint_covered_until_event_id is not None:
    payload["covered_until_event_id"] = result.checkpoint_covered_until_event_id
covered_until_event_id = await checkpoint_sink(
    payload=payload,
    base_messages=result.base_snapshot,
)
```

In `src/services/history_checkpoint_service.py`, after validation and flush:

```python
raw_covered_until = payload.get("covered_until_event_id")
if raw_covered_until is not None:
    covered_until_event_id = int(raw_covered_until)
else:
    covered_until_event_id = await asyncio.to_thread(
        self.events_table.get_latest_scope_event_id,
        session_id,
        spawn_id,
    )
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/services/test_history_checkpoint_service.py tests/matmaster/core/test_agent_kernel_compaction.py::test_kernel_passes_checkpoint_covered_until_override_to_sink -q
```

Commit:

```bash
git add matmaster/core/context_compactor.py matmaster/core/agent.py src/services/history_checkpoint_service.py tests/matmaster/core/test_agent_kernel_compaction.py tests/matmaster/services/test_history_checkpoint_service.py
git commit -m "feat(compaction): support checkpoint boundary override"
```

---

### Task 5: Implement Compactor Current Input Split

**Files:**
- Modify: `matmaster/core/context_compactor.py`
- Modify: `tests/matmaster/core/test_context_compactor.py`

- [ ] **Step 1: Write failing compactor tests**

Add `TestPreflightCurrentInputSplit` in `tests/matmaster/core/test_context_compactor.py` with these assertions:

```python
ctx = CurrentInputContext.from_values(
    user_text="Use only the new file",
    files=["https://oss.example.com/chat/new.cif"],
    images=["https://oss.example.com/chat/new.png"],
    workspace_paths=["/share/current/POSCAR"],
    pre_query_scope_event_id=42,
)
msgs = [
    SystemMessage(content="sys"),
    UserMessage(content="old question"),
    AssistantMessage(content="old answer"),
    UserMessage(
        content=(
            "Use only the new file\n\n"
            "<available_attachments>\n"
            "file_1 old.cif https://oss.example.com/chat/old.cif\n"
            "file_2 new.cif https://oss.example.com/chat/new.cif\n"
            "</available_attachments>"
        ),
        images=[ImageContentPart(url="https://oss.example.com/chat/new.png")],
    ),
]
result = await compactor.apply_compaction_plan(
    compactor.plan_preflight_compaction(msgs),
    msgs,
    current_input_context=ctx,
)
```

Assert all of the following in the test:

```python
prompt_text = provider.calls[0][1]["content"]
assert "old question" in prompt_text
assert "old answer" in prompt_text
assert "Use only the new file" not in prompt_text
assert "new.cif" not in prompt_text
assert "<current_instruction>" in (msgs[1].content or "")
assert "file_1 new.cif https://oss.example.com/chat/new.cif" in (msgs[1].content or "")
assert "old.cif" not in (msgs[1].content or "").split("<current_instruction>", 1)[1]
assert msgs[1].images[0].url == "https://oss.example.com/chat/new.png"
assert result.checkpoint_covered_until_event_id == 42
assert result.base_snapshot is not None
assert "<current_instruction>" not in result.base_snapshot[0]["content"]
assert result.base_snapshot[0].get("images") in (None, [])
```

Add two sibling tests:

```python
assert result.durability == "ephemeral"
assert result.failure_reason == "preflight_current_input_boundary_missing"
assert result.base_snapshot is None
```

for missing `pre_query_scope_event_id`, and:

```python
assert "file_1 only.cif https://oss.example.com/chat/only.cif" in (msgs[1].content or "")
```

for attachment-only current input.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/core/test_context_compactor.py::TestPreflightCurrentInputSplit -q
```

- [ ] **Step 3: Implement split**

In `matmaster/core/context_compactor.py`, import:

```python
from matmaster.types.current_input import (
    CurrentInputContext,
    build_current_instruction_block,
)
```

Add:

```python
CURRENT_INPUT_CONTINUATION_INSTRUCTION = (
    "不要向用户复述上述摘要，除非用户明确要求。"
    "当前用户指令位于下面的 <current_instruction> 块中；"
    "请基于摘要背景直接执行该指令。"
)
```

Extend signature:

```python
async def apply_compaction_plan(
    self,
    plan: CompactionPlan,
    messages: list[Message],
    *,
    current_input_context: CurrentInputContext | None = None,
) -> CompactionResult:
```

Use split only when `plan.phase == "preflight"`, context has input, last message is `UserMessage`, and `messages[1:-1]` is non-empty:

```python
current_split = (
    plan.phase == "preflight"
    and current_input_context is not None
    and current_input_context.has_effective_input()
    and len(messages) >= 3
    and isinstance(messages[-1], UserMessage)
    and bool(messages[1:-1])
)
summary_input = list(messages[1:-1]) if current_split else [
    message for message in messages if not isinstance(message, SystemMessage)
]
```

On summary success, branch:

```python
bundle = self._context_builder.build_compact_bundle(
    summary=summary,
    rehydrated_context=rehydrated,
    continuation_instruction=(
        CURRENT_INPUT_CONTINUATION_INSTRUCTION if current_split else None
    ),
)
if current_split and current_input_context is not None:
    current_user_message = messages[-1]
    instruction = build_current_instruction_block(current_input_context)
    runtime_user_msg = UserMessage(
        content=f"{bundle}\n\n{instruction}" if instruction else bundle,
        images=list(current_user_message.images),
    )
    checkpoint_user_msg = UserMessage(content=bundle)
    messages[:] = [system_msg, runtime_user_msg]
    if current_input_context.pre_query_scope_event_id is None:
        durability = "ephemeral"
        failure_reason = "preflight_current_input_boundary_missing"
    else:
        checkpoint_covered_until_event_id = (
            current_input_context.pre_query_scope_event_id
        )
else:
    checkpoint_user_msg = UserMessage(content=bundle)
    messages[:] = [system_msg, checkpoint_user_msg]
```

Build snapshot from `checkpoint_user_msg`, not from `messages[1]`.

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/core/test_context_compactor.py::TestPreflightCurrentInputSplit tests/matmaster/core/test_context_compactor.py::TestCompactorResultMetadata::test_preflight_summary_returns_durable_result -q
```

Commit:

```bash
git add matmaster/core/context_compactor.py tests/matmaster/core/test_context_compactor.py
git commit -m "feat(compaction): split current input during preflight summary"
```

---

### Task 6: Wire Current Context Through AgentKernel

**Files:**
- Modify: `matmaster/core/agent.py`
- Modify: `tests/matmaster/core/test_agent_kernel_compaction.py`
- Modify: `tests/matmaster/core/test_hook_wiring.py`

- [ ] **Step 1: Write failing kernel tests**

Add tests asserting:

```python
compactor.seen_current_input_context.user_text == "effective task text"
compactor.seen_current_input_context.files == ("https://oss.example.com/chat/current.cif",)
compactor.seen_current_input_context.pre_query_scope_event_id == 42
```

after running with:

```python
spec = _make_spec(provider=ContentOnlyProvider()).model_copy(
    update={
        "compactor": RecordingCompactor(),
        "meta": {
            "current_input_context": CurrentInputContext.from_values(
                user_text="original before rewrite",
                files=["https://oss.example.com/chat/current.cif"],
                pre_query_scope_event_id=42,
            )
        },
        "runtime_ports": KernelRuntimePorts(checkpoint_sink=lambda **kwargs: 42),
    }
)
```

Add a second test where `history=None` and current context exists; assert:

```python
assert compactor.preflight_calls == 0
```

- [ ] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_compaction.py::test_kernel_passes_effective_current_input_context_to_preflight_compactor tests/matmaster/core/test_agent_kernel_compaction.py::test_kernel_skips_preflight_current_split_when_history_is_empty -q
```

- [ ] **Step 3: Implement kernel context extraction**

In `matmaster/core/agent.py`, import:

```python
from dataclasses import replace
from matmaster.types.current_input import CurrentInputContext
```

After hook prompt rewrite:

```python
raw_current_input_context = spec.meta.get("current_input_context")
current_input_context = (
    raw_current_input_context
    if isinstance(raw_current_input_context, CurrentInputContext)
    else CurrentInputContext.from_payload(raw_current_input_context)
)
effective_current_input_context = (
    replace(current_input_context, user_text=task)
    if current_input_context is not None
    else None
)
```

If oversized offloader lands first, keep this block after offloader rewrites `task`.

- [ ] **Step 4: Pass context to preflight only and guard first turn**

Add `current_input_context` parameter to `_run_compaction_plan()` and apply call. In preflight branch:

```python
skip_preflight_for_empty_history = (
    effective_current_input_context is not None
    and effective_current_input_context.has_effective_input()
    and not history
)
plan = None if skip_preflight_for_empty_history else preflight_planner(state.messages)
```

Pass `current_input_context=effective_current_input_context` only for preflight `_run_compaction_plan()` calls. Runtime compaction calls omit it.

Update fake compactor signatures in `test_agent_kernel_compaction.py` and `test_hook_wiring.py`:

```python
async def apply_compaction_plan(self, plan, messages, *, current_input_context=None):
```

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/matmaster/core/test_agent_kernel_compaction.py tests/matmaster/core/test_hook_wiring.py -q
```

Commit:

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_compaction.py tests/matmaster/core/test_hook_wiring.py
git commit -m "feat(compaction): pass current input context through kernel"
```

---

### Task 7: Add Rehydrator Boundary Regression And Full Verification

**Files:**
- Modify: `tests/matmaster/manifests/test_rehydrator.py`

- [ ] **Step 1: Add boundary regression**

Append a regression to `tests/matmaster/manifests/test_rehydrator.py` using two
User/query events:

```python
events = [
    {"id": 10, "source": "User", "type": "query", "content": "old",
     "files": ["https://oss.example.com/chat/old.cif"]},
    {"id": 20, "source": "User", "type": "query", "content": "current",
     "files": ["https://oss.example.com/chat/current.cif"]},
]
rehydrator = CompactionRehydrator(
    get_query_events=lambda: events,
    get_all_events=lambda: events,
    get_latest_checkpoint_covered_until_event_id=lambda: 10,
    skill_registry=_registry(tmp_path),
    playground_ctx=PlaygroundContext(
        workdir=tmp_path,
        session_type="local",
        cache_area=tmp_path / "cache",
    ),
)
text = await rehydrator.build()
assert "old.cif" not in text
assert "file_2 current.cif https://oss.example.com/chat/current.cif" in text
```

- [ ] **Step 2: Run focused verification**

Run:

```bash
uv run pytest \
  tests/matmaster/types/test_current_input.py \
  tests/matmaster/types/test_runtime_ports.py::test_checkpoint_sink_protocol_signature \
  tests/test_chat_stream_direct.py::test_prepare_send_message_captures_current_input_context_before_user_event \
  tests/test_chat_stream_reply_events.py::test_send_stream_context_does_not_carry_unused_reply_queue \
  tests/matmaster/services/test_agent_run_stream.py::test_run_agent_injects_current_input_context_into_pg_ctx_run_meta \
  tests/matmaster/services/test_history_checkpoint_service.py \
  tests/matmaster/core/test_context_compactor.py::TestPreflightCurrentInputSplit \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/manifests/test_rehydrator.py \
  -q
```

- [ ] **Step 3: Run broader touched-area verification**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_context_compactor.py \
  tests/matmaster/core/test_agent_kernel_compaction.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/test_chat_stream_direct.py \
  tests/test_chat_stream_planner.py \
  tests/test_chat_stream_session_directory.py \
  tests/matmaster/manifests/test_rehydrator.py \
  -q
```

- [ ] **Step 4: Run static sanity checks**

Run:

```bash
uv run python -m compileall matmaster/types/current_input.py matmaster/core/context_compactor.py matmaster/core/agent.py src/services/stream_service.py src/services/agent_run_service.py src/worker/agent_worker.py
git diff --check
```

- [ ] **Step 5: Commit**

```bash
git add tests/matmaster/manifests/test_rehydrator.py
git commit -m "test(compaction): cover pre-query checkpoint rehydration boundary"
```

---

## Self-Review Checklist

- Coverage: Tasks 2, 4, 5, 6, and 7 cover send-boundary snapshot, checkpoint override, preflight split, first-turn guard, and rehydrator boundary.
- Types: `CurrentInputContext.images` stores raw URLs; `UserMessage.images` keeps existing `ImageContentPart` objects. New checkpoint fields are optional/defaulted.
