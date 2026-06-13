# 工具结果图片通路（agent 看图能力）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 agent 能通过 Read 工具读取 session 文件系统中的图片并送入模型上下文，且带图历史在体积、token、模型能力三个维度上受四道防线约束。

**Architecture:** ToolResult/ToolMessage/ToolResultEvent 一等公民图片字段（base64 data URI）；Read 工具 magic bytes 判定 + vision 门控；Anthropic 原生 tool_result image block，ChatCompletions / Responses 风格 wire 层 relay；四道防线（工具入口校验 → kernel 请求前在途预算 → compaction 估算与摘要剥图 → 恢复层按模型能力剥图）。规格见 `docs/superpowers/specs/2026-06-10-tool-image-vision-design.md`（rev2，commit c2003dea）。

**Tech Stack:** Python 3.11+、pydantic v2、pytest。无新增第三方依赖（PNG 尺寸用 struct 手解）。

**全局约定：**
- 仓库根目录运行所有命令；用工作区 venv 的 `uv run pytest`。
- 每个 Task 一次 commit；commit message 风格沿用仓库（`feat(scope): ...` / `test(scope): ...`）。
- `ImageContentPart` 是现有类型（`matmaster/types/messages.py:207`），本计划不改它。
- data URI 测试样例统一用：`"data:image/png;base64,aGVsbG8="`。

---

### Task 1: types 层三个 images 字段

**Files:**
- Modify: `matmaster/tools/tool_result.py`
- Modify: `matmaster/types/messages.py`（ToolMessage，约 238 行）
- Modify: `matmaster/types/events.py`（ToolResultEvent，约 73 行）
- Test: `tests/matmaster/tools/test_tool_result.py`（追加）
- Test: `tests/matmaster/types/test_image_messages.py`（追加）
- Test: `tests/matmaster/types/test_events.py`（追加）

- [ ] **Step 1: 写三个失败测试**

在 `tests/matmaster/tools/test_tool_result.py` 追加（import 按文件现有风格补全）：

```python
def test_tool_result_images_roundtrip():
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.messages import ImageContentPart

    tr = ToolResult(
        content="Read image: a.png",
        images=[ImageContentPart(url="data:image/png;base64,aGVsbG8=", mime_type="image/png")],
    )
    restored = ToolResult.model_validate(tr.model_dump(mode="json"))
    assert restored.images[0].url == "data:image/png;base64,aGVsbG8="
    assert restored.images[0].mime_type == "image/png"
    assert ToolResult(content="no images").images == []
```

在 `tests/matmaster/types/test_image_messages.py` 追加：

```python
def test_tool_message_images_roundtrip():
    from matmaster.types.messages import ImageContentPart, ToolMessage

    msg = ToolMessage(
        tool_call_id="tc1",
        tool_name="Read",
        content="Read image: a.png",
        images=[ImageContentPart(url="data:image/png;base64,aGVsbG8=", mime_type="image/png")],
    )
    restored = ToolMessage.model_validate(msg.model_dump(mode="json"))
    assert restored.images[0].url == msg.images[0].url
    assert ToolMessage(tool_call_id="tc2", tool_name="Read", content="x").images == []
```

在 `tests/matmaster/types/test_events.py` 追加：

```python
def test_tool_result_event_images_roundtrip():
    from matmaster.types.events import ToolResultEvent
    from matmaster.types.messages import ImageContentPart

    event = ToolResultEvent(
        source="agent",
        call_id="tc1",
        tool_name="Read",
        result="Read image: a.png",
        images=[ImageContentPart(url="data:image/png;base64,aGVsbG8=", mime_type="image/png")],
    )
    dumped = event.model_dump(mode="json")
    assert dumped["images"][0]["url"] == "data:image/png;base64,aGVsbG8="
    restored = ToolResultEvent.model_validate(dumped)
    assert restored.images[0].mime_type == "image/png"
```

注：若 `ToolResultEvent` 构造需要其他必填字段（看 `EventBase` 定义），按现有测试文件中的构造方式补齐。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/test_tool_result.py::test_tool_result_images_roundtrip tests/matmaster/types/test_image_messages.py::test_tool_message_images_roundtrip tests/matmaster/types/test_events.py::test_tool_result_event_images_roundtrip -v`
Expected: 3 个 FAIL，报 `images` 字段不存在（pydantic ValidationError 或 extra="forbid" 拒绝）。

- [ ] **Step 3: 加字段**

`matmaster/tools/tool_result.py`——`ToolResult` 类内、`meta` 字段之后加一行，并补 import：

```python
from matmaster.types.messages import ImageContentPart
```

```python
    images: list[ImageContentPart] = Field(default_factory=list)
```

`matmaster/types/messages.py`——`ToolMessage` 类（约 238 行）加：

```python
class ToolMessage(Message):
    """Tool execution result message."""

    role: Role = Role.TOOL
    tool_call_id: str
    tool_name: str
    images: list[ImageContentPart] = Field(default_factory=list)
```

`matmaster/types/events.py`——`ToolResultEvent` 的 `payload` 字段之后加，并补 import：

```python
from matmaster.types.messages import ImageContentPart
```

```python
    images: list[ImageContentPart] = Field(default_factory=list)
```

（依赖方向安全：`tool_result.py` 与 `events.py` 均可 import `messages.py`，反向不存在。）

- [ ] **Step 4: 跑测试确认通过 + 全量类型测试回归**

Run: `uv run pytest tests/matmaster/tools/test_tool_result.py tests/matmaster/types/ -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/tool_result.py matmaster/types/messages.py matmaster/types/events.py tests/matmaster/tools/test_tool_result.py tests/matmaster/types/test_image_messages.py tests/matmaster/types/test_events.py
git commit -m "feat(types): add images field to ToolResult/ToolMessage/ToolResultEvent"
```

---

### Task 2: dispatch 透传 + 事件公共 content 携带 images

**Files:**
- Modify: `matmaster/core/agent_tool_dispatch.py:109-131`
- Modify: `matmaster/integration/event_payloads.py`（`_public_content_for_event` 的 tool_result 分支，约 248-262 行）
- Test: `tests/matmaster/core/test_agent_tool_dispatch.py`（追加）
- Test: `tests/matmaster/integration/` 下与 event_payloads 对应的测试文件（`grep -rln "_public_content_for_event\|public_content" tests/` 定位；无则新建 `tests/matmaster/integration/test_event_payloads_images.py`）

- [ ] **Step 1: 写失败测试（event_payloads 纯函数）**

新建或追加：

```python
def test_tool_result_public_content_carries_images():
    from matmaster.integration.event_payloads import _public_content_for_event

    payload = {
        "call_id": "tc1",
        "tool_name": "Read",
        "result": "Read image: a.png",
        "status": "success",
        "payload": {},
        "images": [{"url": "data:image/png;base64,aGVsbG8=", "mime_type": "image/png", "detail": None}],
    }
    out = _public_content_for_event("tool_result", payload)
    assert out["images"][0]["url"] == "data:image/png;base64,aGVsbG8="


def test_tool_result_public_content_no_images_key_when_empty():
    from matmaster.integration.event_payloads import _public_content_for_event

    payload = {"call_id": "tc1", "tool_name": "Read", "result": "ok", "status": "success", "payload": {}}
    out = _public_content_for_event("tool_result", payload)
    assert "images" not in out
