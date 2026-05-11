# Available Attachments Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore files, images, and workspace paths from previous chat turns into each new user prompt as a compact `[Available attachments]` manifest.

**Architecture:** Keep the existing event storage model unchanged: user query events already persist `files`, `images`, and `workspace_paths` inside JSON content. Add a small formatter service that scans persisted user query events, deduplicates attachments in first-seen order, assigns stable per-kind labels such as `file_1`, `image_1`, and `workspace_1`, and appends the manifest to the current prompt before the job is enqueued. This phase deliberately does not add URL downloading, attachment resolver logic, or tool argument rewriting; agents will use tool schemas and tool errors to decide whether to pass URLs directly or download first.

**Tech Stack:** FastAPI, Pydantic, uv-managed Python, pytest, existing `ChatEventsService`, existing `ChatSendRequest`, existing Redis worker queue flow.

---

## File Map

- Create `src/services/attachment_manifest_service.py`: parse user query events, normalize attachment display names, preserve existing file/image URL path percent-encoding behavior, deduplicate entries, format the `[Available attachments]` prompt block.
- Modify `src/apis/chat_api.py`: inject `ChatEventsService` into `chat_stream()`, build the attachment manifest from persisted events after `prepare_send_message()` writes the current query, and replace the current ad hoc `[Attached files]` / `[Workspace paths]` prompt suffix with the unified manifest block.
- Add `tests/services/test_attachment_manifest_service.py`: unit tests for parsing top-level and JSON-content query events, deduplication, stable labels, URL basename extraction, and empty output.
- Add `tests/apis/test_chat_api_available_attachments.py`: API-level helper test that verifies the prompt builder used by `chat_stream()` includes `[Available attachments]` with previous and current attachments without opening a long-lived SSE response.

## Scope Rules

- Do not download attachment URLs in this phase.
- Do not rewrite tool call arguments.
- Do not change database schema.
- Do not change frontend replay payloads.
- Do not remove image vision input behavior; `images` still flow through `current_user_images` and historical `UserMessage.images`.
- Treat the manifest as prompt context for the agent, not as a strict machine parsing protocol. Labels such as `file_1` and `image_1` are the stable references.
- It is intentional that current and historical images can be visible through both the vision message channel and the text manifest.
- Include only root user query events returned by `ChatEventsService.get_session_events()` in this phase; spawned-agent attachments are out of scope.
- Keep the existing lightweight URL contract: agents receive URLs/paths and decide whether to pass them directly or download them based on tool schemas and tool errors.
- Preserve the previous defensive URL path normalization for file/image URLs by percent-encoding decoded URL paths.
- Use a simple total manifest cap of 30 entries. This cap is across all attachment kinds, not per kind. Keep the first 30 unique attachments in event order and silently omit later unique attachments.
- Expired OSS URLs, MIME hints, and file size hints are out of scope for this phase.
- Keep the manifest compact: one line per attachment, in this shape:

```text
[Available attachments]
file_1 data.csv https://oss.example.com/chat/data.csv
image_1 em.png https://oss.example.com/chat/em.png
workspace_1 /share/a.cif
```

## Task 1: Attachment Manifest Service

**Files:**
- Create: `src/services/attachment_manifest_service.py`
- Test: `tests/services/test_attachment_manifest_service.py`

- [ ] **Step 1: Write failing unit tests for manifest extraction**

Create `tests/services/test_attachment_manifest_service.py`:

```python
from src.services.attachment_manifest_service import (
    AttachmentEntry,
    build_available_attachments,
    format_available_attachments,
)


def test_build_available_attachments_reads_top_level_query_metadata() -> None:
    # Production shape returned by ChatEventsTable.get_session_events(): content
    # is unwrapped and attachment metadata is promoted to top-level fields.
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "analyze attachments",
            "files": ["https://oss.example.com/chat/data.csv"],
            "images": ["https://oss.example.com/chat/em.png"],
            "workspace_paths": ["/share/a.cif"],
        }
    ]

    entries = build_available_attachments(events)

    assert entries == [
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="data.csv",
            value="https://oss.example.com/chat/data.csv",
        ),
        AttachmentEntry(
            kind="image",
            label="image_1",
            name="em.png",
            value="https://oss.example.com/chat/em.png",
        ),
        AttachmentEntry(
            kind="workspace",
            label="workspace_1",
            name="/share/a.cif",
            value="/share/a.cif",
        ),
    ]


def test_build_available_attachments_reads_json_content_query_metadata() -> None:
    # Defensive compatibility for raw DB/checkpoint-like rows where content still
    # contains the metadata dict.
    events = [
        {
            "source": "User",
            "type": "query",
            "content": {
                "content": "old turn",
                "files": ["https://oss.example.com/chat/old%20data.csv"],
                "images": ["https://oss.example.com/chat/old-em.webp"],
                "workspace_paths": ["/share/old.cif"],
            },
        }
    ]

    entries = build_available_attachments(events)

    assert [(entry.label, entry.name, entry.value) for entry in entries] == [
        (
            "file_1",
            "old data.csv",
            "https://oss.example.com/chat/old%20data.csv",
        ),
        (
            "image_1",
            "old-em.webp",
            "https://oss.example.com/chat/old-em.webp",
        ),
        ("workspace_1", "/share/old.cif", "/share/old.cif"),
    ]


def test_build_available_attachments_deduplicates_by_kind_and_value() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "first",
            "files": ["https://oss.example.com/chat/data.csv"],
            "images": ["https://oss.example.com/chat/em.png"],
        },
        {
            "source": "User",
            "type": "query",
            "content": {
                "content": "second",
                "files": ["https://oss.example.com/chat/data.csv"],
                "images": ["https://oss.example.com/chat/em.png"],
                "workspace_paths": ["/share/a.cif"],
            },
        },
    ]

    entries = build_available_attachments(events)

    assert [(entry.kind, entry.label, entry.value) for entry in entries] == [
        ("file", "file_1", "https://oss.example.com/chat/data.csv"),
        ("image", "image_1", "https://oss.example.com/chat/em.png"),
        ("workspace", "workspace_1", "/share/a.cif"),
    ]


def test_build_available_attachments_filters_non_user_query_and_invalid_values() -> None:
    events = [
        {
            "source": "System",
            "type": "query",
            "content": "skip",
            "files": ["https://oss.example.com/chat/system.csv"],
        },
        {
            "source": "User",
            "type": "response",
            "content": "skip",
            "images": ["https://oss.example.com/chat/not-query.png"],
        },
        {
            "source": "User",
            "type": "query",
            "content": "keep",
            "files": [" ", 3, "https://oss.example.com/chat/keep.csv"],
            "images": [None, "https://oss.example.com/chat/keep.png"],
            "workspace_paths": ["", " /share/keep.cif "],
        },
    ]

    entries = build_available_attachments(events)

    assert [(entry.kind, entry.label, entry.value) for entry in entries] == [
        ("file", "file_1", "https://oss.example.com/chat/keep.csv"),
        ("image", "image_1", "https://oss.example.com/chat/keep.png"),
        ("workspace", "workspace_1", "/share/keep.cif"),
    ]


def test_build_available_attachments_normalizes_file_and_image_url_paths() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "raw urls",
            "files": ["https://oss.example.com/chat/raw data.csv"],
            "images": ["https://oss.example.com/chat/显微 图.png"],
        }
    ]

    entries = build_available_attachments(events)

    assert [(entry.label, entry.name, entry.value) for entry in entries] == [
        (
            "file_1",
            "raw data.csv",
            "https://oss.example.com/chat/raw%20data.csv",
        ),
        (
            "image_1",
            "显微 图.png",
            "https://oss.example.com/chat/%E6%98%BE%E5%BE%AE%20%E5%9B%BE.png",
        ),
    ]


def test_dedup_treats_raw_and_percent_encoded_as_same_url() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "t1",
            "files": ["https://oss.example.com/chat/raw data.csv"],
        },
        {
            "source": "User",
            "type": "query",
            "content": "t2",
            "files": ["https://oss.example.com/chat/raw%20data.csv"],
        },
    ]

    entries = build_available_attachments(events)

    assert len(entries) == 1
    assert entries[0].label == "file_1"
    assert entries[0].name == "raw data.csv"
    assert entries[0].value == "https://oss.example.com/chat/raw%20data.csv"


def test_build_available_attachments_uses_simple_total_max_entries_limit() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "many",
            "files": [
                "https://oss.example.com/chat/1.csv",
                "https://oss.example.com/chat/2.csv",
            ],
            "images": ["https://oss.example.com/chat/3.png"],
        }
    ]

    entries = build_available_attachments(events, max_entries=2)

    assert [(entry.kind, entry.label, entry.value) for entry in entries] == [
        ("file", "file_1", "https://oss.example.com/chat/1.csv"),
        ("file", "file_2", "https://oss.example.com/chat/2.csv"),
    ]


def test_format_available_attachments_outputs_compact_block() -> None:
    entries = [
        AttachmentEntry(
            kind="file",
            label="file_1",
            name="data.csv",
            value="https://oss.example.com/chat/data.csv",
        ),
        AttachmentEntry(
            kind="image",
            label="image_1",
            name="em.png",
            value="https://oss.example.com/chat/em.png",
        ),
        AttachmentEntry(
            kind="workspace",
            label="workspace_1",
            name="/share/a.cif",
            value="/share/a.cif",
        ),
    ]

    assert format_available_attachments(entries) == (
        "[Available attachments]\n"
        "file_1 data.csv https://oss.example.com/chat/data.csv\n"
        "image_1 em.png https://oss.example.com/chat/em.png\n"
        "workspace_1 /share/a.cif"
    )


def test_format_available_attachments_returns_empty_string_without_entries() -> None:
    assert format_available_attachments([]) == ""
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
uv run pytest tests/services/test_attachment_manifest_service.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'src.services.attachment_manifest_service'`.

