# Fix SkillHit Tracking

## Problem

`SkillHitHook` is dead code. It checks `tool_call.name.startswith("skill:")` but the actual tool name registered via `EvoToolAdapter` is `"use_skill"` (from `SkillTool.name` class variable). `SkillHitEvent` is never emitted.

The goal is to enable `SkillHitHook` as a **session-level skill usage tracker** — recording which skills were called and how many times per session — persisted to MySQL via the existing `PersistenceHandler`, **not** pushed to the frontend via SSE (live or replay).

## Design

### Change 1: Fix SkillHitHook matching logic

**File:** `matmaster/hooks/skill_hit.py`

Replace prefix-based detection with exact name match + argument extraction. Update module and class docstrings to reflect the new matching logic.

```python
# Before
_SKILL_PREFIX = "skill:"

class SkillHitHook(BaseHook):
    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        if not tool_call.name.startswith(_SKILL_PREFIX):
            return
        skill_name = tool_call.name[len(_SKILL_PREFIX):]
        self._bus.emit(SkillHitEvent(source=self._source, skill_name=skill_name))

# After
_SKILL_TOOL_NAME = "use_skill"

class SkillHitHook(BaseHook):
    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        if tool_call.name != _SKILL_TOOL_NAME:
            return
        raw = tool_call.arguments.get("skill_name")
        if not isinstance(raw, str) or not raw:
            return
        self._bus.emit(SkillHitEvent(source=self._source, skill_name=raw))
```

**Rationale:** `tool_call.arguments` is `dict[str, Any]` — the LLM could theoretically pass a non-string value. Explicit `isinstance(raw, str)` check prevents Pydantic coercion failures from propagating up through `run_post_tool_call()` (which does not isolate per-hook exceptions) and crashing the agent run.

**Scope note:** This hook records all `use_skill` invocations regardless of whether the skill exists in the registry. Failed lookups (typos) are intentionally counted — the `tool_result` event persisted alongside contains the error message, so analytics can distinguish successful vs failed hits via a join. Filtering at the hook level would require injecting the SkillRegistry, adding unnecessary coupling for an analytics-only concern.

### Change 2: Filter skill_hit from SSE (live + replay)

**File:** `matmaster/integration/sse_handler.py` — live SSE path

Add `"skill_hit"` to `_should_skip`:

```python
def _should_skip(self, event: BusEvent) -> bool:
    event_type = getattr(event, "type", "")

    if event_type == "assistant_state":
        return True

    # skill_hit is persist-only, not pushed to frontend
    if event_type == "skill_hit":
        return True

    # ... rest unchanged
```

**File:** `src/services/stream_service.py` — history replay path

Add `"skill_hit"` to `_should_emit_event_to_sse`:

```python
def _should_emit_event_to_sse(event: dict) -> bool:
    t = event.get('type')
    if t == 'log_line':
        return False
    if t == 'assistant_state':
        return False
    if t == 'skill_hit':
        return False
    return True
```

**Rationale:** `use_skill` already generates `tool_call` + `tool_result` SSE events. Pushing `skill_hit` would be redundant. The live path (`SSEHandler`) and replay path (`_should_emit_event_to_sse`) must both filter it, otherwise `skill_hit` would leak to the frontend on history replay even if suppressed during live streaming.

### Change 3: Update tests

**File:** `tests/matmaster/hooks/test_skill_hit.py` — hook unit tests

| Test | tool_call.name | arguments | Expected |
|------|---------------|-----------|----------|
| Emits for use_skill with valid skill_name | `"use_skill"` | `{"skill_name": "bohrium-job", "action": "get_info"}` | `SkillHitEvent(skill_name="bohrium-job")` |
| Silent for non-skill tool | `"bash"` | `{}` | No emit |
| Silent for use_skill without skill_name | `"use_skill"` | `{"action": "get_info"}` | No emit |
| Silent for use_skill with non-string skill_name | `"use_skill"` | `{"skill_name": 123}` | No emit |

**File:** `tests/matmaster/integration/test_sse_handler.py` (new or extend) — SSE suppression

| Test | Description |
|------|-------------|
| SSEHandler skips skill_hit | Verify `_should_skip` returns True for `SkillHitEvent` |

**File:** `tests/` (new or extend) — replay suppression

| Test | Description |
|------|-------------|
| Replay filter skips skill_hit | Verify `_should_emit_event_to_sse({"type": "skill_hit", ...})` returns False |

## What stays unchanged

- `SkillHitEvent` type definition (`matmaster/types/events.py`)
- `PersistenceHandler` already persists `skill_hit` events (not in its skip list)
- `event_payloads.py` content mapping for `skill_hit` (returns `{"skill_name": ...}`)
- `EventLogger` (devshell JSONL) does not handle `skill_hit` — intentional, devshell does not need usage statistics
- `SkillTool` and `EvoToolAdapter` in evomaster layer — no changes needed

## Event flow after fix

```
AgentKernel executes use_skill tool
  -> EventEmitterHook.pre_tool_call()  -> ToolCallEvent   -> SSE (pushed) + MySQL (persisted)
  -> SkillTool.execute()               -> (tool runs)
  -> EventEmitterHook.post_tool_call() -> ToolResultEvent  -> SSE (pushed) + MySQL (persisted)
  -> SkillHitHook.post_tool_call()     -> SkillHitEvent    -> SSE (SKIPPED) + Replay (SKIPPED) + MySQL (persisted)
```

Frontend receives 2 SSE messages per skill invocation (tool_call + tool_result), both live and on replay.
MySQL stores 3 records per skill invocation (tool_call + tool_result + skill_hit).

## Usage query example

```sql
SELECT content->>'skill_name' AS skill, COUNT(*) AS hits
FROM evo_chat_events
WHERE session_id = ? AND type = 'skill_hit'
GROUP BY content->>'skill_name';
```
