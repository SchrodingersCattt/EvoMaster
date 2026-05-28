# R7 SessionEvent Decoding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 SessionEvent row 反序列化路径，移除 `matmaster/context/` 中对 DAO row schema 的认知，并保证 prompt assembly、runtime compaction、active skill rehydration 三条路径得到完全一致的 typed `SessionEvent`。

**Architecture:** DAO row → `SessionEvent` 的唯一反序列化点放在 service 层 `src/services/session_event_codec.py`。`matmaster/context/` 只消费 typed `SessionEvent`，保留 scanner 的事件扫描职责，不再接收 raw dict。`_RunSessionEventHistory` 增加 typed `load_events()`，runtime compaction 直接使用同一个 typed port，不再通过 `RuntimeHistorySessionEventsPort` shim。

**Tech Stack:** Python 3.11+ via `uv run`, dataclasses, Protocol, pytest, existing MatMaster context assembly ports.

---

## Scope And Non-Negotiables

- 本计划只做 R7。不要顺手处理 R1-R6 或 E3 等其它 deferred simplification。
- 只在全部任务完成、验证通过后发起一个最终 PR。任务内可以分阶段 commit/checkpoint，但 PR 不是阶段边界。
- `matmaster/` 不得依赖 `src/`。因此 `src/services/session_event_codec.py` 可以依赖 `matmaster.context.ports.SessionEvent`，但 `matmaster/context/*` 不能 import codec。
- `matmaster/context/scanner.py` 终态只保留 typed event 扫描函数：`SkillHitRecord`、`scan_skill_hits`、`_skill_name_from_content`。
- `SessionEvent` 时间字段使用 `created_at_ms: int | None`，与 `ChatEventsTable._row_to_context_event()` 当前输出保持一致，不新增字符串型 `created_at` 字段。
- Legacy string skill hit 必须保留：typed content 可能是 `{"skill_name": "pxrd"}`、`{"value": "pxrd"}` 或迁移期旧值 `{"content": "pxrd"}`，scanner 必须都能识别。
- Core context 测试直接构造 `SessionEvent` fixture，不从 `tests/matmaster/context/*` import service codec。

## Current Behavior Map

| 维度 | 当前 Path A: `matmaster/context/scanner.py` | 当前 Path B: `src/services/context_assembly_ports.py` | 终态 |
|---|---|---|---|
| D1 未识别 Python 类型 | `str()` 降级 | 抛 `TypeError` | codec 抛 `TypeError` |
| D2 非 mapping content | `{"content": ...}` | `{"value": ...}` | codec 写 `{"value": ...}`；scanner 兼容 `skill_name/value/content` |
| D3 `None` content | `{}` | `{"value": None}` | codec 写 `{"value": None}` |
| D4 无合法 id | 丢弃整行 | `id=0` | `decode_session_events()` 丢弃，`row_to_event()` 抛 `ValueError` |
| D5 非 Mapping row | 跳过 | 抛错 | `decode_session_events()` 跳过，`row_to_event()` 抛 `TypeError` |
| D6 event type 备用键 | 只读 `type` | `type` or `event_type` | codec 支持 `type` or `event_type` |
| D7 event type strip | strip | 不 strip | codec strip |
| D8 optional str normalize | 空字符串 → None + strip | 原值透传 | codec 空字符串 → None + strip |
| D9 时间字段 | 注入 `content.created_at` | 无顶层字段 | codec 写 `SessionEvent.created_at_ms` |
| D10 顶层 schema | 分叉 | 分叉 | 全部路径同一个 `SessionEvent` dataclass |

## File Structure

- Create: `src/services/session_event_codec.py`  
  唯一 row decoder。提供 `freeze_json_value()`、`freeze_json_object()`、`coerce_event_id()`、`coerce_created_at_ms()`、`row_to_event()`、`decode_session_events()`。

- Modify: `matmaster/context/ports.py`  
  给 `SessionEvent` 增加 `created_at_ms: int | None = None`。

- Modify: `src/services/context_assembly_ports.py`  
  删除本地 `_freeze_json_value()`、`_freeze_json_object()`、`AppSessionEventsPort._row_to_event()`，改用 codec。

- Modify: `src/services/agent_run_history_wiring.py`  
  `_RunSessionEventHistory` 增加 async `load_events(query)`，内部通过 codec 返回 typed tuple。