- [ ] **Step 3: Implement the attachment manifest service**

Create `src/services/attachment_manifest_service.py`:

```python
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, unquote, urlparse, urlunparse

from src.utils.chat_event_source import normalize_event_source


AttachmentKind = Literal["file", "image", "workspace"]


@dataclass(frozen=True)
class AttachmentEntry:
    kind: AttachmentKind
    label: str
    name: str
    value: str


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _query_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    content = event.get("content")
    if isinstance(content, dict):
        payload.update(content)
    for key in ("files", "images", "workspace_paths"):
        if key in event:
            payload[key] = event.get(key)
    return payload


def _name_from_url(value: str, fallback: str) -> str:
    parsed = urlparse(value)
    path = unquote(parsed.path or "")
    basename = posixpath.basename(path)
    return basename or fallback


def _entry_name(kind: AttachmentKind, value: str) -> str:
    if kind == "file":
        return _name_from_url(value, "file")
    if kind == "image":
        return _name_from_url(value, "image")
    return value


def _normalize_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return urlunparse(
        parsed._replace(path=quote(unquote(parsed.path or ""), safe="/"))
    )


def _normalize_attachment_value(kind: AttachmentKind, value: str) -> str:
    if kind in {"file", "image"}:
        return _normalize_url(value)
    return value


def build_available_attachments(
    events: list[dict[str, Any]],
    *,
    max_entries: int = 30,
) -> list[AttachmentEntry]:
    counters: dict[AttachmentKind, int] = {
        "file": 0,
        "image": 0,
        "workspace": 0,
    }
    seen: set[tuple[AttachmentKind, str]] = set()
    entries: list[AttachmentEntry] = []

    def add(kind: AttachmentKind, value: str) -> None:
        if len(entries) >= max_entries:
            return
        normalized_value = _normalize_attachment_value(kind, value)
        key = (kind, normalized_value)
        if key in seen:
            return
        seen.add(key)
        counters[kind] += 1
        entries.append(
            AttachmentEntry(
                kind=kind,
                label=f"{kind}_{counters[kind]}",
                name=_entry_name(kind, normalized_value),
                value=normalized_value,
            )
        )

    for event in events:
        if normalize_event_source(event.get("source")) != "User":
            continue
        if (event.get("type") or "").strip() != "query":
            continue
        payload = _query_payload(event)
        for value in _string_list(payload.get("files")):
            add("file", value)
        for value in _string_list(payload.get("images")):
            add("image", value)
        for value in _string_list(payload.get("workspace_paths")):
            add("workspace", value)

    return entries


def format_available_attachments(entries: list[AttachmentEntry]) -> str:
    if not entries:
        return ""
    lines = ["[Available attachments]"]
    for entry in entries:
        if entry.kind == "workspace":
            lines.append(f"{entry.label} {entry.value}")
        else:
            lines.append(f"{entry.label} {entry.name} {entry.value}")
    return "\n".join(lines)


def append_available_attachments(
    prompt: str,
    events: list[dict[str, Any]],
    *,
    max_entries: int = 30,
) -> str:
    block = format_available_attachments(
        build_available_attachments(events, max_entries=max_entries)
    )
    if not block:
        return prompt
    if not prompt:
        return block
    return f"{prompt}\n\n{block}"
```

