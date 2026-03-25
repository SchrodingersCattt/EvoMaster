# Subagent Event spawn_id Tagging

## Problem

Subagent events are persisted and streamed alongside parent agent events on a shared MessageBus, but lack structural boundaries. This causes:

1. **History reconstruction corruption**: `ChatHistoryConverter.events_to_dialog_messages()` processes subagent internal tool_call/tool_result events as parent-level dialog, breaking tool_call-to-tool_result pairing logic.
2. **No per-invocation traceability**: Multiple spawn invocations cannot be distinguished -- all subagent events of the same type share the same `source` prefix (`MatMaster:explore`).

## Decision

Add a `spawn_id` field to the event model, tagged at emission time and written to DB. Parent events have `spawn_id=NULL`, subagent events carry a unique 16-char hex identifier per spawn invocation.

- Persistence: all events written (parent + subagent), differentiated by `spawn_id`
- SSE: all events pushed (parent + subagent), `spawn_id` included in payload for frontend grouping
- History reconstruction: `get_session_events()` defaults to `WHERE spawn_id IS NULL`, excluding subagent internals from LLM dialog context
- Subagent `final_content` remains the parent's spawn tool_result -- sufficient as folded summary

## Design

### Data Model

`BusEvent` base class (`matmaster/types/events.py`):

```python
spawn_id: str | None = None  # NULL = parent event, non-NULL = child event
```

All 18 event subclasses inherit automatically.

DB `evo_chat_events` table:

```sql
ALTER TABLE evo_chat_events ADD COLUMN spawn_id VARCHAR(64) NULL;
CREATE INDEX idx_chat_events_spawn_id ON evo_chat_events (session_id, spawn_id);
```

### Event Emission

`EventEmitterHook` (`matmaster/core/hooks.py`) accepts `spawn_id` in constructor, stamps it on every event created.

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

### Consumption

**PersistenceHandler** (`matmaster/integration/persistence_handler.py`): reads `event.spawn_id`, passes to `add_event()`. No filtering -- all events persisted.

**SSEHandler** (`matmaster/integration/sse_handler.py`): no filtering. Adds `spawn_id` to SSE payload alongside existing `source`, `session_id`, `task_id`:

```python
payload["spawn_id"] = getattr(event, "spawn_id", None)
```

**ChatHistoryConverter** (`src/services/chat_history.py`): no changes needed. `get_session_events()` default filter ensures subagent events never reach the converter.

**ChatEventsTable** (`src/dao/chat_events_table.py`):
- `add_event()`: accepts and writes `spawn_id`
- `get_session_events()`: adds `WHERE spawn_id IS NULL` by default; accepts `include_spawn=True` for audit/debug queries

### Edge Cases

1. **Nested spawn**: Currently blocked by `spawn_fn=None` recursion guard. If enabled in future, each layer generates its own `spawn_id`. Hierarchy derivable from `source` prefix chain (`MatMaster:explore:sub`).
2. **Child exception**: `finally: child_runtime.cleanup()` covers cleanup. Partial events already in DB with `spawn_id` remain queryable, do not affect parent history.
3. **Cancellation**: `stop_event` propagation to child is unchanged. Interrupted child events persist with `spawn_id`.

## Changes

| File | Change |
|------|--------|
| `matmaster/types/events.py` | `BusEvent` add `spawn_id` field |
| `matmaster/core/hooks.py` | `EventEmitterHook` accept and stamp `spawn_id` |
| `matmaster/core/exp.py` | `build_runtime` pass `spawn_id`; `_make_spawn_fn` generate spawn_id |
| `matmaster/integration/persistence_handler.py` | Read `event.spawn_id`, pass to DB |
| `matmaster/integration/sse_handler.py` | Add `spawn_id` to SSE payload |
| `src/dao/chat_events_table.py` | `add_event` write spawn_id; `get_session_events` default filter |
| DB migration | Add column + index |