- Modify: `matmaster/types/runtime_ports.py`  
  `SessionEventHistoryPort` 和 `EmptySessionEventHistory` 增加 typed `load_events()`，保留 `latest_scope_event_id()` 给 compactor 使用。

- Modify: `matmaster/core/runtime_context_assembly.py`  
  删除 `RuntimeHistorySessionEventsPort` 和 `coerce_session_events` import。`ContextAssemblyPorts.session_events` 直接接收 `history_port`。

- Modify: `src/services/agent_run_service.py`  
  active skill rehydration 从 `coerce_session_events(raw_events)` 改为 `decode_session_events(raw_events)`。

- Modify: `matmaster/context/scanner.py`  
  删除 row decoder，只保留 typed scanner。`SkillHitRecord` 使用 `created_at_ms`。

- Modify: `matmaster/context/session.py`  
  更新错误信息，去掉对 `matmaster.context.scanner.coerce_session_events` 的提示。

- Modify: `matmaster/context/sources/attachments.py`  
  删除只被测试使用的 raw row legacy scanner `scan_legacy_attachment_entries()`，从而去掉对 `scanner.coerce_event_id` 的依赖。

- Modify tests:
  - Create: `tests/matmaster/services/test_session_event_codec.py`
  - Modify: `tests/matmaster/services/test_context_assembly_ports.py`
  - Modify: `tests/matmaster/services/test_agent_run_history_wiring.py`
  - Modify: `tests/services/test_context_assembly_factory.py`
  - Modify: `tests/matmaster/context/test_scanner.py`
  - Modify: `tests/matmaster/context/test_session.py`
  - Modify: `tests/matmaster/context/sources/test_attachments.py`
  - Modify: `tests/matmaster/context/sources/test_skills.py`
  - Modify: `tests/matmaster/context/sources/test_tools.py`
  - Modify: `tests/matmaster/context/test_phase4_static_boundaries.py`
  - Modify every test fixture found by `rg -n "SessionEvent\\(" tests matmaster src`

---

### Task 1: Add Service-Layer SessionEvent Codec

**Files:**
- Create: `src/services/session_event_codec.py`
- Test: `tests/matmaster/services/test_session_event_codec.py`

- [ ] **Step 1: Write codec tests first**

Add `tests/matmaster/services/test_session_event_codec.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from matmaster.context.ports import SessionEvent
from src.services.session_event_codec import (
    coerce_created_at_ms,
    coerce_event_id,
    decode_session_events,
    freeze_json_object,
    row_to_event,
)


def test_row_to_event_maps_basic_fields_and_normalizes_strings() -> None:
    event = row_to_event(
        {
            "id": "10",
            "event_type": " query ",
            "source": " User ",
            "content": {"content": "hi", "files": ["a"]},
            "task_id": " task-1 ",
            "invocation_id": " ",
            "spawn_id": "",
            "created_at_ms": "1234",
        }
    )

    assert event == SessionEvent(
        id=10,
        event_type="query",
        source="User",
        content={"content": "hi", "files": ("a",)},
        task_id="task-1",
        invocation_id=None,
        spawn_id=None,
        created_at_ms=1234,
    )


def test_decode_session_events_drops_rows_without_valid_id() -> None:
    events = decode_session_events(
        [
            {"id": None, "type": "query"},
            {"id": True, "type": "query"},
            {"id": "not-an-int", "type": "query"},
            {"id": 9, "type": "query", "content": None},
        ]
    )

    assert len(events) == 1
    assert events[0].id == 9
    assert events[0].content == {"value": None}


def test_decode_session_events_skips_non_mapping_rows() -> None:
    events = decode_session_events(
        [
            ["not", "a", "row"],
            {"id": 1, "type": "query", "content": {"content": "ok"}},
        ]
    )

    assert [event.id for event in events] == [1]


def test_row_to_event_rejects_non_mapping_row() -> None:
    with pytest.raises(TypeError, match="Session event row must be a mapping"):
        row_to_event(["not", "a", "row"])  # type: ignore[arg-type]


def test_row_to_event_rejects_invalid_id() -> None:
    with pytest.raises(ValueError, match="valid id"):
        row_to_event({"id": "bad", "type": "query"})


def test_freeze_json_object_rejects_non_json_schema_drift() -> None:
    with pytest.raises(TypeError, match="Unsupported JSON value type"):
        freeze_json_object({"bad": object()})


def test_freeze_json_object_wraps_non_mapping_content_with_value_key() -> None:
    assert freeze_json_object("") == {"value": ""}
    assert freeze_json_object(None) == {"value": None}


def test_coerce_event_id_rejects_bool() -> None:
    assert coerce_event_id(True) is None
    assert coerce_event_id(False) is None


def test_coerce_created_at_ms_accepts_created_at_ms_and_datetime() -> None:
    assert coerce_created_at_ms({"created_at_ms": "42"}) == 42
    assert coerce_created_at_ms(
        {"created_at": datetime(2026, 1, 1, tzinfo=UTC)}
    ) == 1767225600000


def test_coerce_created_at_ms_ignores_invalid_values() -> None:
    assert coerce_created_at_ms({"created_at_ms": True}) is None
    assert coerce_created_at_ms({"created_at_ms": "bad"}) is None
    assert coerce_created_at_ms({"created_at": "2026-01-01T00:00:00"}) is None
```