```

- [ ] **Step 2: 写失败测试（dispatch 透传）**

在 `tests/matmaster/core/test_agent_tool_dispatch.py` 追加（该文件已有 `StaticRunner` stub 与 `_KernelState` import，直接复用）：

```python
@pytest.mark.asyncio
async def test_dispatch_propagates_tool_result_images() -> None:
    from matmaster.types.messages import ImageContentPart

    image = ImageContentPart(
        url="data:image/png;base64,aGVsbG8=", mime_type="image/png"
    )
    state = _KernelState(messages=[SystemMessage(content="sys")], turn=1)
    tool_call = ToolCallData(id="tc1", name="Read", arguments={"file_path": "/a.png"})
    runner = StaticRunner(
        [ToolResult(status="success", content="Read image: /a.png", images=[image])]
    )

    items = [
        item
        async for item in dispatch_tool_calls(
            tool_calls=[tool_call],
            tool_runner=runner,
            max_turns=10,
            state=state,
            cancel_token=None,
        )
    ]

    tool_msg = state.messages[-1]
    assert tool_msg.images == [image]
    event = items[0].event
    assert isinstance(event, ToolResultEvent)
    assert event.images == [image]
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/core/test_agent_tool_dispatch.py tests/matmaster/integration/ -q -k "images"`
Expected: FAIL（ToolMessage.images 为空 / content dict 无 images 键）。

- [ ] **Step 4: 实现**

`matmaster/core/agent_tool_dispatch.py`，`ToolMessage` 构造（109-115 行）加一行：

```python
        state.messages.append(
            ToolMessage(
                tool_call_id=tc.id,
                tool_name=tc.name,
                content=tool_result.content,
                images=tool_result.images,
            )
        )
```

同函数下方 `ToolResultEvent` 构造（119-131 行）加一行 `images=tool_result.images,`（放在 `payload=tool_result.payload,` 之后）。

`matmaster/integration/event_payloads.py`，tool_result 分支在 `'info': ...` 行之后追加：

```python
        images = payload.get('images')
        if images:
            out['images'] = images
```

（`persistence_handler.py:65` 先 `event.model_dump(mode='json')` 再过此映射，DB 与 SSE 同源，一处改动两路生效。）

- [ ] **Step 5: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/matmaster/core/test_agent_tool_dispatch.py tests/matmaster/integration/ -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/agent_tool_dispatch.py matmaster/integration/event_payloads.py tests/
git commit -m "feat(core): propagate tool result images into ToolMessage and persisted events"
```

---

### Task 2b: ToolRunner 截断路径保留 images

**Files:**
- Modify: `matmaster/core/tool_runner.py`（`FullToolRunner._truncate_result`）
- Test: `tests/matmaster/core/test_full_tool_runner_normalize.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/core/test_full_tool_runner_normalize.py` 的 truncation 测试附近追加：

```python
def test_truncate_result_preserves_images(self, tmp_path: Path) -> None:
    from matmaster.types.messages import ImageContentPart

    image = ImageContentPart(
        url="data:image/png;base64,aGVsbG8=",
        mime_type="image/png",
    )
    topology_with_tmp = RuntimeTopology(
        session_kind="local",
        control_root=str(tmp_path),
        workspace_root="/tmp/ws",
        active_planes=frozenset(ToolPlane),
    )
    runner = _make_runner(
        ToolCatalog(ToolRegistry()),
        topology=topology_with_tmp,
    )

    truncated = runner._truncate_result(
        ToolResult(content="A" * 20_000, images=[image]),
        max_chars=12_000,
        tool_call_id="call_img",
    )

    assert truncated.images == [image]
    assert truncated.payload == {}
    assert truncated.meta["truncated"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_normalize.py -q -k truncate_result_preserves_images`
Expected: FAIL（`ToolResult` 增加 images 后，`_truncate_result` 重建对象时未透传 images）。

- [ ] **Step 3: 实现**

`matmaster/core/tool_runner.py`，`_truncate_result` 的返回值增加 `images=tr.images,`：

```python
        return ToolResult(
            status=tr.status,
            content=truncated_content,
            payload=tr.payload,
            meta=new_meta,
            images=tr.images,
        )
```

说明：Task 1 新增 `ToolResult.images` 后，截断仍只作用于文本 `content`；图片字段是结构化模型上下文，不属于长文本截断对象。

- [ ] **Step 4: 跑测试确认通过 + ToolRunner 回归**

Run: `uv run pytest tests/matmaster/core/test_full_tool_runner_normalize.py tests/matmaster/core/test_tool_runner_error_wrap.py -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/tool_runner.py tests/matmaster/core/test_full_tool_runner_normalize.py
git commit -m "feat(core): preserve tool result images during text truncation"
```

---

### Task 3: 图片判定纯函数模块 image_resolution

**Files:**
- Create: `matmaster/tools/filesystem_semantics/image_resolution.py`
- Test: `tests/matmaster/tools/` 下新建 `test_image_resolution.py`（与 `filesystem_semantics` 现有测试位置对齐：先 `ls tests/matmaster/tools/` 看有无 `filesystem_semantics/` 子目录，有则放入）

- [ ] **Step 1: 写失败测试**

```python
"""Tests for matmaster/tools/filesystem_semantics/image_resolution.py."""

import struct
import zlib

import pytest

from matmaster.tools.filesystem_semantics.image_resolution import (
    MAX_IMAGE_BYTES,
    ImagePayload,
    ImageValidationError,
    build_image_payload,
    png_dimensions,
    sniff_image_media_type,
)


def _png_chunk(typ: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + typ
        + data
        + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x00\x00\x00" * width
    idat = zlib.compress(row * height)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def make_png_header_only(width: int, height: int) -> bytes:
    """合成仅含签名+IHDR 的字节，用于超大尺寸校验（无需真实像素数据）。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)


def test_sniff_media_types():
    assert sniff_image_media_type(make_png(2, 2)) == "image/png"
    assert sniff_image_media_type(b"\xff\xd8\xff\xe0" + b"\x00" * 16) == "image/jpeg"
    assert sniff_image_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_image_media_type(b"GIF89a" + b"\x00" * 10) is None  # GIF 不在集合
    assert sniff_image_media_type(b"plain text") is None


def test_png_dimensions():
    assert png_dimensions(make_png(3, 5)) == (3, 5)
    assert png_dimensions(b"\x89PNG\r\n\x1a\n short") is None


def test_build_payload_success():
    raw = make_png(2, 2)
    payload = build_image_payload(raw)
    assert isinstance(payload, ImagePayload)
    assert payload.media_type == "image/png"
    assert payload.data_uri.startswith("data:image/png;base64,")
    assert payload.raw_size == len(raw)
    assert (payload.width, payload.height) == (2, 2)


def test_build_payload_jpeg_has_no_dimensions():
    payload = build_image_payload(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    assert payload.media_type == "image/jpeg"
    assert payload.width is None and payload.height is None


def test_build_payload_rejects_oversize_bytes():
    raw = b"\xff\xd8\xff" + b"\x00" * (MAX_IMAGE_BYTES + 1)
    with pytest.raises(ImageValidationError, match="3 MiB"):
        build_image_payload(raw)


def test_build_payload_rejects_oversize_png_dimensions():
    with pytest.raises(ImageValidationError, match="8000px"):
        build_image_payload(make_png_header_only(9000, 100))


def test_build_payload_rejects_non_image():
    with pytest.raises(ValueError):
        build_image_payload(b"plain text")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/filesystem_semantics/test_image_resolution.py -v`
Expected: FAIL，`ModuleNotFoundError: ... image_resolution`。

- [ ] **Step 3: 实现模块**

新建 `matmaster/tools/filesystem_semantics/image_resolution.py`：

