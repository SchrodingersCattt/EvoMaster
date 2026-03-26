# Subagent Event spawn_id Tagging

## Problem

Subagent events are persisted and streamed alongside parent agent events on a shared MessageBus, but lack structural boundaries. This causes:

1. **History reconstruction corruption**: `ChatHistoryConverter.events_to_dialog_messages()` processes subagent internal tool_call/tool_result events as parent-level dialog, breaking tool_call-to-tool_result pairing logic.
2. **No per-invocation traceability**: Multiple spawn invocations cannot be distinguished -- all subagent events of the same type share the same `source` prefix (`MatMaster:explore`).

## Decision

Add a `spawn_id` field to the event model, tagged at emission time and written to DB. Parent events have `spawn_id=NULL`, subagent events carry a unique 16-char hex identifier per spawn invocation.

- Persistence: all events written (parent + subagent), differentiated by `spawn_id`
- SSE: all events pushed (parent + subagent), `spawn_id` included in payload for frontend grouping
- History reconstruction: for rows written by the new code path after rollout, `get_session_events()` defaults to `WHERE spawn_id IS NULL`, excluding subagent internals from LLM dialog context
- Frontend history replay: `stream_service.py` passes `include_spawn=True` so subagent events are replayed on reconnect
- Subagent `final_content` remains the parent's spawn tool_result -- sufficient as folded summary
- Rollout scope: **forward-only**. Existing rows written before deployment (and any rows written by old workers during a rolling deploy) keep `spawn_id=NULL`; they are not backfilled and may still pollute legacy history reconstruction.

## Design

### Data Model

Currently, all 18 event classes in `matmaster/types/events.py` independently inherit from `BaseModel`. `BusEvent` is a `Union` type alias, not a class. To avoid adding `spawn_id` to each of the 18 classes individually, introduce a common base class:

```python
class EventBase(BaseModel):
    """Common fields shared by all bus event types."""
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)
    spawn_id: str | None = None  # NULL = parent event, non-NULL = child event
```

Each event class changes from `class XxxEvent(BaseModel)` to `class XxxEvent(EventBase)`, removing their duplicated `source` and `timestamp` fields. The per-class `type: Literal[...]` discriminator field remains on each subclass. The `BusEvent`, `AgentEvent`, `SystemEvent` Union aliases are unchanged.

DB `evo_chat_events` table:

```sql
ALTER TABLE evo_chat_events ADD COLUMN spawn_id VARCHAR(64) NULL;
CREATE INDEX idx_session_spawn_id ON evo_chat_events (session_id, spawn_id);
```

Rollback: `ALTER TABLE evo_chat_events DROP COLUMN spawn_id;`

Bootstrap SQL in `src/sql/create_chat_tables.sql` must also be updated so fresh environments create the column from day 1. Incremental migration lives in `src/sql/migrate_add_spawn_id.sql`, mirroring the existing `migrate_add_invocation_id.sql` pattern but intentionally omitting `AFTER invocation_id` so it can run safely on older schemas as well.

Deployment order: update bootstrap SQL, run migration first (add column), then deploy new code. Between migration and deploy, old code writes NULL spawn_id (column default), which is acceptable under the forward-only rollout scope.

### Event Emission

`EventEmitterHook` (`matmaster/core/hooks.py`) accepts `spawn_id: str | None = None` in constructor, stores as `self._spawn_id`. All 6 event construction points (`pre_tool_call`, `post_tool_call`, `on_stream_chunk` thought/response branches, `on_segment_complete` thought/response branches) pass `spawn_id=self._spawn_id` to the event constructor.

`Exp.build_runtime()` (`matmaster/core/exp.py`) accepts `spawn_id` parameter, passes to `EventEmitterHook`:

```python
emitter_hook = EventEmitterHook(bus, source=emitter_source, spawn_id=spawn_id)
```

`Exp._make_spawn_fn()` generates spawn_id per invocation:

```python
import uuid

def spawn_fn(exp_name, task, stop_event=None):
    child_source = f"{source_prefix}:{exp_name}"
    child_spawn_id = uuid.uuid4().hex[:16]
    child_runtime = child_exp.build_runtime(
        ctx, bus=bus, source_override=child_source, spawn_id=child_spawn_id
    )
    ...
```

Parent `build_runtime()` does not pass `spawn_id` -- defaults to `None`.

