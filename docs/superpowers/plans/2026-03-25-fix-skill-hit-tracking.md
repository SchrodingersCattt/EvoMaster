# Fix SkillHit Tracking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable SkillHitHook as a session-level skill usage tracker — persisted to MySQL, filtered from frontend SSE (live + replay).

**Architecture:** Fix matching logic in SkillHitHook (check `use_skill` name + extract `skill_name` from arguments), add SSE suppression in both live and replay paths.

**Tech Stack:** Python, Pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-03-25-fix-skill-hit-tracking-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `matmaster/hooks/skill_hit.py` | Fix matching logic: `use_skill` name + `isinstance` guard |
| Modify | `matmaster/integration/sse_handler.py:73-95` | Add `skill_hit` to live SSE skip list |
| Modify | `src/services/stream_service.py:51-73` | Add `skill_hit` to replay SSE filter |
| Modify | `tests/matmaster/hooks/test_skill_hit.py` | Rewrite all 3 tests + add non-string guard test |
| Create | `tests/matmaster/integration/test_sse_skill_hit.py` | SSE suppression test |
| Create | `tests/test_stream_replay_skill_hit.py` | Replay filter test |

---

## Chunk 1: Tests and Implementation

### Task 1: Rewrite SkillHitHook tests for new matching logic

**Files:**
- Modify: `tests/matmaster/hooks/test_skill_hit.py`

- [ ] **Step 1: Rewrite test file with 4 test cases**

```python
"""Tests for SkillHitHook."""

from __future__ import annotations

from unittest.mock import MagicMock

from matmaster.types.messages import ToolCallData


class TestSkillHitHook:
    """SkillHitHook post_tool_call behavior."""

    def test_emits_skill_hit_event_for_use_skill(self) -> None:
        """post_tool_call emits SkillHitEvent when tool is use_skill with valid skill_name."""
        from matmaster.hooks.skill_hit import SkillHitHook
        from matmaster.types.events import SkillHitEvent

        bus = MagicMock()
        hook = SkillHitHook(bus=bus, source="MatMaster")
        tc = ToolCallData(
            id="tc-1",
            name="use_skill",
            arguments={"skill_name": "bohrium-job", "action": "get_info"},
        )
        hook.post_tool_call(tc, "result")

        bus.emit.assert_called_once()
        emitted = bus.emit.call_args[0][0]
        assert isinstance(emitted, SkillHitEvent)
        assert emitted.skill_name == "bohrium-job"
        assert emitted.source == "MatMaster"

    def test_does_nothing_for_non_skill_tool(self) -> None:
        """post_tool_call does nothing for non use_skill tools."""
        from matmaster.hooks.skill_hit import SkillHitHook

        bus = MagicMock()
        hook = SkillHitHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="bash", arguments={})
        hook.post_tool_call(tc, "result")

        bus.emit.assert_not_called()

    def test_does_nothing_for_use_skill_without_skill_name(self) -> None:
        """post_tool_call does nothing when use_skill arguments lack skill_name."""
        from matmaster.hooks.skill_hit import SkillHitHook

        bus = MagicMock()
        hook = SkillHitHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="use_skill", arguments={"action": "get_info"})
        hook.post_tool_call(tc, "result")

        bus.emit.assert_not_called()

    def test_does_nothing_for_non_string_skill_name(self) -> None:
        """post_tool_call does nothing when skill_name is not a string."""
        from matmaster.hooks.skill_hit import SkillHitHook

        bus = MagicMock()
        hook = SkillHitHook(bus=bus)
        tc = ToolCallData(id="tc-1", name="use_skill", arguments={"skill_name": 123})
        hook.post_tool_call(tc, "result")

        bus.emit.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/hooks/test_skill_hit.py -v`