```python
"""Image payload resolution for ReadTool: magic sniffing, PNG dimension check, data URI.

Format set is the Anthropic ∩ qwen-VL intersection (PNG/JPEG/WEBP, no GIF);
limits follow the design spec (3 MiB raw bytes, 8000px PNG edge).
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass

MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_PNG_EDGE_PX = 8000

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class ImageValidationError(ValueError):
    """Image fails size/dimension constraints; message is model-facing (no prefix)."""


@dataclass(frozen=True)
class ImagePayload:
    media_type: str
    data_uri: str
    raw_size: int
    width: int | None
    height: int | None


def sniff_image_media_type(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def png_dimensions(raw: bytes) -> tuple[int, int] | None:
    # PNG layout: signature(8) + IHDR length(4) + b"IHDR"(4) + width(4) + height(4)
    if len(raw) < 24 or raw[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", raw[16:24])
    return width, height


def build_image_payload(raw: bytes) -> ImagePayload:
    media_type = sniff_image_media_type(raw)
    if media_type is None:
        raise ValueError("not a supported image; sniff before calling")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageValidationError(
            f"image is {len(raw) / (1024 * 1024):.1f} MiB, exceeds the 3 MiB limit; "
            "compress it first (e.g. via Bash) and re-Read"
        )
    width: int | None = None
    height: int | None = None
    if media_type == "image/png":
        dims = png_dimensions(raw)
        if dims is None:
            raise ImageValidationError("corrupt PNG header (IHDR not found)")
        width, height = dims
        if max(width, height) > MAX_PNG_EDGE_PX:
            raise ImageValidationError(
                f"image is {width}x{height}px, exceeds the {MAX_PNG_EDGE_PX}px edge limit; "
                "downscale it first (e.g. via Bash) and re-Read"
            )
    encoded = base64.standard_b64encode(raw).decode("ascii")
    return ImagePayload(
        media_type=media_type,
        data_uri=f"data:{media_type};base64,{encoded}",
        raw_size=len(raw),
        width=width,
        height=height,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/tools/filesystem_semantics/test_image_resolution.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/filesystem_semantics/image_resolution.py tests/matmaster/tools/filesystem_semantics/test_image_resolution.py
git commit -m "feat(tools): image_resolution module — magic sniff, PNG dims, data URI payload"
```

---

### Task 4: ReadTool 图片分支

**Files:**
- Modify: `matmaster/tools/builtin/read_tool.py`
- Test: `tests/matmaster/tools/builtin/test_read_tool.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/tools/builtin/test_read_tool.py` 追加。**复用该文件现有的 fake session 构造方式**（若无现成可复用的，用下面的最小 FakeSession）。测试用 Task 3 的 `make_png` helper（从 `tests/matmaster/tools/filesystem_semantics/test_image_resolution.py` import，或本文件内复制同款 helper——以文件内既有约定为准，无约定则 import）：

```python
import dataclasses

from matmaster.tools.builtin.read_tool import ReadTool
from tests.matmaster.tools.filesystem_semantics.test_image_resolution import make_png, make_png_header_only


@dataclasses.dataclass
class _Stat:
    size: int
    mtime: float = 0.0


class FakeImageSession:
    def __init__(self, data: bytes, size: int | None = None):
        self._data = data
        self._size = len(data) if size is None else size

    def is_file(self, path: str) -> bool:
        return True

    def download(self, path: str, timeout=None) -> bytes:
        return self._data

    def stat_file(self, path: str) -> _Stat:
        return _Stat(size=self._size)


def test_read_image_returns_image_payload():
    raw = make_png(2, 2)
    tool = ReadTool(session=FakeImageSession(raw), vision_enabled=True, vision_detail="high")
    result = tool._execute({"file_path": "/ws/plot.png"})
    assert result.status == "success"
    assert "Read image: /ws/plot.png" in result.content
    assert len(result.images) == 1
    assert result.images[0].url.startswith("data:image/png;base64,")
    assert result.images[0].mime_type == "image/png"
    assert result.images[0].detail == "high"
    assert "mark_read" not in result.meta
    assert "snapshot_seed" not in result.payload


def test_read_image_vision_disabled_errors():
    tool = ReadTool(session=FakeImageSession(make_png(2, 2)))  # vision_enabled 默认 False
    result = tool._execute({"file_path": "/ws/plot.png"})
    assert result.status == "error"
    assert "does not support image input" in result.content


def test_read_image_stat_precheck_skips_download():
    class ExplodingSession(FakeImageSession):
        def download(self, path, timeout=None):
            raise AssertionError("must not download oversize image")

    tool = ReadTool(
        session=ExplodingSession(b"", size=4 * 1024 * 1024),
        vision_enabled=True,
    )
    result = tool._execute({"file_path": "/ws/huge.png"})
    assert result.status == "error"
    assert "3 MiB" in result.content


def test_read_image_oversize_png_dimensions_error():
    tool = ReadTool(
        session=FakeImageSession(make_png_header_only(9000, 100)),
        vision_enabled=True,
    )
    result = tool._execute({"file_path": "/ws/wide.png"})
    assert result.status == "error"
    assert "8000px" in result.content


def test_read_image_magic_wins_over_extension():
    """扩展名是 .dat 但内容是 PNG：magic 主判定，走图片分支。"""
    tool = ReadTool(session=FakeImageSession(make_png(2, 2)), vision_enabled=True)
    result = tool._execute({"file_path": "/ws/data.dat"})
    assert result.status == "success"
    assert result.images


def test_read_gif_falls_through_to_text_path():
    """GIF 不在集合：走文本解码路径（对二进制报解码错误，而非图片分支）。"""
    gif = b"GIF89a" + b"\x00\xff" * 64
    tool = ReadTool(session=FakeImageSession(gif), vision_enabled=True)
    result = tool._execute({"file_path": "/ws/anim.gif"})
    assert result.images == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_read_tool.py -q -k "image or gif"`
Expected: FAIL（`__init__` 不接受 vision_enabled / 图片走文本路径报解码错误）。

- [ ] **Step 3: 实现**

`matmaster/tools/builtin/read_tool.py`：

(a) 文件头部补 import：

```python
from typing import Any, ClassVar, Literal

from matmaster.tools.filesystem_semantics.image_resolution import (
    IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    ImageValidationError,
    build_image_payload,
    sniff_image_media_type,
)
from matmaster.types.messages import ImageContentPart
```

(b) `ReadTool` 类内加构造函数（类目前无自定义 `__init__`）：

```python
    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any = None,
        path_access_roots: Any = (),
        vision_enabled: bool = False,
        vision_detail: Literal["low", "high", "auto"] | None = None,
    ) -> None:
        super().__init__(
            session=session, workdir=workdir, path_access_roots=path_access_roots
        )
        self._vision_enabled = vision_enabled
        self._vision_detail = vision_detail
```

(c) `_execute_internal` 中，`if not session.is_file(...)` 块之后、`raw = session.download(file_path)` 之前插入 stat 预检；download 之后插入 magic 分流：

```python
        suffix = posixpath.splitext(file_path)[1].lower()
        if suffix in IMAGE_EXTENSIONS:
            pre_size = session.stat_file(file_path).size
            if pre_size > MAX_IMAGE_BYTES:
                return ToolResult(
                    status="error",
                    content=(
                        f"Error: image file is {pre_size / (1024 * 1024):.1f} MiB, "
                        "exceeds the 3 MiB limit; compress it first "
                        "(e.g. via Bash) and re-Read"
                    ),
                )

        raw = session.download(file_path)
        if sniff_image_media_type(raw) is not None:
            return self._image_read(file_path, raw)
        file_stat = session.stat_file(file_path)
```

（原 `raw = session.download(file_path)` 与 `file_stat = session.stat_file(file_path)` 两行被上述片段替代；后续文本路径逻辑不动。）

(d) 类内新增方法：

```python
    def _image_read(self, file_path: str, raw: bytes) -> ToolResult:
        if not self._vision_enabled:
            return ToolResult(
                status="error",
                content=(
                    "Error: current model profile does not support image input; "
                    f"cannot view {file_path}"
                ),
            )
        try:
            payload = build_image_payload(raw)
        except ImageValidationError as e:
            return ToolResult(status="error", content=f"Error: {e}")
        dims = (
            f", {payload.width}x{payload.height}" if payload.width is not None else ""
        )
        return ToolResult(
            content=(
                f"Read image: {file_path} "
                f"({payload.media_type}{dims}, {payload.raw_size / 1024:.0f} KB)"
            ),
            images=[
                ImageContentPart(
                    url=payload.data_uri,
                    mime_type=payload.media_type,
                    detail=self._vision_detail,
                )
            ],
        )
```