- [ ] **Step 4: Run the service tests and verify GREEN**

Run:

```bash
uv run pytest tests/services/test_attachment_manifest_service.py -q
```

Expected: `9 passed`.

- [ ] **Step 5: Commit the service slice**

Run:

```bash
git add src/services/attachment_manifest_service.py tests/services/test_attachment_manifest_service.py
git commit -m "feat: build available attachment manifests"
```

Expected: commit succeeds with no commit trailers.

## Task 2: Chat Stream Prompt Integration

**Files:**
- Modify: `src/apis/chat_api.py`
- Test: `tests/apis/test_chat_api_available_attachments.py`

- [ ] **Step 1: Write a failing API prompt-builder regression test**

Create `tests/apis/test_chat_api_available_attachments.py`:

```python
from src.apis.chat_api import _build_agent_prompt


def test_build_agent_prompt_appends_available_attachments() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "old turn",
            "files": ["https://oss.example.com/chat/old.csv"],
            "images": ["https://oss.example.com/chat/old.png"],
            "workspace_paths": ["/share/old.cif"],
            "session_id": "sess-attachments",
            "task_id": "task-old",
        },
        {
            "source": "User",
            "type": "query",
            "content": "new turn",
            "files": ["https://oss.example.com/chat/new.csv"],
            "images": ["https://oss.example.com/chat/new.png"],
            "workspace_paths": ["/share/new.cif"],
            "session_id": "sess-attachments",
            "task_id": "task-new",
        },
    ]

    prompt = _build_agent_prompt("new turn", events)

    assert "[Available attachments]" in prompt
    assert "file_1 old.csv https://oss.example.com/chat/old.csv" in prompt
    assert "file_2 new.csv https://oss.example.com/chat/new.csv" in prompt
    assert "image_1 old.png https://oss.example.com/chat/old.png" in prompt
    assert "image_2 new.png https://oss.example.com/chat/new.png" in prompt
    assert "workspace_1 /share/old.cif" in prompt
    assert "workspace_2 /share/new.cif" in prompt
```

Create the new `tests/apis/` directory if needed. Do not create `tests/apis/__init__.py`; sibling pytest-only directories such as `tests/services/` also do not use package marker files.

- [ ] **Step 2: Run the API prompt-builder test and verify RED**

Run:

```bash
uv run pytest tests/apis/test_chat_api_available_attachments.py -q
```

Expected: fail with `ImportError` because `_build_agent_prompt` does not exist.

- [ ] **Step 3: Import the manifest helper in `chat_api.py`**

In `src/apis/chat_api.py`, add this import near the existing service imports:

```python
from src.services.attachment_manifest_service import append_available_attachments
```

- [ ] **Step 4: Inject `ChatEventsService` into `chat_stream()`**

Update the `chat_stream()` signature in `src/apis/chat_api.py`:

```python
async def chat_stream(
    request: Request,
    session_id: str = Path(..., description="会话 ID", examples=["session-001"]),
    req: ChatSendRequest | None = Body(
        None,
        openapi_examples={
            "subscribe_only": {
                "summary": "仅订阅",
                "description": "不发送新消息，只建立 SSE 订阅。",
                "value": {"content": "", "mode": "direct"},
            },
            "send_message": {
                "summary": "发送消息",
                "description": "发送一条新消息，并返回本次运行的 SSE 流。",
                "value": {
                    "content": "请总结项目 42 下最近一次实验结果",
                    "mode": "direct",
                    "bohrium_project_id": 42,
                    "directory": "/share/workspace/run1",
                },
            },
        },
    ),
    user_id: str | None = Depends(UserService.optional_user_id),
    org_id: str | None = Depends(UserService.optional_org_id),
    chat_svc: ChatSessionsService = Depends(get_sessions_service),
    stream_svc: ChatStreamService = Depends(get_stream_service),
    events_svc: ChatEventsService = Depends(get_events_service),
):
```

- [ ] **Step 5: Add the API prompt helper**

In `src/apis/chat_api.py`, add this helper near `_session_workspace_data_from_row()`:

```python
def _build_agent_prompt(content: str, events: list[dict]) -> str:
    return append_available_attachments((content or "").strip(), events)
```

- [ ] **Step 6: Replace the ad hoc current-attachment prompt suffix**

Apply this replacement after the `if ctx is None: ...` conflict block and before `return StreamingResponse(...)`. Do not move `events_svc.get_session_events(sid)` above `prepare_send_message()`, because `prepare_send_message()` persists current-turn attachments that must appear in the manifest.

In `src/apis/chat_api.py`, replace this block:

```python
    # 给 agent 的 prompt：正文 + 附件 URL + 工作区路径；多轮历史由 run_agent 通过 task.meta['dialog_history'] 注入
    base_prompt = (req.content or "").strip()
    if req.files:
        normalized = [
            urlunparse(
                urlparse(u)._replace(path=quote(unquote(urlparse(u).path), safe="/"))
            )
            for u in req.files
        ]
        base_prompt += "\n\n[Attached files]\n" + "\n".join(normalized)
    if req.workspace_paths:
        base_prompt += "\n\n[Workspace paths]\n" + "\n".join(req.workspace_paths)
```

with:

```python
    # 给 agent 的 prompt：正文 + 当前会话可用附件清单。多轮历史由 run_agent
    # 通过 task.meta['dialog_history'] 注入；这里补充 files/images/workspace_paths
    # 的显式可引用资源列表，便于 agent 在后续轮次继续使用附件。
    base_prompt = _build_agent_prompt(
        req.content or "",
        events_svc.get_session_events(sid),
    )
```

- [ ] **Step 7: Remove unused URL quoting imports from `chat_api.py`**

In `src/apis/chat_api.py`, replace:

```python
from urllib.parse import quote, unquote, urlparse, urlunparse
```

with no import from `urllib.parse`, unless another remaining function in the file still uses it.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest \
  tests/services/test_attachment_manifest_service.py \
  tests/apis/test_chat_api_available_attachments.py \
  tests/test_chat_image_events.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit the API integration slice**

Run:

```bash
git add src/apis/chat_api.py tests/apis/test_chat_api_available_attachments.py
git commit -m "feat: restore available attachments in chat prompts"
```

Expected: commit succeeds with no commit trailers.

## Task 3: Regression And Compatibility Sweep

**Files:**
- Verify: `src/apis/chat_api.py`
- Verify: `src/services/attachment_manifest_service.py`
- Verify: `src/services/chat_history.py`
- Verify: `src/services/image_input_service.py`
- Verify: `tests/services/test_attachment_manifest_service.py`
- Verify: `tests/apis/test_chat_api_available_attachments.py`
- Verify: `tests/test_chat_stream_direct.py`
- Verify: `tests/test_chat_image_events.py`
- Verify: `tests/matmaster/types/test_image_messages.py`
- Verify: `tests/matmaster/services/test_history_restore_service.py`
- Verify: `matmaster/exps/*.toml`
- Verify: `matmaster/skills/`