- [ ] **Step 2: Run tests to verify they fail because codec does not exist**

Run:

```bash
uv run pytest tests/matmaster/services/test_session_event_codec.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.session_event_codec'`.

- [ ] **Step 3: Implement codec**

Create `src/services/session_event_codec.py`:

```python
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from matmaster.context.ports import JsonObject, JsonValue, SessionEvent


def freeze_json_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): freeze_json_value(inner) for key, inner in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(freeze_json_value(inner) for inner in value)
    raise TypeError(
        f"Unsupported JSON value type in context event payload: {type(value)!r}"
    )


def freeze_json_object(value: Any) -> JsonObject:
    if not isinstance(value, Mapping):
        return {"value": freeze_json_value(value)}
    return {str(key): freeze_json_value(inner) for key, inner in value.items()}


def coerce_event_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def coerce_created_at_ms(row: Mapping[str, Any]) -> int | None:
    raw_ms = row.get("created_at_ms")
    if raw_ms is not None and not isinstance(raw_ms, bool):
        try:
            return int(raw_ms)
        except (TypeError, ValueError):
            return None

    raw_created_at = row.get("created_at")
    timestamp = getattr(raw_created_at, "timestamp", None)
    if callable(timestamp):
        try:
            return int(timestamp() * 1000)
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    return None


def _row_to_event(row: Mapping[str, Any], event_id: int) -> SessionEvent:
    raw_content = row["content"] if "content" in row else None
    return SessionEvent(
        id=event_id,
        event_type=str(row.get("type") or row.get("event_type") or "").strip(),
        source=coerce_optional_str(row.get("source")),
        content=freeze_json_object(raw_content),
        task_id=coerce_optional_str(row.get("task_id")),
        invocation_id=coerce_optional_str(row.get("invocation_id")),
        spawn_id=coerce_optional_str(row.get("spawn_id")),
        created_at_ms=coerce_created_at_ms(row),
    )


def row_to_event(row: Mapping[str, Any]) -> SessionEvent:
    if not isinstance(row, Mapping):
        raise TypeError("Session event row must be a mapping")
    event_id = coerce_event_id(row.get("id"))
    if event_id is None:
        raise ValueError("Session event row must contain a valid id")
    return _row_to_event(row, event_id)


def decode_session_events(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[SessionEvent, ...]:
    events: list[SessionEvent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        event_id = coerce_event_id(row.get("id"))
        if event_id is None:
            continue
        events.append(_row_to_event(row, event_id))
    return tuple(events)
```

- [ ] **Step 4: Run codec tests**

Run:

```bash
uv run pytest tests/matmaster/services/test_session_event_codec.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

```bash
git add src/services/session_event_codec.py tests/matmaster/services/test_session_event_codec.py
git commit -m "refactor(context): add session event codec"
```

---

### Task 2: Add `created_at_ms` To SessionEvent And Update Scanner

**Files:**
- Modify: `matmaster/context/ports.py`
- Modify: `matmaster/context/scanner.py`
- Modify: `matmaster/context/session.py`
- Test: `tests/matmaster/context/test_scanner.py`
- Test: `tests/matmaster/context/test_ports.py`

- [ ] **Step 1: Update scanner tests to typed fixtures**

Replace decoder-based scanner tests in `tests/matmaster/context/test_scanner.py` with direct `SessionEvent` fixtures:

```python
from __future__ import annotations

from matmaster.context.ports import SessionEvent
from matmaster.context.scanner import SkillHitRecord, scan_skill_hits