- [ ] **Step 4: 跑测试确认通过 + ReadTool 全量回归**

Run: `uv run pytest tests/matmaster/tools/builtin/test_read_tool.py -q`
Expected: 全 PASS（含原有文本路径测试）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/read_tool.py tests/matmaster/tools/builtin/test_read_tool.py
git commit -m "feat(tools): ReadTool image branch — magic sniff, vision gate, size/dimension caps"
```

---

### Task 5: vision 能力注入链（bundle → request → exp → ReadTool；wiring 备用字段）

**Files:**
- Modify: `matmaster/providers/llm_factory.py`（`LLMProviderBundle` 约 64-74 行；两处构造约 226-234、271-279 行）
- Modify: `matmaster/core/run_context.py`（`AgentRunRequest`，llm_model_route 字段附近，约 55 行）
- Modify: `src/services/agent_run_service.py`（`AgentRunRequest(...)` 构造，约 524-531 行）
- Modify: `matmaster/core/exp.py`（`_init_builtin_tools` 内 ReadTool 构造，约 709 行）

- [ ] **Step 1: bundle 加字段**

`matmaster/providers/llm_factory.py`，`LLMProviderBundle` 追加两个字段（放在 `context_limit_source` 之后）：

```python
    supports_vision: bool = False
    vision_detail: Literal["low", "high", "auto"] | None = None
```

（文件已 `from typing import Literal`；若无则补。）

`build_provider_bundle` 的 return（约 226 行）加：

```python
        supports_vision=resolved.profile.supports_vision,
        vision_detail=resolved.profile.vision_detail,
```

`build_byok_provider_bundle` 的 return（约 271 行）加：

```python
        supports_vision=profile.supports_vision,
        vision_detail=profile.vision_detail,
```

（BYOK 的 `LLMProfileConfig` 用默认值构造，`supports_vision` 默认 False——BYOK 无视觉元数据，保守关闭。）

- [ ] **Step 2: AgentRunRequest 加字段**

`matmaster/core/run_context.py`，`AgentRunRequest` 的 `llm_model_route` 字段之后加：

```python
    supports_vision: bool = False
    vision_detail: Literal["low", "high", "auto"] | None = None
```

（补 `from typing import Literal`，若文件没有。）

- [ ] **Step 3: 服务层构造传参**

`src/services/agent_run_service.py`，`AgentRunRequest(` 构造内（约 528 行 `llm_model_profile=llm_bundle.model_profile,` 附近）加：

```python
                    supports_vision=llm_bundle.supports_vision,
                    vision_detail=llm_bundle.vision_detail,
```

- [ ] **Step 4: exp 注入 ReadTool**

`matmaster/core/exp.py` `_init_builtin_tools` 中 ReadTool 构造（约 709 行）改为：

```python
                ReadTool(
                    session=env.session,
                    workdir=exec_wd,
                    vision_enabled=ctx.request.supports_vision,
                    vision_detail=ctx.request.vision_detail,
                ),
```

- [ ] **Step 5: 回归（providers + exp 相关测试）**

Run: `uv run pytest tests/matmaster/providers tests/matmaster/core -q`
Expected: 全 PASS（纯增量字段，现有构造不受影响）。若有测试以位置参数构造 `LLMProviderBundle` 而失败，按新字段补默认值修复该测试。

- [ ] **Step 6: Commit**

```bash
git add matmaster/providers/llm_factory.py matmaster/core/run_context.py src/services/agent_run_service.py matmaster/core/exp.py
git commit -m "feat(providers): thread supports_vision/vision_detail from profile to ReadTool"
```

---

### Task 6: Anthropic transport — tool_result image blocks

**Files:**
- Modify: `matmaster/providers/transports/anthropic_messages.py`（`_tool_result_block`，约 221-226 行）
- Test: `tests/matmaster/providers/test_anthropic_messages_convert.py`（追加）

- [ ] **Step 1: 写失败测试**

追加（消息序列构造方式与文件内既有 convert_messages 测试同构）：

```python
def test_tool_result_with_images_becomes_block_array():
    from matmaster.providers.transports.anthropic_messages import (
        AnthropicMessagesTransport,
    )
    from matmaster.types.messages import (
        AssistantMessage,
        ImageContentPart,
        ToolCallData,
        ToolMessage,
        UserMessage,
    )

    transport = AnthropicMessagesTransport(model="m", api_key="k")
    messages = [
        UserMessage(content="看一下图"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="tc1", name="Read", arguments={"file_path": "/a.png"})],
        ),
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png (image/png, 1 KB)",
            images=[ImageContentPart(url="data:image/png;base64,aGVsbG8=", mime_type="image/png")],
        ),
    ]
    wire = transport.convert_messages(messages)
    result_block = wire[-1]["content"][0]
    assert result_block["type"] == "tool_result"
    blocks = result_block["content"]
    assert blocks[0] == {"type": "text", "text": "Read image: /a.png (image/png, 1 KB)"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "aGVsbG8=",
    }


def test_tool_result_without_images_stays_string():
    from matmaster.providers.transports.anthropic_messages import _tool_result_block
    from matmaster.types.messages import ToolMessage

    block = _tool_result_block(
        ToolMessage(tool_call_id="tc1", tool_name="Read", content="plain")
    )
    assert block["content"] == "plain"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/providers/test_anthropic_messages_convert.py -q -k images`
Expected: FAIL（content 仍是字符串）。

- [ ] **Step 3: 实现**

`_tool_result_block` 改为：

```python
def _tool_result_block(message: ToolMessage) -> dict[str, Any]:
    if message.images:
        blocks: list[dict[str, Any]] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        blocks.extend(_image_block(image) for image in message.images)
        return {
            "type": "tool_result",
            "tool_use_id": message.tool_call_id,
            "content": blocks,
        }
    return {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": message.content or "",
    }
```

- [ ] **Step 4: 跑测试确认通过 + transport 回归**

Run: `uv run pytest tests/matmaster/providers/test_anthropic_messages_convert.py tests/matmaster/providers/test_anthropic_messages_prompt_cache.py -q`
Expected: 全 PASS（含 prompt cache 测试——`_mark_content_block`/`_message_text_size` 对 list content 的既有处理是回归重点）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/providers/transports/anthropic_messages.py tests/matmaster/providers/test_anthropic_messages_convert.py
git commit -m "feat(providers): anthropic tool_result serializes images as content blocks"
```

---

### Task 7: ChatCompletions transport — relay 注入

