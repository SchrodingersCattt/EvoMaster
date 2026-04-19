# Image Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class chat image input so uploaded image URLs are validated, persisted in existing JSON content, restored into user messages, and sent to LiteLLM as OpenAI-compatible multimodal content parts.

**Architecture:** Keep SQL unchanged and add `images` as a structured field inside existing User/query JSON content. The API validates current-turn image URLs before enqueue, the worker carries URLs to `AgentRunService`, and the kernel builds `UserMessage(content=base_prompt, images=...)` while history restoration applies a small image retention policy. Plain `files` remain ordinary attachments and continue to be appended to `[Attached files]`.

**Tech Stack:** FastAPI, Pydantic, uv-managed Python 3.13, pytest, httpx, existing MatMaster `Message` / `AgentKernel` / `ChatHistoryConverter` stack.

---

## File Map

- Modify `src/models/chat.py`: add `ChatSendRequest.images`.
- Create `src/services/image_input_service.py`: image URL config, static validation, HEAD / Range probing, duplicate detection, history trimming.
- Modify `src/apis/chat_api.py`: validate current-turn images before `prepare_send_message()` and include validated URLs in the request copy.
- Modify `src/services/stream_service.py`: persist `images`, include them in `ctx.user_msg`, enqueue them in Redis job.
- Modify `src/worker/agent_worker.py`: read image URLs from Redis job and pass them to `AgentRunService.run_agent()`.
- Modify `src/services/events_service.py`: include `images` in User/query JSON content.
- Modify `src/dao/chat_events_table.py`: split `images` back to replay payloads and `get_last_user_query()`.
- Modify `src/services/chat_history.py`: restore user event images into dialog dicts and `UserMessage` models.
- Modify `src/services/history_restore_service.py`: apply history image policy after raw/checkpoint restore.
- Modify `matmaster/types/messages.py`: add `ImageContentPart` and `UserMessage.images`.
- Modify `matmaster/types/message_normalization.py`: allow user content parts while keeping other roles strict.
- Modify `matmaster/config/llm.py`: add `supports_vision` and `vision_detail`.
- Modify `src/services/agent_run_service.py`: resolve vision profile, fail if unsupported, build current-turn image parts.
- Modify `matmaster/core/agent.py`: create the current user message with `spec.meta["current_user_images"]`.
- Modify `matmaster/core/exp.py`: pass current-turn images from `PlaygroundContext.run_meta` to immutable runtime spec metadata.
- Add tests under `tests/` and `tests/matmaster/` matching each slice.

## Task 1: Message Model And Normalization

**Files:**
- Modify: `matmaster/types/messages.py`
- Modify: `matmaster/types/message_normalization.py`
- Test: `tests/matmaster/types/test_image_messages.py`

- [ ] **Step 1: Write failing tests for multimodal user messages**

Create `tests/matmaster/types/test_image_messages.py`:

```python
import pytest

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    normalize_and_validate_openai_messages,
)
from matmaster.types.messages import ImageContentPart, UserMessage


def test_user_message_with_images_renders_openai_content_parts() -> None:
    message = UserMessage(
        content="请分析这张显微图",
        images=[
            ImageContentPart(
                url="https://oss.example.com/chat/sess/image.png",
                mime_type="image/png",
                detail="high",
            )
        ],
    )

    assert message.to_api_dict() == {
        "role": "user",
        "content": [
            {"type": "text", "text": "请分析这张显微图"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://oss.example.com/chat/sess/image.png",
                    "detail": "high",
                },
            },
        ],
    }


def test_user_content_parts_pass_normalization() -> None:
    normalized = normalize_and_validate_openai_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://oss.example.com/a.webp"},
                    },
                ],
            }
        ]
    )

    assert normalized[0]["content"][1]["image_url"]["url"].endswith("a.webp")


def test_assistant_content_parts_are_still_rejected() -> None:
    with pytest.raises(LLMError, match="assistant"):
        normalize_and_validate_openai_messages(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "no"}],
                }
            ]
        )
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --extra dev pytest tests/matmaster/types/test_image_messages.py -q
```

Expected: fail because `ImageContentPart` does not exist or `UserMessage` has no `images`.

- [ ] **Step 3: Implement message parts and validation**

In `matmaster/types/messages.py`, add:

```python
from typing import Any, Literal


class ImageContentPart(BaseModel):
    url: str
    mime_type: str | None = None
    detail: Literal["low", "high", "auto"] | None = None
```

Update `UserMessage`:

```python
class UserMessage(Message):
    """User input message."""

    role: Role = Role.USER
    images: list[ImageContentPart] = Field(default_factory=list)

    def to_api_dict(self) -> dict[str, Any]:
        if not self.images:
            return {"role": self.role.value, "content": self.content}
        parts: list[dict[str, Any]] = []
        if self.content:
            parts.append({"type": "text", "text": self.content})
        for image in self.images:
            image_url: dict[str, Any] = {"url": image.url}
            if image.detail is not None:
                image_url["detail"] = image.detail
            parts.append({"type": "image_url", "image_url": image_url})
        return {"role": self.role.value, "content": parts}
```