- [ ] **Step 1: Run image and history regression tests**

Run:

```bash
uv run pytest \
  tests/test_chat_image_events.py \
  tests/matmaster/types/test_image_messages.py \
  tests/matmaster/services/test_history_restore_service.py \
  -q
```

Expected: all tests pass. This confirms image content parts and image history trimming still behave as before.

- [ ] **Step 2: Run stream direct tests**

Run:

```bash
uv run pytest tests/test_chat_stream_direct.py -q
```

Expected: all tests pass. This confirms the prompt change does not break Redis queue mode, subscribe mode, or existing stream event behavior.

- [ ] **Step 3: Check active prompts no longer reference old attachment sections**

Run:

```bash
rg -n "Attached files|Workspace paths" src matmaster/exps matmaster/skills tests
```

Expected: no output. Historical docs under `docs/superpowers/` may still mention the old tags because they are not runtime prompts.

- [ ] **Step 4: Run import and lint-oriented checks for changed modules**

Run:

```bash
uv run python -m py_compile \
  src/apis/chat_api.py \
  src/services/attachment_manifest_service.py
```

Expected: command exits with status 0 and prints no errors.

- [ ] **Step 5: Inspect generated prompt shape manually with the service helper**

Run:

```bash
uv run python - <<'PY'
from src.services.attachment_manifest_service import append_available_attachments

events = [
    {
        "source": "User",
        "type": "query",
        "content": "old",
        "files": ["https://oss.example.com/chat/data.csv"],
        "images": ["https://oss.example.com/chat/em.png"],
        "workspace_paths": ["/share/a.cif"],
    }
]

print(append_available_attachments("继续分析上一张图", events))
PY
```

Expected output:

```text
继续分析上一张图

[Available attachments]
file_1 data.csv https://oss.example.com/chat/data.csv
image_1 em.png https://oss.example.com/chat/em.png
workspace_1 /share/a.cif
```

- [ ] **Step 6: Commit any verification-only adjustments**

If Step 1 through Step 5 required small test or formatting corrections, commit them:

```bash
git add src/apis/chat_api.py src/services/attachment_manifest_service.py tests/services/test_attachment_manifest_service.py tests/apis/test_chat_api_available_attachments.py tests/test_chat_stream_direct.py
git commit -m "test: cover available attachment prompt context"
```

Expected: commit succeeds only if there are verification corrections to commit. If there are no changes, skip this commit.

## Self-Review

**Spec coverage:** This plan covers the approved lightweight design: restore previous and current attachments into a compact `[Available attachments]` manifest, include `files`, `images`, and `workspace_paths`, keep image vision flow unchanged, and avoid URL downloading or resolver logic.

**Review corrections incorporated:** API-level tests now use the flat event shape returned by `ChatEventsTable.get_session_events()`, while the service unit tests keep one nested-content compatibility case for raw DB/checkpoint-like rows. The chat integration step explicitly reads events only after `prepare_send_message()` has persisted the current turn. The new `tests/apis/` directory follows the existing pytest directory style and does not add an `__init__.py`.

**URL behavior:** The manifest service applies the previous current-turn `quote(unquote(path), safe="/")` policy uniformly to all manifested file/image URLs across turns. Workspace paths are kept as paths and are not URL-encoded.

**Runtime behavior acknowledged:** Historical attachments now enter every prompt through the manifest until the total 30-entry cap is reached. Historical images can appear both as vision inputs and text manifest rows; this is intentional so the agent can refer to them when deciding later tool calls. Expired URLs, MIME/size hints, spawned-agent attachments, and per-kind caps are deferred from this phase by design.

**Placeholder scan:** The plan contains no deferred implementation placeholders. Each code-writing step includes exact code and each verification step includes exact commands and expected results.

**Type consistency:** The plan defines one dataclass, `AttachmentEntry`, with `kind`, `label`, `name`, and `value`; all tests and helper functions use the same names. The API integration exposes `_build_agent_prompt(content, events)` and delegates to `append_available_attachments(prompt, events)` exactly as defined in Task 1.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-available-attachments-context.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