**Files:**
- Modify: `matmaster/providers/transports/chat_completions.py`（`convert_messages`，约 388-391 行；新增 `_relay_parts_for` 方法）
- Test: `tests/matmaster/providers/test_chat_completions_convert_messages.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def _mk_transport():
    from matmaster.providers.transports.chat_completions import ChatCompletionsTransport

    return ChatCompletionsTransport(model="m", api_key="k")


def _image(url="data:image/png;base64,aGVsbG8="):
    from matmaster.types.messages import ImageContentPart

    return ImageContentPart(url=url, mime_type="image/png", detail="high")


def _tool_turn(images_on=("tc1",)):
    from matmaster.types.messages import (
        AssistantMessage,
        ToolCallData,
        ToolMessage,
        UserMessage,
    )

    return [
        UserMessage(content="看图"),
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCallData(id="tc1", name="Read", arguments={}),
                ToolCallData(id="tc2", name="Read", arguments={}),
            ],
        ),
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png",
            images=[_image()] if "tc1" in images_on else [],
        ),
        ToolMessage(
            tool_call_id="tc2",
            tool_name="Read",
            content="plain text result",
            images=[_image()] if "tc2" in images_on else [],
        ),
    ]


def test_relay_inserted_after_tool_group():
    wire = _mk_transport().convert_messages(_tool_turn(images_on=("tc1",)))
    roles = [m["role"] for m in wire]
    assert roles == ["user", "assistant", "tool", "tool", "user"]
    relay = wire[-1]["content"]
    assert relay[0] == {"type": "text", "text": "[Images from Read (tool_call tc1)]"}
    assert relay[1]["type"] == "image_url"
    assert relay[1]["image_url"] == {
        "url": "data:image/png;base64,aGVsbG8=",
        "detail": "high",
    }


def test_relay_merges_into_following_user_message():
    from matmaster.types.messages import UserMessage

    messages = _tool_turn(images_on=("tc2",)) + [UserMessage(content="继续")]
    wire = _mk_transport().convert_messages(messages)
    roles = [m["role"] for m in wire]
    assert roles == ["user", "assistant", "tool", "tool", "user"]  # 无独立 relay 条目
    merged = wire[-1]["content"]
    assert merged[0]["text"] == "[Images from Read (tool_call tc2)]"
    assert merged[1]["type"] == "image_url"
    assert merged[-1] == {"type": "text", "text": "继续"}


def test_no_images_keeps_wire_unchanged():
    wire = _mk_transport().convert_messages(_tool_turn(images_on=()))
    assert [m["role"] for m in wire] == ["user", "assistant", "tool", "tool"]
    assert all("image_url" not in str(m.get("content")) for m in wire)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/providers/test_chat_completions_convert_messages.py -q -k relay`
Expected: FAIL（无 relay 条目）。

- [ ] **Step 3: 实现**

`chat_completions.py` 中 `convert_messages` 替换为（并新增 helper；`UserMessage` 已在 import 列表）：

```python
    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """canonical list[Message] -> OpenAI-compatible wire dicts。

        tool 协议消息不能携带图片：带图 ToolMessage 的图片在该连续 tool 组
        之后以 user 消息 relay 下发；若紧随真实 UserMessage 则并入其 parts 头部。
        """
        validate_tool_turn_sequence(messages)
        out: list[dict[str, Any]] = []
        pending_relay: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, ToolMessage):
                out.append(self._message_to_wire(message))
                pending_relay.extend(self._relay_parts_for(message))
                continue
            if pending_relay and isinstance(message, UserMessage):
                wire = _user_message_to_dict(message)
                content = wire["content"]
                if isinstance(content, str):
                    content_parts: list[dict[str, Any]] = (
                        [{"type": "text", "text": content}] if content else []
                    )
                else:
                    content_parts = content
                wire["content"] = pending_relay + content_parts
                out.append(wire)
                pending_relay = []
                continue
            if pending_relay:
                out.append({"role": "user", "content": pending_relay})
                pending_relay = []
            out.append(self._message_to_wire(message))
        if pending_relay:
            out.append({"role": "user", "content": pending_relay})
        return out

    @staticmethod
    def _relay_parts_for(message: ToolMessage) -> list[dict[str, Any]]:
        if not message.images:
            return []
        parts: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"[Images from {message.tool_name} "
                    f"(tool_call {message.tool_call_id})]"
                ),
            }
        ]
        for image in message.images:
            image_url: dict[str, Any] = {"url": image.url}
            if image.detail is not None:
                image_url["detail"] = image.detail
            parts.append({"type": "image_url", "image_url": image_url})
        return parts
```

- [ ] **Step 4: 跑测试确认通过 + vendor transport 回归**

Run: `uv run pytest tests/matmaster/providers/test_chat_completions_convert_messages.py tests/matmaster/providers/test_chat_completions_vendor_transports.py -q`
Expected: 全 PASS（qwen/deepseek 子类继承基类 convert_messages，回归确认 reasoning replay 不受影响）。

- [ ] **Step 5: Commit**

```bash
git add matmaster/providers/transports/chat_completions.py tests/matmaster/providers/test_chat_completions_convert_messages.py
git commit -m "feat(providers): chat_completions relays tool images via user message parts"
```

---

### Task 7b: Responses transport — relay 注入

**Files:**
- Modify: `matmaster/providers/transports/responses.py`（`convert_messages`；新增 `_relay_content_for` helper）
- Test: `tests/matmaster/providers/test_responses_convert.py`（追加）

背景：`config/llm_config.yaml` 中 `matmaster/gpt-5.5` 使用 `responses` transport 且 `supports_vision: true`。Task 5 会允许 ReadTool 为该 profile 产出图片；如果 Responses transport 不处理 `ToolMessage.images`，会出现工具成功读图但模型实际没收到图的半成功状态。

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/providers/test_responses_convert.py` 追加：

```python
def _tool_turn_with_image():
    return [
        UserMessage(content="看图"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="call_1", name="Read", arguments={})],
        ),
        ToolMessage(
            tool_call_id="call_1",
            tool_name="Read",
            content="Read image: /a.png",
            images=[
                ImageContentPart(
                    url="data:image/png;base64,aGVsbG8=",
                    mime_type="image/png",
                    detail="high",
                )
            ],
        ),
    ]
```

```python
def test_tool_images_are_relayed_as_user_input_item() -> None:
    wire = _provider().convert_messages(_tool_turn_with_image())
    assert wire[-2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Read image: /a.png",
    }
    assert wire[-1] == {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": "[Images from Read (tool_call call_1)]",
            },
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,aGVsbG8=",
                "detail": "high",
            },
        ],
    }


def test_tool_image_relay_merges_into_following_user_item() -> None:
    wire = _provider().convert_messages(
        _tool_turn_with_image() + [UserMessage(content="继续")]
    )
    assert wire[-1] == {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": "[Images from Read (tool_call call_1)]",
            },
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,aGVsbG8=",
                "detail": "high",
            },
            {"type": "input_text", "text": "继续"},
        ],
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/providers/test_responses_convert.py -q -k "tool_images or relay_merges"`
Expected: FAIL（当前 `_function_call_output_item` 只保留文本 output，`ToolMessage.images` 没有任何 wire 表达）。

- [ ] **Step 3: 实现**

`matmaster/providers/transports/responses.py`，新增 helper：

```python
def _relay_content_for(message: ToolMessage) -> list[dict[str, Any]]:
    if not message.images:
        return []
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                f"[Images from {message.tool_name} "
                f"(tool_call {message.tool_call_id})]"
            ),
        }
    ]
    content.extend(_input_image_part(image) for image in message.images)
    return content
```

`ResponsesTransport.convert_messages` 改为带 pending relay 的遍历：

```python
    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        validate_tool_turn_sequence(messages)
        out: list[dict[str, Any]] = []
        pending_relay: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                continue
            if pending_relay and isinstance(message, UserMessage):
                user_item = _user_input_item(message)
                user_item["content"] = pending_relay + user_item["content"]
                out.append(user_item)
                pending_relay = []
                continue
            if pending_relay:
                out.append({"role": "user", "content": pending_relay})
                pending_relay = []
            if isinstance(message, UserMessage):
                out.append(_user_input_item(message))
                continue
            if isinstance(message, AssistantMessage):
                out.extend(self._assistant_to_items(message))
                continue
            if isinstance(message, ToolMessage):
                out.append(_function_call_output_item(message))
                pending_relay.extend(_relay_content_for(message))
                continue
        if pending_relay:
            out.append({"role": "user", "content": pending_relay})
        return out