16-char hex (64-bit random) is chosen for log readability and index efficiency. Collision within a single session is negligible given the current recursion depth limit of 1. DB column is `VARCHAR(64)` to accommodate future expansion.

### Consumption

**PersistenceHandler** (`matmaster/integration/persistence_handler.py`): reads `event.spawn_id` (guaranteed by `EventBase`), passes to `events_table.add_event()` as keyword argument. No filtering -- all events persisted.

**ChatEventsTable** (`src/dao/chat_events_table.py`):
- `add_event()`: accepts keyword-only `task_id`, `invocation_id`, and `spawn_id: str | None = None` parameters, writes `spawn_id` to DB column. This avoids positional argument drift at existing call sites.
- `get_session_events()`: adds `WHERE spawn_id IS NULL` by default; accepts `include_spawn: bool = False` parameter -- when `True`, omits the spawn_id filter

**ChatEventsService** (`src/services/events_service.py`):
- `get_session_events()`: passes through `include_spawn` parameter to `self.table.get_session_events()`
- `add_history_event()`: no changes needed -- only writes User events which have no spawn_id

**SSEHandler** (`matmaster/integration/sse_handler.py`): no filtering. Adds `spawn_id` to live SSE payload alongside existing `source`, `session_id`, `task_id`. Since `EventBase` includes `spawn_id`, `event.model_dump()` naturally includes it in the payload dict.

**ChatHistoryConverter** (`src/services/chat_history.py`): parsing logic is unchanged, but add a small helper to exclude events with non-NULL `spawn_id`. `agent_run_service.py` uses this helper defensively before converting DB rows into LLM dialog history. This only protects newly written rows with populated `spawn_id`; legacy `NULL` child rows remain out of scope for this rollout.

**stream_service.py** (`src/services/stream_service.py`): both history replay paths must pass `include_spawn=True` to `events_service.get_session_events()`, so subagent events are replayed to the frontend on reconnect -- consistent with live SSE behavior for this field. Replay dedupe must key by `(task_id, spawn_id)` rather than `task_id` alone; otherwise a child `response` can incorrectly suppress the parent's `run_result`.

**agent_run_service.py** (`src/services/agent_run_service.py`): still loads parent-only rows by default for LLM dialog history construction, and additionally applies the defensive `exclude_spawn_events()` helper before dialog conversion.

**Worker mode**: SSEHandler's `send_cb` in worker mode is `redis_dao.publish_stream_event()`. Since `spawn_id` is already in the payload dict, it passes through Redis to the API pod transparently. No additional changes needed.

### Edge Cases

1. **Nested spawn**: Currently blocked by `spawn_fn=None` recursion guard. If enabled in future, each layer generates its own `spawn_id`. Hierarchy derivable from `source` prefix chain (`MatMaster:explore:sub`).
2. **Child exception**: `finally: child_runtime.cleanup()` covers cleanup. Partial events already in DB with `spawn_id` remain queryable, do not affect parent history.
3. **Cancellation**: `stop_event` propagation to child is unchanged. Interrupted child events persist with `spawn_id`.

## Changes

| File | Change |
|------|--------|
| `matmaster/types/events.py` | Introduce `EventBase` base class with `spawn_id`; 18 event classes inherit from `EventBase` instead of `BaseModel` |
| `matmaster/core/hooks.py` | `EventEmitterHook` accept `spawn_id` in constructor; stamp on all 6 event construction points |
| `matmaster/core/exp.py` | `build_runtime` accept and pass `spawn_id`; `_make_spawn_fn` generate spawn_id per invocation |
| `matmaster/integration/persistence_handler.py` | Read `event.spawn_id`, pass to `add_event()` |
| `matmaster/integration/sse_handler.py` | Live SSE payload keeps `spawn_id`; replay reads it back from DB rows |
| `src/dao/chat_events_table.py` | `add_event` accept keyword-only `spawn_id` and write it; `get_session_events` accept `include_spawn` param, default filter `WHERE spawn_id IS NULL` |
| `src/services/events_service.py` | `get_session_events` pass through `include_spawn` param; existing `add_event()` calls use explicit keyword arguments |
| `src/services/chat_history.py` | Add `exclude_spawn_events()` helper for defensive parent-only dialog reconstruction |
| `src/services/stream_service.py` | Both replay paths call `get_session_events` with `include_spawn=True`; replay dedupe keys by `(task_id, spawn_id)` |
| DB migration | Add `spawn_id` column + index |