Expected: 3 PASS (non-skill tool, missing skill_name, non-string — old code returns early on name mismatch), 1 FAIL (use_skill emit test — old code checks `"skill:"` prefix, `"use_skill"` doesn't match)

---

### Task 2: Implement SkillHitHook fix

**Files:**
- Modify: `matmaster/hooks/skill_hit.py`

- [ ] **Step 3: Rewrite skill_hit.py**

```python
"""SkillHitHook -- emits SkillHitEvent when the use_skill tool is invoked.

Skills are identified by tool_call.name == "use_skill" and the
skill_name is extracted from tool_call.arguments.
"""

from __future__ import annotations

import logging

from matmaster.core.bus import MessageBus
from matmaster.core.hooks import BaseHook
from matmaster.types.messages import ToolCallData
from matmaster.types.events import SkillHitEvent

logger = logging.getLogger(__name__)

_SKILL_TOOL_NAME = "use_skill"


class SkillHitHook(BaseHook):
    """Hook that emits SkillHitEvent when the use_skill tool is called.

    Extracts skill_name from tool_call.arguments. Silently skips
    if skill_name is missing or not a string.
    """

    def __init__(self, bus: MessageBus, *, source: str = "MatMaster") -> None:
        self._bus = bus
        self._source = source

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        """Emit SkillHitEvent if tool is use_skill with a valid skill_name."""
        if tool_call.name != _SKILL_TOOL_NAME:
            return

        raw = tool_call.arguments.get("skill_name")
        if not isinstance(raw, str) or not raw:
            return

        self._bus.emit(
            SkillHitEvent(
                source=self._source,
                skill_name=raw,
            )
        )
```

- [ ] **Step 4: Run tests to verify all 4 pass**

Run: `uv run pytest tests/matmaster/hooks/test_skill_hit.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/hooks/skill_hit.py tests/matmaster/hooks/test_skill_hit.py
git commit -m "fix: enable SkillHitHook by matching use_skill tool name"
```

---

### Task 3: Filter skill_hit from live SSE

**Files:**
- Create: `tests/matmaster/integration/test_sse_skill_hit.py`
- Modify: `matmaster/integration/sse_handler.py:78-82`

- [ ] **Step 6: Write SSE suppression test**

```python
"""Test that SSEHandler skips skill_hit events."""

from __future__ import annotations

from matmaster.types.events import SkillHitEvent


class TestSSEHandlerSkillHit:
    """SSEHandler must not push skill_hit to frontend."""

    def test_should_skip_skill_hit(self) -> None:
        """_should_skip returns True for SkillHitEvent."""
        from matmaster.integration.sse_handler import SSEHandler

        handler = SSEHandler(
            send_cb=lambda x: None,
            loop=None,
            session_id="s-1",
            task_id="t-1",
            invocation_id=None,
            mode="direct",
        )
        event = SkillHitEvent(source="MatMaster", skill_name="bohrium-job")
        assert handler._should_skip(event) is True
```

- [ ] **Step 7: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/integration/test_sse_skill_hit.py -v`
Expected: FAIL — `_should_skip` returns False for `skill_hit`

- [ ] **Step 8: Add skill_hit to SSEHandler._should_skip**

In `matmaster/integration/sse_handler.py`, after line 82 (`return True` for `assistant_state`), add:

```python
        # skill_hit is persist-only, not pushed to frontend
        if event_type == "skill_hit":
            return True
```

- [ ] **Step 9: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/integration/test_sse_skill_hit.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add matmaster/integration/sse_handler.py tests/matmaster/integration/test_sse_skill_hit.py
git commit -m "fix: filter skill_hit from live SSE push"
```

---

### Task 4: Filter skill_hit from history replay

**Files:**
- Create: `tests/test_stream_replay_skill_hit.py`
- Modify: `src/services/stream_service.py:68-73`

- [ ] **Step 11: Write replay filter test**

```python
"""Test that _should_emit_event_to_sse filters skill_hit on replay."""

from __future__ import annotations


class TestReplayFilterSkillHit:
    """History replay must not emit skill_hit to SSE."""

    def test_should_not_emit_skill_hit(self) -> None:
        """_should_emit_event_to_sse returns False for skill_hit events."""
        from src.services.stream_service import _should_emit_event_to_sse

        event = {"type": "skill_hit", "source": "MatMaster", "content": {"skill_name": "bohrium-job"}}
        assert _should_emit_event_to_sse(event) is False

    def test_still_emits_tool_call(self) -> None:
        """Sanity: tool_call events are still emitted."""
        from src.services.stream_service import _should_emit_event_to_sse

        event = {"type": "tool_call", "source": "MatMaster"}
        assert _should_emit_event_to_sse(event) is True
```

- [ ] **Step 12: Run test to verify it fails**

Run: `uv run pytest tests/test_stream_replay_skill_hit.py -v`
Expected: `test_should_not_emit_skill_hit` FAIL (returns True), `test_still_emits_tool_call` PASS

- [ ] **Step 13: Add skill_hit to replay filter**

In `src/services/stream_service.py`, after line 72 (`return False` for `assistant_state`), add:

```python
    if t == 'skill_hit':
        return False
```

- [ ] **Step 14: Run test to verify it passes**

Run: `uv run pytest tests/test_stream_replay_skill_hit.py -v`
Expected: 2 PASS

- [ ] **Step 15: Commit**

```bash
git add src/services/stream_service.py tests/test_stream_replay_skill_hit.py
git commit -m "fix: filter skill_hit from history replay SSE"
```

---

### Task 5: Run full test suite

- [ ] **Step 16: Run all related tests**

Run: `uv run pytest tests/matmaster/hooks/test_skill_hit.py tests/matmaster/integration/test_sse_skill_hit.py tests/test_stream_replay_skill_hit.py -v`
Expected: 7 PASS (4 hook + 1 SSE + 2 replay)

- [ ] **Step 17: Verify no regressions in existing event tests**

Run: `uv run pytest tests/matmaster/types/test_events.py tests/matmaster/integration/test_event_router.py -v`
Expected: All existing tests PASS