```

语义与 ChatCompletions relay 对齐：kernel `Message` 列表不插入假 user，只在 wire 层把工具图片转成模型可见的用户输入图片 item。

- [ ] **Step 4: 跑测试确认通过 + Responses 回归**

Run: `uv run pytest tests/matmaster/providers/test_responses_convert.py -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/providers/transports/responses.py tests/matmaster/providers/test_responses_convert.py
git commit -m "feat(providers): responses relays tool images via user input items"
```

---

### Task 8: 防线 3 — compaction 估算常数与摘要无条件剥图

**Files:**
- Modify: `matmaster/context/compaction.py`（常数区约 91 行、`estimate_tokens` 约 125 行、`prepare_messages_for_summary_call` 约 262 行、新增 `_drop_images_for_summary`）
- Test: `tests/matmaster/context/test_compaction.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_estimate_tokens_counts_images():
    from matmaster.context.compaction import _IMAGE_TOKEN_ESTIMATE, estimate_tokens
    from matmaster.types.messages import ImageContentPart, ToolMessage

    bare = ToolMessage(tool_call_id="tc1", tool_name="Read", content="x")
    with_images = bare.model_copy(
        update={
            "images": [
                ImageContentPart(url="data:image/png;base64,aGVsbG8="),
                ImageContentPart(url="data:image/png;base64,aGVsbG8="),
            ]
        }
    )
    delta = estimate_tokens([with_images]) - estimate_tokens([bare])
    assert delta == 2 * _IMAGE_TOKEN_ESTIMATE


def test_summary_prep_strips_images_even_within_budget():
    """短 content 带图消息：budget 内 early-return 路径也必须剥图。"""
    from matmaster.context.compaction import prepare_messages_for_summary_call
    from matmaster.types.messages import (
        AssistantMessage,
        ImageContentPart,
        SystemMessage,
        ToolCallData,
        ToolMessage,
        UserMessage,
    )

    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="看图"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="tc1", name="Read", arguments={})],
        ),
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png",  # 远短于 500 字符的截断选材门槛
            images=[ImageContentPart(url="data:image/png;base64,aGVsbG8=")],
        ),
        AssistantMessage(content="done"),
    ]
    prep = prepare_messages_for_summary_call(
        full_messages=messages,
        phase="runtime",
        turn_input=None,
        compact_request=UserMessage(content="summarize"),
        context_limit=1_000_000,  # budget 充裕 → 走 early-return 分支
        reserved_summary_tokens=1_000,
    )
    tool_msgs = [m for m in prep.messages if getattr(m, "tool_call_id", None) == "tc1"]
    assert tool_msgs[0].images == []
    assert "[images omitted for summary: 1]" in tool_msgs[0].content
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/context/test_compaction.py -q -k "images or summary_prep"`
Expected: FAIL（无常数 / early-return 原样返回带图消息）。

- [ ] **Step 3: 实现**

(a) 常数区（`_TRUNCATE_MIN_CONTENT_CHARS = 500` 附近）加：

```python
_IMAGE_TOKEN_ESTIMATE = 2000
```

(b) `estimate_tokens` 循环内 `total += 4` 之后加：

```python
        images = getattr(msg, "images", None)
        if images:
            total += _IMAGE_TOKEN_ESTIMATE * len(images)
```

(c) 新增模块级函数（放 `_truncate_tool_message_for_summary` 之前）：

```python
def _drop_images_for_summary(msg: Message) -> Message:
    """摘要请求无条件剥图：与按长度选材的截断完全解耦（spec §9 防线 3）。"""
    if not isinstance(msg, ToolMessage) or not msg.images:
        return msg
    return msg.model_copy(
        update={
            "images": [],
            "content": (msg.content or "")
            + f"\n[images omitted for summary: {len(msg.images)}]",
        }
    )
```

(d) `prepare_messages_for_summary_call` 中 `base_messages = _summary_base_messages(...)` 之后、`request_tokens = ...` 之前加：

```python
    base_messages = [_drop_images_for_summary(msg) for msg in base_messages]
```

- [ ] **Step 4: 跑测试确认通过 + compaction 全量回归**

Run: `uv run pytest tests/matmaster/context/test_compaction.py tests/matmaster/core/test_agent_compaction.py tests/matmaster/core/test_agent_kernel_compaction.py -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/context/compaction.py tests/matmaster/context/test_compaction.py
git commit -m "feat(context): compaction sees image tokens and strips images before summary"
```

---

### Task 9: 防线 2 — kernel 在途图片预算

**Files:**
- Modify: `matmaster/types/message_normalization.py`（新增常数与 `apply_tool_image_budget`）
- Modify: `matmaster/core/agent.py`（feed_tail 调用点，约 329 行）
- Test: `tests/matmaster/types/test_message_normalization.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def _tool_msg(call_id: str, n_images: int = 1, byte_size: int = 100):
    from matmaster.types.messages import ImageContentPart, ToolMessage

    payload = "x" * byte_size
    return ToolMessage(
        tool_call_id=call_id,
        tool_name="Read",
        content=f"Read image: /{call_id}.png",
        images=[
            ImageContentPart(url=f"data:image/png;base64,{payload}")
            for _ in range(n_images)
        ],
    )


def test_budget_keeps_newest_strips_oldest_by_count():
    from matmaster.types.message_normalization import apply_tool_image_budget

    messages = [_tool_msg(f"tc{i}") for i in range(6)]
    out = apply_tool_image_budget(messages, max_count=4, max_bytes=10**9)
    assert [bool(m.images) for m in out] == [False, False, True, True, True, True]
    assert "[image pruned from context" in out[0].content
    assert "[image pruned from context" not in out[2].content
    # 原对象不被修改（视图层策略）
    assert messages[0].images


def test_budget_strips_by_bytes():
    from matmaster.types.message_normalization import apply_tool_image_budget

    messages = [_tool_msg(f"tc{i}", byte_size=600) for i in range(3)]
    # 每条约 600+ 字节，预算 1300 → 只留最新 2 条
    out = apply_tool_image_budget(messages, max_count=10, max_bytes=1300)
    assert [bool(m.images) for m in out] == [False, True, True]


def test_budget_noop_within_limits():
    from matmaster.types.message_normalization import apply_tool_image_budget

    messages = [_tool_msg("tc0"), _tool_msg("tc1")]
    out = apply_tool_image_budget(messages)
    assert out[0] is messages[0] and out[1] is messages[1]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/types/test_message_normalization.py -q -k budget`
Expected: FAIL，`ImportError: apply_tool_image_budget`。

- [ ] **Step 3: 实现**

`matmaster/types/message_normalization.py` 追加（文件已 import `ToolMessage`）：

```python
TOOL_IMAGE_BUDGET_MAX_COUNT = 4
TOOL_IMAGE_BUDGET_MAX_BYTES = 16 * 1024 * 1024
_IMAGE_PRUNED_MARKER = "\n[image pruned from context: re-Read the file if needed]"


def apply_tool_image_budget(
    messages: list[Message],
    *,
    max_count: int = TOOL_IMAGE_BUDGET_MAX_COUNT,
    max_bytes: int = TOOL_IMAGE_BUDGET_MAX_BYTES,
) -> list[Message]:
    """请求前在途预算：最新优先保留工具图片，超出者剥离并留占位（spec §9 防线 2）。

    纯视图层——不修改入参消息对象本身；剥离以 model_copy 生成副本。
    """
    out = list(messages)
    kept_count = 0
    kept_bytes = 0
    for idx in range(len(out) - 1, -1, -1):
        msg = out[idx]
        if not isinstance(msg, ToolMessage) or not msg.images:
            continue
        msg_bytes = sum(len(image.url) for image in msg.images)
        if (
            kept_count + len(msg.images) <= max_count
            and kept_bytes + msg_bytes <= max_bytes
        ):
            kept_count += len(msg.images)
            kept_bytes += msg_bytes
            continue
        out[idx] = msg.model_copy(
            update={
                "images": [],
                "content": (msg.content or "") + _IMAGE_PRUNED_MARKER,
            }
        )
    return out
```

`matmaster/core/agent.py`：import 处加 `apply_tool_image_budget`（与 `validate_tool_turn_sequence` 同源模块），调用点改为：

```python
            canonical_messages = state.pipeline.feed_tail(state.messages)
            canonical_messages = apply_tool_image_budget(canonical_messages)
            validate_tool_turn_sequence(canonical_messages)