In `matmaster/types/message_normalization.py`, keep non-user roles string-only and add helper validation for user content parts:

```python
def _validate_user_content_parts(content: Any, idx: int) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise LLMError(...)
    for part in content:
        if not isinstance(part, dict):
            raise LLMError(...)
        part_type = part.get("type")
        if part_type == "text" and isinstance(part.get("text"), str):
            continue
        if part_type == "image_url" and isinstance(part.get("image_url"), dict):
            url = part["image_url"].get("url")
            if isinstance(url, str) and url:
                continue
        raise LLMError(...)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run --extra dev pytest tests/matmaster/types/test_image_messages.py -q
```

Expected: all tests pass.

## Task 2: Event Persistence And History Replay

**Files:**
- Modify: `src/models/chat.py`
- Modify: `src/services/events_service.py`
- Modify: `src/dao/chat_events_table.py`
- Modify: `src/services/chat_history.py`
- Test: `tests/test_chat_image_events.py`

- [ ] **Step 1: Write failing tests for JSON persistence/replay**

Create `tests/test_chat_image_events.py` with focused fake table tests:

```python
from unittest.mock import MagicMock

from src.services.chat_history import ChatHistoryConverter
from src.services.events_service import ChatEventsService


def test_events_service_persists_images_inside_user_query_content() -> None:
    table = MagicMock()
    sessions = MagicMock()
    service = ChatEventsService(events_table=table, sessions_service=sessions)

    service.add_history_event(
        "sess-1",
        {
            "source": "User",
            "type": "query",
            "content": "看图",
            "files": ["https://oss.example.com/data.csv"],
            "images": ["https://oss.example.com/image.png"],
            "workspace_paths": ["/share/a.cif"],
        },
        user_id="u1",
    )

    content = table.add_event.call_args.args[3]
    assert content == {
        "content": "看图",
        "files": ["https://oss.example.com/data.csv"],
        "images": ["https://oss.example.com/image.png"],
        "workspace_paths": ["/share/a.cif"],
    }


def test_chat_history_restores_user_message_images() -> None:
    messages = ChatHistoryConverter.events_to_messages(
        [
            {
                "source": "User",
                "type": "query",
                "content": "看图",
                "images": ["https://oss.example.com/image.png"],
            }
        ]
    )

    assert messages[0].content == "看图"
    assert messages[0].images[0].url == "https://oss.example.com/image.png"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --extra dev pytest tests/test_chat_image_events.py -q
```

Expected: fail because images are ignored.

- [ ] **Step 3: Implement images in request and event plumbing**

Add `images: list[str] | None = None` to `ChatSendRequest`.

Update `ChatEventsService.add_history_event()` condition to include `payload.get("images")`, and write `content["images"] = list(payload["images"])`.

Update `ChatEventsTable.get_session_events()` User/query split to set:

```python
ev["images"] = content.get("images", [])
```

Update `ChatEventsTable.get_last_user_query()` to return `images` in both dict and string branches.

Update `ChatHistoryConverter.events_to_dialog_messages()` User/query branch:

```python
images = cls._user_images(ev)
out.append(
    UserMessage(
        content=text,
        images=[ImageContentPart(url=url) for url in images],
    ).model_dump()
)
```

Update `events_to_messages()` to pass `images` when creating `UserMessage`.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run --extra dev pytest tests/test_chat_image_events.py tests/test_chat_history_repair.py -q
```

Expected: all tests pass.

## Task 3: Image Validation Service

**Files:**
- Create: `src/services/image_input_service.py`
- Test: `tests/services/test_image_input_service.py`

- [ ] **Step 1: Write failing tests for current-turn validation**

Create `tests/services/test_image_input_service.py`:

```python
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.image_input_service import (
    IMAGE_INPUT_DUPLICATE_ATTACHMENT,
    ImageInputError,
    ImageInputSettings,
    ImageInputService,
)


def _service() -> ImageInputService:
    return ImageInputService(
        ImageInputSettings(
            allowed_hosts=frozenset({"oss.example.com"}),
            allowed_path_prefixes=("/chat/",),
            allow_insecure_hosts=frozenset(),
        )
    )


def test_rejects_duplicate_file_and_image_url() -> None:
    service = _service()

    with pytest.raises(ImageInputError) as exc:
        service.validate_current_images(
            files=["https://oss.example.com/chat/a.png"],
            images=["https://oss.example.com/chat/a.png"],
        )

    assert exc.value.error_code == IMAGE_INPUT_DUPLICATE_ATTACHMENT