def test_scan_skill_hits_accepts_session_events() -> None:
    events = (
        SessionEvent(id=1, event_type="query", source=None, content={"value": "skip"}),
        SessionEvent(
            id=2,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "pxrd"},
            created_at_ms=1767225600000,
        ),
        SessionEvent(
            id=3,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "mlip"},
        ),
        SessionEvent(
            id=4,
            event_type="skill_hit",
            source=None,
            content={"skill_name": "pxrd"},
        ),
        SessionEvent(
            id=5,
            event_type="skill_hit",
            source=None,
            content={"skill_name": ""},
        ),
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(
            skill_name="pxrd",
            event_id=2,
            created_at_ms=1767225600000,
        ),
        SkillHitRecord(skill_name="mlip", event_id=3, created_at_ms=None),
    )


def test_scan_skill_hits_accepts_value_wrapped_legacy_string_content() -> None:
    events = (
        SessionEvent(
            id=7,
            event_type="skill_hit",
            source=None,
            content={"value": "search"},
        ),
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(skill_name="search", event_id=7, created_at_ms=None),
    )


def test_scan_skill_hits_accepts_content_wrapped_migration_rows() -> None:
    events = (
        SessionEvent(
            id=8,
            event_type="skill_hit",
            source=None,
            content={"content": "legacy-search"},
        ),
    )

    records = scan_skill_hits(events)

    assert records == (
        SkillHitRecord(skill_name="legacy-search", event_id=8, created_at_ms=None),
    )
```

- [ ] **Step 2: Run scanner tests to verify they fail before implementation**

Run:

```bash
uv run pytest tests/matmaster/context/test_scanner.py -q
```

Expected: FAIL because `SessionEvent` does not accept `created_at_ms` and `SkillHitRecord` does not have `created_at_ms`.

- [ ] **Step 3: Add `created_at_ms` field**

Modify `matmaster/context/ports.py`:

```python
@dataclass(frozen=True)
class SessionEvent:
    """DB events row envelope for context assembly.

    `content` must preserve the raw DB payload shape after JSON parsing. For
    rows loaded through service-layer codecs, nested lists are converted to
    tuples by `freeze_json_object`; callers should not pass display-flattened
    User/query rows where files/images/workspace_paths were hoisted out.
    """

    id: int
    event_type: str
    source: str | None
    content: JsonObject
    task_id: str | None = None
    invocation_id: str | None = None
    spawn_id: str | None = None
    created_at_ms: int | None = None
```

- [ ] **Step 4: Remove row decoder from scanner and preserve typed scanning**

Modify `matmaster/context/scanner.py` so the whole file becomes:

```python
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from matmaster.context.ports import JsonValue, SessionEvent


@dataclass(frozen=True)
class SkillHitRecord:
    skill_name: str
    event_id: int | None = None
    created_at_ms: int | None = None


def _skill_name_from_content(content: JsonValue) -> str:
    if isinstance(content, Mapping):
        raw = (
            content.get("skill_name")
            or content.get("value")
            or content.get("content")
        )
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
        records.append(
            SkillHitRecord(
                skill_name=name,
                event_id=event.id,
                created_at_ms=event.created_at_ms,
            )
        )
    return tuple(records)
```

- [ ] **Step 5: Update SessionContextBuilder error text**

Modify `matmaster/context/session.py`:

```python
def __post_init__(self) -> None:
    if not isinstance(self.events, tuple):
        raise TypeError(
            "SessionContextBuilder.events must be a tuple of SessionEvent; "
            "service-layer callers should decode raw rows before constructing it"
        )
```

- [ ] **Step 6: Run scanner and port tests**

Run:

```bash
uv run pytest tests/matmaster/context/test_scanner.py tests/matmaster/context/test_ports.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit checkpoint**

```bash
git add matmaster/context/ports.py matmaster/context/scanner.py matmaster/context/session.py tests/matmaster/context/test_scanner.py tests/matmaster/context/test_ports.py
git commit -m "refactor(context): keep scanner on typed events"
```

---

### Task 3: Route AppSessionEventsPort Through Codec

**Files:**
- Modify: `src/services/context_assembly_ports.py`
- Modify: `tests/matmaster/services/test_context_assembly_ports.py`

- [ ] **Step 1: Update AppSessionEventsPort tests**

In `tests/matmaster/services/test_context_assembly_ports.py`:

- Import `freeze_json_object` from `src.services.session_event_codec`.
- Remove imports of `_freeze_json_object` from `src.services.context_assembly_ports`.
- Add assertions for `created_at_ms`, invalid id filtering, and normalized optional strings:

```python
@pytest.mark.asyncio
async def test_app_session_events_port_filters_rows_without_valid_id() -> None:
    table = FakeEventsTable(
        rows=[
            {"id": None, "source": "User", "type": "query", "content": {}},
            {"id": "bad", "source": "User", "type": "query", "content": {}},
            {"id": 6, "source": "User", "type": "query", "content": {}},
        ]
    )

    events = await AppSessionEventsPort(table).load_events(
        SessionEventQuery(session_id="sess-1", spawn_id=None)
    )

    assert [event.id for event in events] == [6]
```

Also update the existing falsy raw content assertion:

```python
assert events[0].content == {"value": ""}
```

- [ ] **Step 2: Run service port tests**

Run:

```bash
uv run pytest tests/matmaster/services/test_context_assembly_ports.py -q
```

Expected: FAIL before wiring because `AppSessionEventsPort` still uses its local decoder.

- [ ] **Step 3: Modify `src/services/context_assembly_ports.py`**

Remove local `JsonObject`, `JsonValue`, `_freeze_json_value()`, `_freeze_json_object()`, and `_row_to_event()`. Keep only service ports and import codec:

```python
from src.services.session_event_codec import decode_session_events
```

Change `AppSessionEventsPort.load_events()`:

```python
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
    return decode_session_events(rows)
```

- [ ] **Step 4: Run service port and codec tests**

Run:

```bash
uv run pytest tests/matmaster/services/test_context_assembly_ports.py tests/matmaster/services/test_session_event_codec.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

```bash
git add src/services/context_assembly_ports.py tests/matmaster/services/test_context_assembly_ports.py
git commit -m "refactor(context): decode app session events through codec"
```

---

### Task 4: Upgrade Runtime History To Typed `load_events`

**Files:**
- Modify: `matmaster/types/runtime_ports.py`
- Modify: `src/services/agent_run_history_wiring.py`
- Modify: `matmaster/core/runtime_context_assembly.py`
- Modify: `tests/matmaster/types/test_runtime_ports.py`
- Modify: `tests/matmaster/services/test_agent_run_history_wiring.py`
- Modify: `tests/services/test_context_assembly_factory.py`
- Modify: `tests/matmaster/core/test_exp_runtime_v2.py`

- [ ] **Step 1: Add failing runtime history tests**

In `tests/matmaster/services/test_agent_run_history_wiring.py`, add:

```python
import pytest

from matmaster.context.ports import SessionEventQuery


@pytest.mark.asyncio
async def test_history_wiring_load_events_returns_typed_session_events() -> None:
    class EventsTable:
        def get_session_user_query_events(self, session_id):
            return []

        def query_context_events(self, **kwargs):
            self.context_kwargs = kwargs
            return [
                {
                    "id": "3",
                    "source": " User ",
                    "type": " query ",
                    "content": {"content": "old", "files": ["a"]},
                    "created_at_ms": 100,
                }
            ]

        def get_latest_scope_event_id(self, session_id, spawn_id):
            return 3

        def get_bohrium_events(self, session_id):
            return []

    table = EventsTable()

    with patch(
        "src.services.agent_run_history_wiring.ModelHistoryRestoreService"
    ) as restore_cls:
        restore_cls.return_value.restore_history.return_value = []
        result = _build_history_wiring(events_table=table)

    history = result.runtime_ports.compaction.history
    assert history is not None
    events = await history.load_events(
        SessionEventQuery(session_id="sess-1", spawn_id=None, until_event_id=3)
    )

    assert events[0].id == 3
    assert events[0].source == "User"
    assert events[0].content["files"] == ("a",)
    assert table.context_kwargs["session_id"] == "sess-1"
```

In `tests/matmaster/types/test_runtime_ports.py`, add:

```python
@pytest.mark.asyncio
async def test_empty_session_event_history_load_events_returns_empty() -> None:
    from matmaster.context.ports import SessionEventQuery

    history = EmptySessionEventHistory()

    assert (
        await history.load_events(SessionEventQuery(session_id="sess-1", spawn_id=None))
    ) == ()
```

- [ ] **Step 2: Run targeted tests to see failures**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_history_wiring.py tests/matmaster/types/test_runtime_ports.py -q
```

Expected: FAIL because history ports do not expose `load_events()`.

- [ ] **Step 3: Update runtime port protocol**

Modify `matmaster/types/runtime_ports.py` imports:

```python
from matmaster.context.ports import SessionEvent, SessionEventQuery
```

Extend `SessionEventHistoryPort`:

```python
@runtime_checkable
class SessionEventHistoryPort(Protocol):
    async def load_events(
        self,
        query: SessionEventQuery,
    ) -> tuple[SessionEvent, ...]: ...

    def query_events(self) -> list[dict[str, Any]]: ...

    def all_events(self) -> list[dict[str, Any]]: ...

    def latest_checkpoint_covered_until_event_id(self) -> int | None: ...

    def latest_scope_event_id(self) -> int | None: ...
```

Remove `query_context_events()` from the protocol and from `EmptySessionEventHistory`. Add:

```python
async def load_events(
    self,
    query: SessionEventQuery,
) -> tuple[SessionEvent, ...]:
    return ()
```

- [ ] **Step 4: Update `_RunSessionEventHistory`**

Modify `src/services/agent_run_history_wiring.py`:

```python
import asyncio
```

Add imports:

```python
from matmaster.context.ports import SessionEvent, SessionEventQuery
from src.services.session_event_codec import decode_session_events
```

In `_RunSessionEventHistory`, replace `query_context_events()` with:

```python
async def load_events(
    self,
    query: SessionEventQuery,
) -> tuple[SessionEvent, ...]:
    rows = await asyncio.to_thread(
        _query_context_events,
        spawn_id=query.spawn_id,
        until_event_id=query.until_event_id,
        event_types=query.event_types,
        limit=query.limit,
        order=query.order,
    )
    return decode_session_events(rows)
```

- [ ] **Step 5: Delete runtime adapter**

Modify `matmaster/core/runtime_context_assembly.py`:

- Remove `from collections.abc import Callable, Mapping` if `Mapping` becomes unused.
- Remove `from matmaster.context.scanner import coerce_session_events`.
- Remove the entire `RuntimeHistorySessionEventsPort` class.
- Change assembly ports construction to:

```python
assembly_ports = ContextAssemblyPorts(
    session_events=history_port,
    session_jobs=_EmptySessionJobsPort(),
)
```

Keep:

```python
runtime_covered_until_provider=history_port.latest_scope_event_id,
```

- [ ] **Step 6: Update tests that mock runtime history**

Search:

```bash
rg -n "query_context_events\\(|RuntimeHistorySessionEventsPort|EmptySessionEventHistory" tests matmaster src
```

Apply these exact changes:

- In `tests/services/test_context_assembly_factory.py`, delete `test_runtime_history_events_port_filters_existing_history_rows` because the adapter no longer exists.
- In `tests/matmaster/core/test_exp_runtime_v2.py`, replace `def query_context_events(self, **kwargs): return []` in `RuntimeHistory` with:

```python
async def load_events(self, query):
    self.context_query = query
    return ()
```

- In any remaining test fake that implements `query_context_events()` only to satisfy `SessionEventHistoryPort`, replace it with async `load_events()`.

- [ ] **Step 7: Run runtime history tests**

Run:

```bash
uv run pytest tests/matmaster/services/test_agent_run_history_wiring.py tests/matmaster/types/test_runtime_ports.py tests/services/test_context_assembly_factory.py tests/matmaster/core/test_exp_runtime_v2.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit checkpoint**

```bash
git add matmaster/types/runtime_ports.py src/services/agent_run_history_wiring.py matmaster/core/runtime_context_assembly.py tests/matmaster/types/test_runtime_ports.py tests/matmaster/services/test_agent_run_history_wiring.py tests/services/test_context_assembly_factory.py tests/matmaster/core/test_exp_runtime_v2.py
git commit -m "refactor(context): use typed runtime history events"
```

---

### Task 5: Switch Active Skill Rehydration To Codec

**Files:**
- Modify: `src/services/agent_run_service.py`
- Modify: `tests/matmaster/services/test_lazy_mcp_replay.py`
- Modify: `tests/matmaster/services/test_active_mcp_replay.py`

- [ ] **Step 1: Add regression for value-wrapped skill hits**

In `tests/matmaster/services/test_active_mcp_replay.py`, add a case that decodes raw rows through service codec:

```python
from src.services.session_event_codec import decode_session_events