```

（挂在 feed_tail 之后而非其内部：feed_tail 维护前缀缓存，窗口滑动改写前缀会触发其 mutation 检测；预算作用于其返回的副本列表，`state.messages` 与缓存均不受影响。）

- [ ] **Step 4: 跑测试确认通过 + kernel 回归**

Run: `uv run pytest tests/matmaster/types/test_message_normalization.py tests/matmaster/core -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/types/message_normalization.py matmaster/core/agent.py tests/matmaster/types/test_message_normalization.py
git commit -m "feat(core): per-request tool-image budget (4 imgs / 16 MiB, newest-first)"
```

---

### Task 10: 事件恢复路径三位点（chat_history）

**Files:**
- Modify: `src/services/chat_history.py`（`_tool_result_from_event` 约 339-350 行；`events_to_dialog_messages` 的 tool_result 分支约 571-593 行；`events_to_messages` 的 role=tool 分支约 688-695 行）
- Test: `tests/services/test_chat_history_images.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/services/test_chat_history_images.py`（事件 dict 形状参照 `tests/services/test_events_to_messages_provider_state.py` 的既有构造惯例——type/source/content 三键；若该文件用了辅助构造函数，复用之）：

```python
"""tool_result 事件中的 images 经 events_to_messages 恢复为 ToolMessage.images。"""

from src.services.chat_history import ChatHistoryConverter

_IMG = {"url": "data:image/png;base64,aGVsbG8=", "mime_type": "image/png", "detail": None}


def _events_with_tool_image():
    return [
        {"type": "query", "source": "User", "content": {"content": "看图"}},
        {
            "type": "tool_call",
            "source": "matmaster",
            "content": {"id": "tc1", "name": "Read", "args": {"file_path": "/a.png"}},
        },
        {
            "type": "tool_result",
            "source": "matmaster",
            "content": {
                "id": "tc1",
                "name": "Read",
                "result": "Read image: /a.png",
                "status": "success",
                "images": [_IMG],
            },
        },
        {"type": "response", "source": "matmaster", "content": "看到了"},
    ]


def test_events_to_messages_restores_tool_images():
    messages = ChatHistoryConverter.events_to_messages(_events_with_tool_image())
    tool_msgs = [m for m in messages if getattr(m, "tool_call_id", None) == "tc1"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].images[0].url == _IMG["url"]
    assert tool_msgs[0].images[0].mime_type == "image/png"


def test_events_without_images_restore_empty_list():
    events = _events_with_tool_image()
    del events[2]["content"]["images"]
    messages = ChatHistoryConverter.events_to_messages(events)
    tool_msgs = [m for m in messages if getattr(m, "tool_call_id", None) == "tc1"]
    assert tool_msgs[0].images == []
```

注：若 `events_to_dialog_messages` 对 query/tool_call/response 事件形状有更多必要字段（跑测试看具体报错），按 `test_events_to_messages_provider_state.py` 中的事件构造修正 `_events_with_tool_image`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_chat_history_images.py -v`
Expected: `test_events_to_messages_restores_tool_images` FAIL（images 为空）。

- [ ] **Step 3: 实现三位点**

(a) `_tool_result_from_event`：返回值从三元组改为四元组：

```python
    @staticmethod
    def _tool_result_from_event(ev: dict) -> tuple[str, str, Any, list] | None:
        """从 type=tool_result 的事件 content 得到 (tool_call_id, name, content, images)。"""
        c = ev.get('content')
        if not isinstance(c, dict):
            return None
        call_id = str(c.get('id') or '')
        name = str(c.get('name') or '')
        result = c.get('result')
        if result is None:
            result = {}
        images = c.get('images')
        if not isinstance(images, list):
            images = []
        return (call_id, name, result, images)
```

先 `grep -n "_tool_result_from_event" src/ matmaster/` 找出**所有**调用方，逐一改为四元组解包。已知调用方：`events_to_dialog_messages`（chat_history.py 约 572 行）。若 grep 发现其他调用方（如 kernel 侧 history_restore 的注入回调），同步更新解包并把 images 传到其 ToolMessage 构造。

(b) `events_to_dialog_messages` 的 tool_result 分支（约 572-593 行）整段改为（orphan 判定逻辑原样保留，仅解包与构造两处变化）：

```python
            if typ == 'tool_result':
                triple = cls._tool_result_from_event(ev)
                if triple:
                    flush_tool_calls()
                    call_id, name, content, images = triple
                    if (
                        call_id not in active_tool_turn_ids
                        and call_id not in assistant_state_tool_ids
                    ):
                        logger.warning(
                            "chat_history: skipping orphan tool_result call_id=%s context=events_to_dialog_messages",
                            call_id[:64],
                        )
                        continue
                    assistant_state_tool_ids.discard(call_id)
                    active_tool_turn_ids.discard(call_id)
                    out.append(
                        ToolMessage(
                            tool_call_id=call_id,
                            tool_name=name,
                            content=content,
                            images=[
                                ImageContentPart.model_validate(image)
                                for image in images
                            ],
                        ).model_dump()
                    )
                continue
```

（`ImageContentPart` 已在文件 import——`events_to_messages` 的 user 分支在用。）

(c) `events_to_messages` 的 `role == "tool"` 分支：

```python
            elif role == "tool":
                messages.append(
                    ToolMessage(
                        content=d.get("content", ""),
                        tool_call_id=d.get("tool_call_id", ""),
                        tool_name=d.get("tool_name", ""),
                        images=[
                            ImageContentPart.model_validate(image)
                            for image in d.get("images", [])
                        ],
                    )
                )
```

- [ ] **Step 4: 跑测试确认通过 + 相关服务回归**

Run: `uv run pytest tests/services -q && uv run pytest tests/matmaster/services tests/matmaster/context/test_history_restore.py -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/services/chat_history.py tests/services/test_chat_history_images.py
git commit -m "feat(services): restore tool images from tool_result events across rebuild points"
```

---

### Task 11: 防线 4 — 恢复层按模型能力剥图

**Files:**
- Modify: `src/services/image_input_service.py`（新增 `strip_all_history_images`）
- Modify: `src/services/model_history_restore_service.py`（构造参数 + `_finalize_history`，替换两处 `return trim_history_images(messages)`）
- Modify: `src/services/agent_run_history_wiring.py`（`build_history_wiring` 加参数，约 60-76 行）
- Modify: `src/services/agent_run_service.py`（`build_history_wiring(` 调用，约 484 行）
- Test: `tests/services/test_image_input_service.py`（追加）
- Test: `tests/matmaster/services/test_model_history_restore_service.py`（追加）

- [ ] **Step 1: 写失败测试（strip 函数）**

在 `tests/services/test_image_input_service.py` 追加：

```python
def test_strip_all_history_images_both_directions():
    from matmaster.types.messages import ImageContentPart, ToolMessage, UserMessage
    from src.services.image_input_service import strip_all_history_images

    data_uri = "data:image/png;base64,aGVsbG8="
    messages = [
        UserMessage(content="看", images=[ImageContentPart(url="https://oss/a.png")]),
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png",
            images=[ImageContentPart(url=data_uri)],
        ),
        ToolMessage(tool_call_id="tc2", tool_name="Bash", content="ok"),
    ]
    out = strip_all_history_images(messages)
    assert out[0].images == [] and out[1].images == []
    assert "[历史图片已移除：当前模型不支持图片输入]" in out[0].content
    assert "[历史图片已移除：当前模型不支持图片输入]" in out[1].content
    assert out[2] is messages[2]  # 无图消息原对象直通
    assert messages[1].images  # 入参不被修改
```

- [ ] **Step 2: 写失败测试（restore 接线，自包含单测 `_finalize_history`）**

在 `tests/matmaster/services/test_model_history_restore_service.py` 追加（不依赖 events_table fixture，直接测 finalize 分流——两条 return 路径共用它）：