def test_head_success_accepts_png() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "image/png", "content-length": "100"},
        request=httpx.Request("HEAD", "https://oss.example.com/chat/a.png"),
    )
    client = MagicMock()
    client.head.return_value = response

    with patch("src.services.image_input_service.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = client
        images = _service().validate_current_images(
            files=[],
            images=["https://oss.example.com/chat/a.png"],
        )

    assert images[0].url == "https://oss.example.com/chat/a.png"
    assert images[0].mime_type == "image/png"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --extra dev pytest tests/services/test_image_input_service.py -q
```

Expected: fail because the service does not exist.

- [ ] **Step 3: Implement service**

Implement constants, `ImageInputSettings`, `ValidatedImageInput`, `ImageInputError`, and `ImageInputService.validate_current_images()`. Use `urllib.parse.urlparse`, `httpx.Client(timeout=httpx.Timeout(3.0, connect=3.0), follow_redirects=True)`, HEAD first, fallback Range GET `bytes=0-4095`, and magic byte checks for PNG/JPEG/WebP. Raise `ImageInputError(error_code, message, http_status=422)`.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run --extra dev pytest tests/services/test_image_input_service.py -q
```

Expected: all tests pass.

## Task 4: LLM Vision Profile Config

**Files:**
- Modify: `matmaster/config/llm.py`
- Test: `tests/matmaster/config/test_llm.py`

- [ ] **Step 1: Write failing config tests**

Add tests:

```python
def test_profile_defaults_to_no_vision() -> None:
    p = LLMProfileConfig(model="plain")
    assert p.supports_vision is False
    assert p.vision_detail == "high"


def test_profile_accepts_vision_detail_none() -> None:
    p = LLMProfileConfig(model="vision", supports_vision=True, vision_detail=None)
    assert p.supports_vision is True
    assert p.vision_detail is None
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --extra dev pytest tests/matmaster/config/test_llm.py -q
```

Expected: fail because fields do not exist.

- [ ] **Step 3: Implement fields**

Add to `LLMProfileConfig`:

```python
supports_vision: bool = False
vision_detail: Literal["low", "high", "auto"] | None = "high"
```

Import `Literal` from `typing`.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run --extra dev pytest tests/matmaster/config/test_llm.py -q
```

Expected: all tests pass.

## Task 5: API, Stream, Worker, And Agent Current-Turn Wiring

**Files:**
- Modify: `src/apis/chat_api.py`
- Modify: `src/services/stream_service.py`
- Modify: `src/worker/agent_worker.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `matmaster/core/exp.py`
- Modify: `matmaster/core/agent.py`
- Test: `tests/test_chat_stream_direct.py`
- Test: `tests/matmaster/core/test_agent.py`

- [ ] **Step 1: Write failing tests for request and job wiring**

Add to `tests/test_chat_stream_direct.py`:

```python
def test_prepare_send_message_persists_images_in_user_message():
    from src.models.chat import ChatSendRequest
    from src.services.stream_service import ChatStreamService

    sessions_service = MagicMock()
    sessions_service.ensure_session.return_value = None
    sessions_service.try_acquire_session_run.return_value = (True, None)
    sessions_service.set_session_last_task.return_value = None
    events_service = MagicMock()
    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
    )

    ctx = service.prepare_send_message(
        "sess-1",
        ChatSendRequest(
            content="看图",
            images=["https://oss.example.com/chat/a.png"],
        ),
        "u1",
    )

    assert ctx is not None
    assert ctx.user_msg["images"] == ["https://oss.example.com/chat/a.png"]
    assert events_service.add_history_event.call_args.args[1]["images"] == [
        "https://oss.example.com/chat/a.png"
    ]
```

Add a kernel-level test that builds a fake provider and verifies the final API user message contains `image_url`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run --extra dev pytest tests/test_chat_stream_direct.py::test_prepare_send_message_persists_images_in_user_message -q
```

Expected: fail because `ctx.user_msg` has no images.

- [ ] **Step 3: Implement wiring**

Update `SendStreamContext` to include `images: list[str]`.

In `prepare_send_message()`, add `user_msg["images"] = list(req.images)` when present and set `ctx.images`.

In `generate_send_stream()`, add `"images": ctx.images` to the Redis job.

In `agent_worker.py`, read `images = list(payload.get("images") or [])` and pass to `run_agent(images=images)`.

In `AgentRunService.run_agent()`, add parameter `images: list[str] | None = None`, resolve selected profile with `llm_config.resolve_route()`, fail early if `images` and `not profile.supports_vision`, then put current-turn image parts into `pg_ctx.run_meta["current_user_images"]`.

In `Exp.run_stream()`, copy `ctx.run_meta["current_user_images"]` into `runtime.spec.meta`.

In `AgentKernel._run_items()`, create:

```python
current_images = [
    ImageContentPart.model_validate(item)
    for item in spec.meta.get("current_user_images", [])
]
UserMessage(content=task, images=current_images)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
uv run --extra dev pytest tests/test_chat_stream_direct.py tests/matmaster/core/test_agent.py -q
```

Expected: all relevant tests pass.

## Task 6: History Image Policy

**Files:**
- Modify: `src/services/image_input_service.py`
- Modify: `src/services/history_restore_service.py`
- Test: `tests/matmaster/services/test_history_restore_service.py`

- [ ] **Step 1: Write failing trimming test**

Add:

```python
def test_restore_trims_history_images_by_image_turns() -> None:
    events_table = FakeEventsTable(
        session_events=[
            _user_event("img 1", images=["https://oss.example.com/chat/1.png"]),
            _user_event("text only"),
            _user_event("img 2", images=["https://oss.example.com/chat/2.png"]),
            _user_event("img 3", images=["https://oss.example.com/chat/3.png"]),
            _user_event("img 4", images=["https://oss.example.com/chat/4.png"]),
        ]
    )
    service = HistoryRestoreService(events_table)

    history = service.restore_history(
        session_id="sess-raw",
        spawn_id=None,
        task_id=None,
    )

    image_counts = [len(getattr(message, "images", [])) for message in history]
    assert image_counts == [0, 0, 1, 1, 1]
```

Update helper `_user_event()` to accept `images`.

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
uv run --extra dev pytest tests/matmaster/services/test_history_restore_service.py::test_restore_trims_history_images_by_image_turns -q
```

Expected: fail because old image turns are not trimmed.

- [ ] **Step 3: Implement trimming**

Add `trim_history_images(messages: list[Message], *, last_k_turns: int = 3, max_images: int = 10) -> list[Message]` in `image_input_service.py`. Count only user messages with non-empty `images`, from newest to oldest. For excluded images, return a copied `UserMessage` with `images=[]` and append text placeholders to `content`.

Call it at the end of `HistoryRestoreService.restore_history()` for both checkpoint and raw paths.

- [ ] **Step 4: Run test and verify GREEN**

Run:

```bash
uv run --extra dev pytest tests/matmaster/services/test_history_restore_service.py -q
```

Expected: all tests pass.

## Task 7: Final Verification

**Files:**
- All modified files.

- [ ] **Step 1: Run focused suite**

Run:

```bash
uv run --extra dev pytest \
  tests/matmaster/types/test_image_messages.py \
  tests/test_chat_image_events.py \
  tests/services/test_image_input_service.py \
  tests/test_chat_history_repair.py \
  tests/test_chat_stream_direct.py \
  tests/matmaster/config/test_llm.py \
  tests/matmaster/services/test_history_restore_service.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Run style check on changed files**

Run:

```bash
uv run --extra dev python -m compileall \
  matmaster/types/messages.py \
  matmaster/types/message_normalization.py \
  matmaster/config/llm.py \
  matmaster/core/agent.py \
  matmaster/core/exp.py \
  src/models/chat.py \
  src/services/image_input_service.py \
  src/services/events_service.py \
  src/services/stream_service.py \
  src/services/agent_run_service.py \
  src/services/history_restore_service.py \
  src/dao/chat_events_table.py \
  src/apis/chat_api.py \
  src/worker/agent_worker.py
```

Expected: compile succeeds.

- [ ] **Step 3: Commit**

Run:

```bash
git add \
  matmaster/types/messages.py \
  matmaster/types/message_normalization.py \
  matmaster/config/llm.py \
  matmaster/core/agent.py \
  matmaster/core/exp.py \
  src/models/chat.py \
  src/services/image_input_service.py \
  src/services/events_service.py \
  src/services/stream_service.py \
  src/services/agent_run_service.py \
  src/services/history_restore_service.py \
  src/dao/chat_events_table.py \
  src/apis/chat_api.py \
  src/worker/agent_worker.py \
  tests/matmaster/types/test_image_messages.py \
  tests/test_chat_image_events.py \
  tests/services/test_image_input_service.py \
  tests/test_chat_stream_direct.py \
  tests/matmaster/config/test_llm.py \
  tests/matmaster/services/test_history_restore_service.py
git commit -m "feat: add chat image input support"
```

Expected: commit succeeds.

## Self-Review

- Spec coverage: API `images`, existing JSON persistence, no SQL migration, current-turn validation, URL duplicate rejection, multimodal message payload, vision config, history trimming, and no-image compatibility are all covered.
- Known out of scope: frontend sibling repository changes are not included because this worktree is limited to `matmaster-evo`; frontend send splitting must be done in `../scimaster-bohr-chat` as a separate explicit edit.
- Placeholder scan: no task relies on TBD behavior; every task has concrete files, commands, and expected results.