def test_value_wrapped_skill_hit_resolves_runnable_server(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write_skill(root, "test-skill", "mat_sg")
    registry = SkillRegistry([root])
    events = [{"id": 1, "type": "skill_hit", "content": "test-skill"}]

    skills = resolve_active_skills(decode_session_events(events), registry)

    assert [skill.meta_info.name for skill in skills] == ["test-skill"]
```

- [ ] **Step 2: Run active MCP replay tests**

Run:

```bash
uv run pytest tests/matmaster/services/test_active_mcp_replay.py tests/matmaster/services/test_lazy_mcp_replay.py -q
```

Expected: FAIL until `agent_run_service.py` imports and uses codec.

- [ ] **Step 3: Modify active skill rehydration**

In `src/services/agent_run_service.py`, replace:

```python
from matmaster.context.scanner import coerce_session_events
```

with:

```python
from src.services.session_event_codec import decode_session_events
```

Replace:

```python
skills = resolve_active_skills(coerce_session_events(raw_events), registry)
```

with:

```python
skills = resolve_active_skills(decode_session_events(raw_events), registry)
```

- [ ] **Step 4: Update tests that import `coerce_session_events` only for active skill replay**

Search:

```bash
rg -n "coerce_session_events" tests/matmaster/services tests/services src/services
```

For service-layer tests, import `decode_session_events` from `src.services.session_event_codec`. Do not change core context tests in this task.

- [ ] **Step 5: Run service replay tests**

Run:

```bash
uv run pytest tests/matmaster/services/test_active_mcp_replay.py tests/matmaster/services/test_lazy_mcp_replay.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit checkpoint**

```bash
git add src/services/agent_run_service.py tests/matmaster/services/test_active_mcp_replay.py tests/matmaster/services/test_lazy_mcp_replay.py
git commit -m "refactor(context): decode active skill events through codec"
```

---

### Task 6: Remove Raw Row Helpers From Core Context Tests And Attachment Source

**Files:**
- Modify: `matmaster/context/sources/attachments.py`
- Modify: `tests/matmaster/context/sources/test_attachments.py`
- Modify: `tests/matmaster/context/sources/test_attachment_source_legacy_scan.py`
- Modify: `tests/matmaster/context/sources/test_skills.py`
- Modify: `tests/matmaster/context/sources/test_tools.py`
- Modify: `tests/matmaster/context/test_session.py`

- [ ] **Step 1: Remove legacy attachment row scanner tests**

Delete `tests/matmaster/context/sources/test_attachment_source_legacy_scan.py`.

In `tests/matmaster/context/sources/test_attachments.py`:

- Remove import of `scan_legacy_attachment_entries`.
- Delete `test_scan_legacy_attachment_entries_reads_top_level_metadata_without_id`.
- Replace every `coerce_session_events(_QUERY_EVENTS)` with direct fixture helper:

```python
from matmaster.context.ports import SessionEvent


def _session_events(rows: list[dict]) -> tuple[SessionEvent, ...]:
    events: list[SessionEvent] = []
    for row in rows:
        events.append(
            SessionEvent(
                id=int(row["id"]),
                source=row.get("source"),
                event_type=str(row.get("type") or ""),
                content=row.get("content") or {"value": None},
            )
        )
    return tuple(events)
```

- [ ] **Step 2: Update context source tests to direct SessionEvent fixtures**

In `tests/matmaster/context/sources/test_skills.py`, `tests/matmaster/context/sources/test_tools.py`, and `tests/matmaster/context/test_session.py`, remove `coerce_session_events` imports and use direct `SessionEvent(...)` fixtures. For example:

```python
events = (
    SessionEvent(
        id=1,
        event_type="skill_hit",
        source=None,
        content={"skill_name": "pxrd"},
    ),
)
```

- [ ] **Step 3: Remove legacy raw scanner from attachments source**

Modify `matmaster/context/sources/attachments.py`:

- Remove `from matmaster.context.scanner import coerce_event_id`.
- Delete `_legacy_query_payload()`.
- Delete `scan_legacy_attachment_entries()`.
- Keep `scan_attachment_entries()` typed-only.

- [ ] **Step 4: Run core context tests**

Run:

```bash
uv run pytest tests/matmaster/context/test_session.py tests/matmaster/context/sources/test_attachments.py tests/matmaster/context/sources/test_skills.py tests/matmaster/context/sources/test_tools.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

```bash
git add matmaster/context/sources/attachments.py tests/matmaster/context/test_session.py tests/matmaster/context/sources/test_attachments.py tests/matmaster/context/sources/test_skills.py tests/matmaster/context/sources/test_tools.py
git rm tests/matmaster/context/sources/test_attachment_source_legacy_scan.py
git commit -m "refactor(context): remove raw row scanning from core context"
```

---

### Task 7: Add Boundary Tests And Remove Remaining Imports

**Files:**
- Modify: `tests/matmaster/context/test_phase4_static_boundaries.py`
- Modify: every file found by searches below

- [ ] **Step 1: Add static boundary tests**

Append to `tests/matmaster/context/test_phase4_static_boundaries.py`:

```python
def test_context_scanner_does_not_decode_raw_rows() -> None:
    scanner_path = ROOT / "matmaster" / "context" / "scanner.py"
    text = scanner_path.read_text(encoding="utf-8")

    forbidden = [
        "coerce_session_events",
        "coerce_event_id",
        "_freeze_json_value",
        "_coerce_content",
        "_coerce_optional_str",
        "Mapping[str, Any]",
    ]
    for token in forbidden:
        assert token not in text


def test_core_context_does_not_import_service_codec() -> None:
    context_root = ROOT / "matmaster" / "context"
    offenders = []
    for path in context_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "src.services.session_event_codec" in text:
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []
```

- [ ] **Step 2: Run boundary tests and fix failures**

Run:

```bash
uv run pytest tests/matmaster/context/test_phase4_static_boundaries.py -q
```

Expected: FAIL until all stale imports are gone.

Fix every match from:

```bash
rg -n "coerce_session_events|coerce_event_id|RuntimeHistorySessionEventsPort|query_context_events\\(" matmaster src tests
```

Allowed remaining `query_context_events(` matches:

- `src/dao/chat_events_table.py`
- `src/services/context_assembly_ports.py`
- private service-layer table calls inside `src/services/agent_run_history_wiring.py`
- DAO-focused tests under `tests/test_chat_events_*`

- [ ] **Step 3: Run boundary tests again**

Run:

```bash
uv run pytest tests/matmaster/context/test_phase4_static_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit checkpoint**

```bash
git add tests/matmaster/context/test_phase4_static_boundaries.py matmaster src tests
git commit -m "test(context): enforce typed session event boundaries"
```

---

### Task 8: Final Regression Suite And PR Preparation

**Files:**
- Verify only, no planned source edits

- [ ] **Step 1: Run targeted regression suites**

Run:

```bash
uv run pytest tests/matmaster/context tests/matmaster/services tests/services/test_context_assembly_factory.py tests/matmaster/types/test_runtime_ports.py tests/matmaster/core/test_exp_runtime_v2.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run pytest tests -q
```

Expected: PASS.

- [ ] **Step 3: Verify no stale raw decoder references**

Run:

```bash
rg -n "coerce_session_events|RuntimeHistorySessionEventsPort|content\\.get\\(\"created_at\"\\)" matmaster src tests
rg -n "created_at: str \\| None" matmaster src tests
```

Expected: no matches.

- [ ] **Step 4: Verify final diff is scoped to R7**

Run:

```bash
git diff --stat
git diff --name-only
```

Expected changed paths are limited to R7 files listed in this plan and their tests.

- [ ] **Step 5: Prepare one final PR**

Use one PR for the completed R7 work:

```text
Title: refactor(context): unify SessionEvent decoding

Summary:
- Add a service-layer SessionEvent codec and route app/runtime history through it.
- Remove raw DAO row decoding from matmaster/context scanner and context source tests.
- Add typed created_at_ms metadata to SessionEvent and preserve legacy skill hit replay.

Tests:
- uv run pytest tests/matmaster/context tests/matmaster/services tests/services/test_context_assembly_factory.py tests/matmaster/types/test_runtime_ports.py tests/matmaster/core/test_exp_runtime_v2.py -q
- uv run pytest tests -q
```

---

## Self-Review Checklist

- [ ] R7 remains a single PR after all tasks complete.
- [ ] `src/services/session_event_codec.py` is the only row → `SessionEvent` decoder.
- [ ] `matmaster/context/scanner.py` no longer imports `Any` or exposes row coercion helpers.
- [ ] `scan_skill_hits()` handles `skill_name`, `value`, and migration `content` keys.
- [ ] `SessionEvent` uses `created_at_ms`, not `created_at`.
- [ ] `RuntimeHistorySessionEventsPort` is deleted.
- [ ] Core context tests construct `SessionEvent` directly and do not import service codec.
- [ ] `scan_legacy_attachment_entries()` is removed with its tests because current grep shows no production use.
- [ ] Full tests run with `uv run pytest`, not system Python.
