# Fix SkillHit Tracking

## Problem

`SkillHitHook` is dead code. It checks `tool_call.name.startswith("skill:")` but the actual tool name registered via `EvoToolAdapter` is `"use_skill"` (from `SkillTool.name` class variable). `SkillHitEvent` is never emitted.

The goal is to enable `SkillHitHook` as a **session-level skill usage tracker** — recording which skills were called and how many times per session — persisted to MySQL via the existing `PersistenceHandler`, **not** pushed to the frontend via SSE.

## Design

### Change 1: Fix SkillHitHook matching logic

**File:** `matmaster/hooks/skill_hit.py`

Replace prefix-based detection with exact name match + argument extraction:

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
        skill_name = tool_call.arguments.get("skill_name", "")
        if not skill_name:
            return
        self._bus.emit(SkillHitEvent(source=self._source, skill_name=skill_name))
```

**Rationale:** `tool_call.arguments` structure is defined by `SkillToolParams` (Pydantic model with `skill_name: str` as a required field), making it a stable contract.

### Change 2: Filter skill_hit from SSE

**File:** `matmaster/integration/sse_handler.py`

Add `"skill_hit"` to `_should_skip` so it is not pushed to the frontend:

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

**Rationale:** `use_skill` already generates `tool_call` + `tool_result` SSE events. Pushing `skill_hit` would be redundant. The event is only needed for persistence (usage tracking).

### Change 3: Update tests

**File:** `tests/matmaster/hooks/test_skill_hit.py`

Three test cases adapted to new matching logic:

| Test | tool_call.name | arguments | Expected |
|------|---------------|-----------|----------|
| Emits for use_skill with skill_name | `"use_skill"` | `{"skill_name": "bohrium-job", "action": "get_info"}` | `SkillHitEvent(skill_name="bohrium-job")` |
| Silent for non-skill tool | `"bash"` | `{}` | No emit |
| Silent for use_skill without skill_name | `"use_skill"` | `{"action": "get_info"}` | No emit |

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
  -> SkillHitHook.post_tool_call()     -> SkillHitEvent    -> SSE (SKIPPED) + MySQL (persisted)
```

Frontend receives 2 SSE messages per skill invocation (tool_call + tool_result).
MySQL stores 3 records per skill invocation (tool_call + tool_result + skill_hit).

## Usage query example

```sql
SELECT content->>'skill_name' AS skill, COUNT(*) AS hits
FROM evo_chat_events
WHERE session_id = ? AND type = 'skill_hit'
GROUP BY content->>'skill_name';
```