```python
def test_finalize_history_strips_images_when_vision_unsupported() -> None:
    from matmaster.types.messages import ImageContentPart, ToolMessage
    from src.services.model_history_restore_service import ModelHistoryRestoreService

    history = [
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png",
            images=[ImageContentPart(url="data:image/png;base64,aGVsbG8=")],
        )
    ]
    no_vision = ModelHistoryRestoreService(None, supports_vision=False)
    stripped = no_vision._finalize_history(history)
    assert stripped[0].images == []
    assert "[历史图片已移除：当前模型不支持图片输入]" in stripped[0].content

    with_vision = ModelHistoryRestoreService(None, supports_vision=True)
    kept = with_vision._finalize_history(history)
    # supports_vision=True：工具图原样保留，且不被 trim_history_images
    # 的 https 校验误剥（spec §9 防线 4 注意事项）
    assert kept[0].images == history[0].images
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/services/test_image_input_service.py tests/matmaster/services/test_model_history_restore_service.py -q -k "strip or vision"`
Expected: FAIL（函数不存在 / 构造参数不存在）。

- [ ] **Step 4: 实现**

(a) `src/services/image_input_service.py`，`trim_history_images` 之后追加：

```python
_NON_VISION_PLACEHOLDER = "[历史图片已移除：当前模型不支持图片输入]"


def strip_all_history_images(messages: list[Message]) -> list[Message]:
    """目标模型不支持视觉时，剥离历史中 User/Tool 双方向全部图片并留占位。

    不走 validate_history_image_url（其要求 https，会把 data URI 判非法）；
    按 images 字段存在性处理（spec §9 防线 4）。
    """
    out = list(messages)
    for idx, message in enumerate(out):
        images = getattr(message, "images", None)
        if not images:
            continue
        text = message.content or ""
        placeholders = "\n".join(_NON_VISION_PLACEHOLDER for _ in images)
        out[idx] = message.model_copy(
            update={
                "content": "\n".join(item for item in (text, placeholders) if item),
                "images": [],
            }
        )
    return out
```

(b) `src/services/model_history_restore_service.py`：

`__init__` 加 keyword 参数（看现有 `__init__` 形状，保持其余不动）：

```python
    def __init__(self, events_table, *, supports_vision: bool = True) -> None:
        self.events_table = events_table
        self._supports_vision = supports_vision
```

（默认 True：能力未知时不剥，剥图必须显式声明不支持。）

新增方法：

```python
    def _finalize_history(self, messages: list[Message]) -> list[Message]:
        if not self._supports_vision:
            return strip_all_history_images(messages)
        return trim_history_images(messages)
```

文件内**两处** `return trim_history_images(messages)`（checkpoint 路径约 59 行、events 兜底路径约 95 行）都改为 `return self._finalize_history(messages)`。import 处加 `strip_all_history_images`（与 `trim_history_images` 同源）。

(c) `src/services/agent_run_history_wiring.py`，`build_history_wiring` 签名加参数并传入：

```python
def build_history_wiring(
    *,
    events_table: Any | None,
    session_id: str,
    task_id: str,
    raw_history_limit: int,
    checkpoint_sink_factory: Callable,
    pre_compaction_barrier: Callable,
    supports_vision: bool = True,
) -> HistoryWiringResult:
```

```python
        ModelHistoryRestoreService(
            events_table, supports_vision=supports_vision
        ).restore_history(
```

(d) `src/services/agent_run_service.py`，`build_history_wiring(` 调用（约 484 行）加实参：

```python
                supports_vision=llm_bundle.supports_vision,
```

- [ ] **Step 5: 跑测试确认通过 + 服务层回归**

Run: `uv run pytest tests/services tests/matmaster/services -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add src/services/image_input_service.py src/services/model_history_restore_service.py src/services/agent_run_history_wiring.py src/services/agent_run_service.py tests/
git commit -m "feat(services): strip history images for non-vision models at restore"
```

---

### Task 12: checkpoint 往返测试 + 全量回归

**Files:**
- Test: `tests/services/test_history_checkpoint_codec_images.py`（新建）

- [ ] **Step 1: 写 checkpoint codec 往返测试（应直接通过——codec 是泛化序列化，此测试为回归锚）**

```python
"""checkpoint codec 对 ToolMessage.images 的泛化序列化往返（spec §5.6）。"""

from matmaster.types.messages import ImageContentPart, ToolMessage
from src.services.history_checkpoint_codec import (
    deserialize_base_messages,
    serialize_base_messages,
)


def test_checkpoint_roundtrip_preserves_tool_images():
    messages = [
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png",
            images=[
                ImageContentPart(
                    url="data:image/png;base64,aGVsbG8=",
                    mime_type="image/png",
                    detail="high",
                )
            ],
        )
    ]
    restored = deserialize_base_messages(serialize_base_messages(messages))
    assert restored[0].images == messages[0].images
```

- [ ] **Step 2: 跑该测试**

Run: `uv run pytest tests/services/test_history_checkpoint_codec_images.py -v`
Expected: PASS（若 FAIL，说明 codec 对未知字段有过滤，按报错修复——但按 `serialize_base_messages` 的 `model_dump(mode="json")` 实现不应发生）。

- [ ] **Step 3: 全量测试回归**

Run: `uv run pytest tests/matmaster tests/services -q`
Expected: 全 PASS。任何失败先判断是否本计划改动引入；与本计划无关的预存失败记录后跳过，不顺手修。

- [ ] **Step 3b: 确认事件存储容量（spec §9 持久化体积）**

Run: `grep -n "content" src/sql/create_chat_tables.sql | head -5`
Expected: `content` 列为 `JSON` 类型（调研已确认）。在最终汇报中注明部署要求：MySQL `max_allowed_packet` 建议 ≥ 64M（单图 base64 后约 4 MiB，checkpoint 行上界为防线 2 的 16 MiB 图片预算量级，另有 JSON envelope / SQL packet 开销；若部署侧不能保证该阈值，应下调 `TOOL_IMAGE_BUDGET_MAX_BYTES` 后再发布）；这是部署配置项，不改代码。

- [ ] **Step 4: 静态检查**

Run: `uv run --extra dev pre-commit run --all-files`
Expected: 全过（ruff/格式钩子按仓库 .pre-commit 配置执行）。失败项逐一修复后重跑。

- [ ] **Step 5: Commit**

```bash
git add tests/services/test_history_checkpoint_codec_images.py
git commit -m "test(services): checkpoint codec roundtrip anchor for tool images"
```

---

## 规格条目 → 任务覆盖对照

| 规格条目 | 任务 |
|---|---|
| §5.1-4 types 三字段 + dispatch + 事件 | Task 1、2 |
| `ToolResult.images` 在 ToolRunner 截断路径不丢失 | Task 2b |
| §5.5 事件恢复三位点 | Task 10 |
| §5.6 checkpoint 零改动（回归锚） | Task 12 |
| §5.7 SSE 携带 images（与 DB 同源映射） | Task 2 |
| §6 Read 图片分支全部行为 | Task 3、4 |
| §6 vision 门控注入链 | Task 5 |
| §7 Anthropic tool_result block | Task 6 |
| §8 relay（含按消息归属、并入后继 user） | Task 7 |
| Responses transport 的 vision profile 工具图 relay | Task 7b |
| §9 防线 1 | Task 3、4 |
| §9 防线 2（4 张/16 MiB） | Task 9 |
| §9 防线 3（2000 常数 + 无条件剥图） | Task 8 |
| §9 防线 4（恢复层剥图 + 双方向） | Task 11 |
| §9 持久化体积（列类型与 max_allowed_packet 确认） | Task 12 Step 3b |
| §10 错误矩阵 | Task 3、4（工具层各 error 路径测试） |
| §11 测试计划 | 各任务内嵌 + Task 12 |
