# Chat Response Figures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MatMaster 增加基于 manifest 的 chat response 图片能力，让 Bash 产出的图片能同步上传到产品侧 OSS，作为 `response_figures` 事件进入 SSE 与历史回放，同时保持正文仍是纯文本。

**Architecture:** 本仓库只实现后端与协议层。Bash 工具在 session 可见目录中产出 `ARTIFACT_DIR` 与 `MANIFEST_PATH`，工具侧 collector 负责校验、上传并把标准化图片描述写入 `ToolResult.payload.figures`，`AgentRunService` 再在 `run_result` 之前汇总出一次性的 `response_figures` 事件。下游 Web 侧边栏与 PDF 渲染器消费这个正式事件，不在本仓库实现 UI。

**Tech Stack:** Python 3.13（`uv run`）, Pydantic, MatMaster session abstraction（local/SSH）, Aliyun OSS helper, SSE `ag-ui`, pytest

---

## Scope Guardrails

### In scope

1. 新增图片 manifest / descriptor / `response_figures` 后端类型合同。
2. 为 Bash 工具注入 `ARTIFACT_DIR` 与 `MANIFEST_PATH`。
3. 本地与 Bohrium SSH session 下的图片收集、校验、上传与 `payload.figures` 产出。
4. 在 `run_result` 之前发出一次性的 `response_figures` 事件。
5. SSE / 持久化 / 历史回放都能携带该事件。
6. 用测试锁定顺序、去重、路径安全、格式限制与回放行为。

### Out of scope

1. 本仓库内实现前端侧边栏 UI。
2. PDF 渲染器实现。
3. 非 Bash 工具的自动产图接入。
4. 图片异步上传、进度流、独立图片表或独立 job 系统。
5. `svg`、`tiff`、`eps` 等额外格式支持。

## File Structure

### 新增文件

- `matmaster/types/figures.py`
  - 图片 manifest 条目、标准化 descriptor、临时运行态上传配置。
- `matmaster/tools/figure_artifacts.py`
  - manifest 解析、路径校验、格式/大小校验、session 下载与上传归一化。
- `src/services/response_figures_service.py`
  - 回答级图片聚合、first-writer-wins、最终 `ResponseFiguresEvent` 构造。
- `tests/matmaster/types/test_figures.py`
  - 图片 schema 与默认值测试。
- `tests/matmaster/tools/test_figure_artifacts.py`
  - manifest 解析、路径穿越、格式白名单、上传 key、部分失败保留成功图等单测。

### 修改文件

- `matmaster/types/events.py`
  - 增加 `ResponseFiguresEvent`，并加入 `SystemEvent` / `BusEvent`。
- `matmaster/types/__init__.py`
  - 导出 `ResponseFiguresEvent` 与图片 schema。
- `matmaster/types/tool_spec.py`
  - `ToolExecutionContext` 增加 `tool_call_id`，让工具能拿到本次调用 id。
- `matmaster/core/tool_runner.py`
  - 为每个 tool call 生成独立的 per-call `ToolExecutionContext`，避免共享上下文产生并发串扰。
- `matmaster/core/exp.py`
  - 把图片上传配置从 `run_meta` 塞进 `runner_state`。
- `matmaster/tools/builtin/bash_tool.py`
  - 注入 `ARTIFACT_DIR` / `MANIFEST_PATH`，并在命令完成后调用 figure collector。
- `matmaster/integration/event_payloads.py`
  - 显式映射 `response_figures` 的 SSE / 持久化 `content` 形状。
- `src/services/agent_run_service.py`
  - 构造 figure upload callback 并注入 `pg_ctx.run_meta`，收集 `payload.figures`，在 `run_result` 之前发射 `response_figures`。
- `src/models/chat.py`
  - 更新公开 `ag-ui` 协议文档，加入 `response_figures` 说明。
- `tests/matmaster/types/test_events.py`
  - 联合类型与 `ResponseFiguresEvent` round-trip。
- `tests/matmaster/types/test_tool_spec.py`
  - `ToolExecutionContext.tool_call_id`。
- `tests/matmaster/core/test_full_tool_runner.py`
  - `tool_call_id` 按每个调用分别传给 executor。
- `tests/matmaster/tools/builtin/test_bash_tool.py`
  - Bash 成功产图时返回 `ToolResult(payload.figures=...)`，无 manifest 时保持旧行为。
- `tests/matmaster/integration/test_event_payloads.py`
  - `response_figures` 的公开 payload 映射。
- `tests/matmaster/services/test_agent_run_stream.py`
  - `response_figures` 在 `run_result` 前发射，且只发一次。
- `tests/test_chat_stream_direct.py`
  - 历史回放包含 `response_figures`，并且不影响现有 `response` / `run_result` 去重。
- `tests/matmaster/integration/test_events_to_messages.py`
  - `response_figures` 不进入 LLM dialog history。

## Task 1: 建立图片类型与事件合同

**Files:**
- Create: `matmaster/types/figures.py`
- Modify: `matmaster/types/events.py`
- Modify: `matmaster/types/__init__.py`
- Test: `tests/matmaster/types/test_figures.py`
- Test: `tests/matmaster/types/test_events.py`

- [ ] **Step 1: 写失败测试，锁定图片 schema 默认值与事件 union**

```python
from pydantic import TypeAdapter

from matmaster.types.events import BusEvent, ResponseFiguresEvent
from matmaster.types.figures import FigureDescriptor, FigureManifestEntry


def test_figure_manifest_entry_defaults() -> None:
    entry = FigureManifestEntry(
        figure_id="band_structure",
        path="plots/band.png",
        caption="Si 的能带图",
    )

    assert entry.importance == "secondary"
    assert entry.placement_hint == "sidebar_only"
    assert entry.alt is None


def test_response_figures_event_round_trips_through_bus_union() -> None:
    evt = ResponseFiguresEvent(
        source="System",
        figures=[
            FigureDescriptor(
                figure_id="band_structure",
                asset_url="https://oss.example/band.png",
                caption="Si 的能带图",
                alt="Si 的能带结构图",
                importance="primary",
                placement_hint="sidebar_only",
                source_tool_call_id="call-band",
            )
        ],
    )

    dumped = evt.model_dump(mode="json")
    restored = TypeAdapter(BusEvent).validate_python(dumped)

    assert isinstance(restored, ResponseFiguresEvent)
    assert restored.figures[0].figure_id == "band_structure"
```

- [ ] **Step 2: 运行聚焦测试，确认它们先失败**

Run:

```bash
uv run pytest tests/matmaster/types/test_figures.py tests/matmaster/types/test_events.py -k "figure or response_figures" -v
```

Expected:

- `ModuleNotFoundError` 或 `ImportError`，因为 `matmaster.types.figures` 还不存在。
- `ValidationError` / union fail，因为 `ResponseFiguresEvent` 还未加入事件系统。

- [ ] **Step 3: 实现最小 schema 与事件类型**

```python
# matmaster/types/figures.py
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FigureManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figure_id: str
    path: str
    caption: str
    alt: str | None = None
    importance: Literal["primary", "secondary"] = "secondary"
    placement_hint: Literal["sidebar_only", "appendix_candidate"] = "sidebar_only"


class FigureDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figure_id: str
    asset_url: str
    caption: str
    alt: str | None = None
    importance: Literal["primary", "secondary"] = "secondary"
    placement_hint: Literal["sidebar_only", "appendix_candidate"] = "sidebar_only"
    source_tool_call_id: str | None = None


class FigureUploadConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    task_id: str
    asset_key_prefix: str
    upload_bytes: Callable[[bytes, str], str]
```

```python
# matmaster/types/events.py
class ResponseFiguresEvent(EventBase):
    type: Literal["response_figures"] = "response_figures"
    figures: list[FigureDescriptor] = Field(default_factory=list)
```

```python
# matmaster/types/events.py
SystemEvent = Annotated[
    Union[
        ConfirmationRequestEvent,
        ConfirmationTimeoutEvent,
        AskQuestionEvent,
        AskQuestionReplyEvent,
        AskQuestionTimeoutEvent,
        CompactionEvent,
        ExpRunEvent,
        CancelledEvent,
        StreamClosedEvent,
        WorkspaceUploadErrorEvent,
        BohriumNodeEvent,
        McpServerStatusEvent,
        McpConnectEvent,
        ResponseFiguresEvent,
    ],
    Field(discriminator="type"),
]
```

```python
# matmaster/types/events.py
BusEvent = Annotated[
    Union[
        ThoughtEvent,
        ResponseEvent,
        ToolCallEvent,
        ToolResultEvent,
        RunResultEvent,
        ErrorEvent,
        AssistantStateEvent,
        SkillHitEvent,
        ToolProgressEvent,
        ConfirmationRequestEvent,
        ConfirmationTimeoutEvent,
        AskQuestionEvent,
        AskQuestionReplyEvent,
        AskQuestionTimeoutEvent,
        CompactionEvent,
        ExpRunEvent,
        CancelledEvent,
        StreamClosedEvent,
        WorkspaceUploadErrorEvent,
        BohriumNodeEvent,
        McpServerStatusEvent,
        McpConnectEvent,
        ResponseFiguresEvent,
    ],
    Field(discriminator="type"),
]
```

- [ ] **Step 4: 重新运行聚焦测试**

Run:

```bash
uv run pytest tests/matmaster/types/test_figures.py tests/matmaster/types/test_events.py -k "figure or response_figures" -v
```

Expected: PASS

- [ ] **Step 5: 提交这一小块合同变更**

```bash
git add matmaster/types/figures.py matmaster/types/events.py matmaster/types/__init__.py tests/matmaster/types/test_figures.py tests/matmaster/types/test_events.py
git commit -m "feat: add response figures event contract"
```

---

## Task 2: 给每个 tool call 传递独立图片上下文

**Files:**
- Modify: `matmaster/types/tool_spec.py`
- Modify: `matmaster/core/tool_runner.py`
- Modify: `matmaster/core/exp.py`
- Test: `tests/matmaster/types/test_tool_spec.py`
- Test: `tests/matmaster/core/test_full_tool_runner.py`

- [ ] **Step 1: 写失败测试，锁定 `tool_call_id` 与 per-call exec context**

```python
from matmaster.types.tool_spec import ToolExecutionContext


def test_tool_execution_context_accepts_tool_call_id() -> None:
    ctx = ToolExecutionContext(tool_call_id="call-1")
    assert ctx.tool_call_id == "call-1"
```

```python
@pytest.mark.asyncio
async def test_full_tool_runner_passes_tool_call_id_into_executor() -> None:
    seen: list[str | None] = []

    async def executor(args, exec_ctx):
        seen.append(exec_ctx.tool_call_id)
        return ToolResult(content="ok")

    spec = ToolSpec(tool_name="Bash")
    binding = ToolBinding(binding_key="session_shell:Bash", plane=ToolPlane.SESSION_SHELL)
    instance = ToolInstance(tool_spec=spec, tool_binding=binding, tool_executor=executor)

    catalog = MagicMock()
    catalog.get_tool.return_value = instance

    runner = FullToolRunner(
        catalog=catalog,
        structural_validation=StructuralValidation(),
        capability_policy=DefaultCapabilityPolicy(),
        scheduler=ToolScheduler(default_timeout=1.0),
        topology=_make_topology(),
        state=ToolRunnerState(),
    )

    await runner.execute_batch([_make_tc("Bash", call_id="call-xyz")], _make_ctx())

    assert seen == ["call-xyz"]
```

- [ ] **Step 2: 运行测试，确认它们失败**

Run:

```bash
uv run pytest tests/matmaster/types/test_tool_spec.py tests/matmaster/core/test_full_tool_runner.py -k "tool_call_id" -v
```

Expected:

- `ToolExecutionContext` 没有 `tool_call_id` 字段。
- `FullToolRunner` 给 executor 的上下文不区分具体调用。

- [ ] **Step 3: 加入 per-call context，并把 run-level figure config 放到 runner_state**

```python
# matmaster/types/tool_spec.py
@dataclass(frozen=True)
class ToolExecutionContext:
    cancel_token: CancellationToken | None = None
    on_progress: Callable[[str], Awaitable[None]] | None = None
    runner_state: ToolRunnerState | None = None
    tool_call_id: str | None = None
```

```python
# matmaster/core/tool_runner.py
from dataclasses import replace

...
call_exec_ctx = replace(exec_ctx, tool_call_id=tc.id)
tr = await instance.tool_executor(effective_args, call_exec_ctx)
```

```python
# matmaster/core/exp.py
figure_upload_config = run_meta.get("figure_upload_config")
if figure_upload_config is not None:
    runner_state.set("figure_upload_config", figure_upload_config)
```

- [ ] **Step 4: 重新运行聚焦测试**

Run:

```bash
uv run pytest tests/matmaster/types/test_tool_spec.py tests/matmaster/core/test_full_tool_runner.py -k "tool_call_id" -v
```

Expected: PASS

- [ ] **Step 5: 提交上下文注入改动**

```bash
git add matmaster/types/tool_spec.py matmaster/core/tool_runner.py matmaster/core/exp.py tests/matmaster/types/test_tool_spec.py tests/matmaster/core/test_full_tool_runner.py
git commit -m "feat: pass per-call figure context into tools"
```

---

## Task 3: 实现图片 manifest 解析、校验与上传归一化

**Files:**
- Create: `matmaster/tools/figure_artifacts.py`
- Test: `tests/matmaster/tools/test_figure_artifacts.py`

- [ ] **Step 1: 写失败测试，锁定 manifest、路径安全、格式白名单与部分成功保留**

```python
import pytest
from unittest.mock import MagicMock

from matmaster.tools.figure_artifacts import (
    FigureCollectionResult,
    build_figure_env,
    collect_figures_from_session,
)
from matmaster.types.figures import FigureUploadConfig


def test_build_figure_env_uses_tool_call_scoped_paths() -> None:
    artifact_dir, manifest_path = build_figure_env("/share", "call-1")
    assert artifact_dir == "/share/.matmaster/figures/call-1/artifacts"
    assert manifest_path == "/share/.matmaster/figures/call-1/manifest.json"


def _upload_cfg(upload_bytes=lambda data, key: f"https://oss.example/{key}") -> FigureUploadConfig:
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=upload_bytes,
    )


def test_collect_figures_invalid_manifest_returns_warning_and_no_figures() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"../../etc/passwd","caption":"bad"}]}'
    )

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == []
    assert result.warnings and "invalid_manifest" in result.warnings[0]


def test_collect_figures_duplicate_ids_returns_warning_and_no_figures() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = """
    {"figures":[
      {"figure_id":"band","path":"plots/band.png","caption":"band"},
      {"figure_id":"band","path":"plots/band-2.png","caption":"band2"}
    ]}
    """.strip()

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == []
    assert result.warnings and "invalid_manifest" in result.warnings[0]


def test_collect_figures_keeps_successful_entries_when_one_upload_fails() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = """
    {"figures":[
      {"figure_id":"band","path":"plots/band.png","caption":"band"},
      {"figure_id":"dos","path":"plots/dos.png","caption":"dos"}
    ]}
    """.strip()
    fake_session.download.side_effect = [
        b"\\x89PNG\\r\\n\\x1a\\n" + b"a" * 32,
        b"\\x89PNG\\r\\n\\x1a\\n" + b"b" * 32,
    ]

    def upload_bytes(data: bytes, key: str) -> str:
        if key.endswith("dos.png"):
            raise RuntimeError("upload failed")
        return f"https://oss.example/{key}"

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(upload_bytes=upload_bytes),
    )

    assert isinstance(result, FigureCollectionResult)
    assert [fig.figure_id for fig in result.figures] == ["band"]
    assert result.failure_ids == ["dos"]


def test_collect_figures_retries_remote_download_once_before_failing() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.side_effect = [TimeoutError("ssh hiccup"), TimeoutError("ssh still down")]

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(),
    )

    assert result.figures == []
    assert result.failure_ids == ["band"]
    assert fake_session.download.call_count == 2


def test_collect_figures_retries_upload_before_success() -> None:
    fake_session = MagicMock()
    fake_session.path_exists.return_value = True
    fake_session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    fake_session.download.return_value = b"\\x89PNG\\r\\n\\x1a\\n" + b"x" * 64

    attempts = {"count": 0}

    def upload_bytes(data: bytes, key: str) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient oss failure")
        return f"https://oss.example/{key}"

    result = collect_figures_from_session(
        session=fake_session,
        artifact_dir="/share/.matmaster/figures/call-1/artifacts",
        manifest_path="/share/.matmaster/figures/call-1/manifest.json",
        tool_call_id="call-1",
        upload_config=_upload_cfg(upload_bytes=upload_bytes),
    )

    assert [fig.figure_id for fig in result.figures] == ["band"]
    assert result.failure_ids == []
    assert attempts["count"] == 3
```

- [ ] **Step 2: 运行测试，确认 helper 还不存在**

Run:

```bash
uv run pytest tests/matmaster/tools/test_figure_artifacts.py -v
```

Expected: FAIL because `matmaster.tools.figure_artifacts` does not exist yet.

- [ ] **Step 3: 实现纯后端 collector helper**

```python
# matmaster/tools/figure_artifacts.py
from __future__ import annotations

import hashlib
import json
import mimetypes
import posixpath
import time
from dataclasses import dataclass, field

from matmaster.types.figures import FigureDescriptor, FigureManifestEntry, FigureUploadConfig

_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_BYTES = 10 * 1024 * 1024


@dataclass
class FigureCollectionResult:
    figures: list[FigureDescriptor] = field(default_factory=list)
    failure_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_figure_env(workdir: str, tool_call_id: str) -> tuple[str, str]:
    base = posixpath.join(workdir, ".matmaster", "figures", tool_call_id)
    return (
        posixpath.join(base, "artifacts"),
        posixpath.join(base, "manifest.json"),
    )


def collect_figures_from_session(*, session, artifact_dir: str, manifest_path: str, tool_call_id: str, upload_config: FigureUploadConfig) -> FigureCollectionResult:
    if not session.path_exists(manifest_path):
        return FigureCollectionResult()

    out = FigureCollectionResult()
    entries = _load_manifest_entries(
        session=session,
        manifest_path=manifest_path,
        artifact_dir=artifact_dir,
        out=out,
    )
    if entries is None:
        return out

    for entry, resolved in entries:
        try:
            blob = _download_with_retry(session, resolved)
            suffix = _validate_image_blob(resolved, blob)
            sha = hashlib.sha256(blob).hexdigest()
            object_key = (
                f"{upload_config.asset_key_prefix.rstrip('/')}/"
                f"{upload_config.session_id}/{upload_config.task_id}/{tool_call_id}/"
                f"{sha}_{entry.figure_id}{suffix}"
            )
            url = _upload_with_retry(upload_config, blob, object_key)
            out.figures.append(
                FigureDescriptor(
                    figure_id=entry.figure_id,
                    asset_url=url,
                    caption=entry.caption,
                    alt=entry.alt,
                    importance=entry.importance,
                    placement_hint=entry.placement_hint,
                    source_tool_call_id=tool_call_id,
                )
            )
        except Exception:
            out.failure_ids.append(entry.figure_id)

    return out


def _load_manifest_entries(*, session, manifest_path: str, artifact_dir: str, out: FigureCollectionResult) -> list[tuple[FigureManifestEntry, str]] | None:
    try:
        payload = json.loads(session.read_file(manifest_path))
        seen: set[str] = set()
        entries: list[tuple[FigureManifestEntry, str]] = []
        for raw in payload.get("figures", []):
            entry = FigureManifestEntry.model_validate(raw)
            if entry.figure_id in seen:
                raise ValueError(f"duplicate figure_id in manifest: {entry.figure_id}")
            seen.add(entry.figure_id)
            resolved = _resolve_under_artifact_dir(artifact_dir, entry.path)
            entries.append((entry, resolved))
        return entries
    except Exception as exc:
        out.warnings.append(f"invalid_manifest:{exc}")
        return None


def _download_with_retry(session, remote_path: str) -> bytes:
    attempts = 2  # 首次 + 1 次短重试，对齐 spec
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return session.download(remote_path)
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            time.sleep(0.2)
    raise last_exc or RuntimeError("download failed")


def _upload_with_retry(upload_config: FigureUploadConfig, blob: bytes, object_key: str) -> str:
    delays = [0.2, 0.5]  # 共 3 次尝试，指数退避
    last_exc: Exception | None = None
    for idx in range(len(delays) + 1):
        try:
            return upload_config.upload_bytes(blob, object_key)
        except Exception as exc:
            last_exc = exc
            if idx == len(delays):
                break
            time.sleep(delays[idx])
    raise last_exc or RuntimeError("upload failed")
```

实现要点：

- manifest 缺失或非法时，只返回 warning，不抛回答级异常。
- 单张图片下载或上传失败时，只把对应 `figure_id` 记入 `failure_ids`；同一 tool call 中已成功上传的图片照常保留在 `figures` 中。

- [ ] **Step 4: 重新运行 helper 测试**

Run:

```bash
uv run pytest tests/matmaster/tools/test_figure_artifacts.py -v
```

Expected: PASS

- [ ] **Step 5: 提交图片 collector**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_figure_artifacts.py
git commit -m "feat: add figure artifact collector"
```

---

## Task 4: 把 Bash 产图接入 `payload.figures`

**Files:**
- Modify: `matmaster/tools/builtin/bash_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_bash_tool.py`

- [ ] **Step 1: 写失败测试，锁定 env 注入与成功产图的返回形状**

```python
import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext


def test_bash_injects_figure_env_and_returns_payload_figures(monkeypatch) -> None:
    session = MagicMock()
    session.exec_bash.return_value = {
        "output": "done",
        "exit_code": 0,
        "working_dir": "/share",
    }
    session.path_exists.return_value = True
    session.read_file.return_value = (
        '{"figures":[{"figure_id":"band","path":"plots/band.png","caption":"band"}]}'
    )
    session.download.return_value = b"\\x89PNG\\r\\n\\x1a\\n" + b"x" * 64

    state = ToolRunnerState()
    state.set(
        "figure_upload_config",
        FigureUploadConfig(
            session_id="sess-1",
            task_id="task-1",
            asset_key_prefix="matmaster/chat_figures",
            upload_bytes=lambda data, key: f"https://oss.example/{key}",
        ),
    )

    tool = BashTool(session=session, workdir="/share")
    result = asyncio.run(
        tool.execute_with_context(
            {"command": "python render.py"},
            ToolExecutionContext(runner_state=state, tool_call_id="call-band"),
        )
    )

    assert isinstance(result, ToolResult)
    assert result.status == "success"
    assert result.payload["figures"][0]["figure_id"] == "band"

    final_cmd = session.exec_bash.call_args.kwargs["command"]
    assert "ARTIFACT_DIR=" in final_cmd or "export ARTIFACT_DIR=" in final_cmd
    assert "MANIFEST_PATH=" in final_cmd or "export MANIFEST_PATH=" in final_cmd


def test_bash_without_manifest_keeps_legacy_success_string() -> None:
    session = MagicMock()
    session.exec_bash.return_value = {
        "output": "hello",
        "exit_code": 0,
        "working_dir": "/share",
    }
    session.path_exists.return_value = False

    tool = BashTool(session=session, workdir="/share")
    result = asyncio.run(tool.execute({"command": "echo hello"}))

    assert isinstance(result, str)
    assert "hello" in result
```

- [ ] **Step 2: 运行 Bash 聚焦测试，确认它们失败**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py -k "figure_env or legacy_success_string" -v
```

Expected:

- `ToolExecutionContext` 里虽有 `tool_call_id`，但 Bash 还不会注入 `ARTIFACT_DIR` / `MANIFEST_PATH`。
- 成功产图时不会返回 `payload.figures`。

- [ ] **Step 3: 在 BashTool 中接入 figure env 与 collector**

```python
# matmaster/tools/builtin/bash_tool.py
async def execute_with_context(
    self,
    arguments: dict[str, Any],
    exec_ctx: ToolExecutionContext | None,
) -> str | ToolResult:
    figure_cfg = None
    tool_call_id = None
    if exec_ctx is not None:
        tool_call_id = exec_ctx.tool_call_id
        if exec_ctx.runner_state is not None:
            figure_cfg = exec_ctx.runner_state.get("figure_upload_config")

    return await asyncio.to_thread(
        self._execute_with_figure_support,
        arguments,
        figure_cfg,
        tool_call_id,
    )


def _execute_with_figure_support(
    self,
    arguments: dict[str, Any],
    figure_cfg: FigureUploadConfig | None,
    tool_call_id: str | None,
) -> str | ToolResult:
    session = self._require_session()
    command = (arguments.get("command") or "").strip()
    ...
    env = runtime.build_env() if runtime is not None else {}

    artifact_dir = None
    manifest_path = None
    if figure_cfg is not None and tool_call_id and self._workdir is not None:
        artifact_dir, manifest_path = build_figure_env(str(self._workdir), tool_call_id)
        env = {
            **env,
            "ARTIFACT_DIR": artifact_dir,
            "MANIFEST_PATH": manifest_path,
        }
        session.exec_bash(f"mkdir -p {shlex.quote(artifact_dir)}")

    ...
    if exit_code != 0:
        base = ToolResult(status="error", content=obs)
    else:
        base = obs

    if figure_cfg is None or tool_call_id is None or artifact_dir is None or manifest_path is None:
        return base

    collection = collect_figures_from_session(
        session=session,
        artifact_dir=artifact_dir,
        manifest_path=manifest_path,
        tool_call_id=tool_call_id,
        upload_config=figure_cfg,
    )
    if (
        not collection.figures
        and not collection.failure_ids
        and not collection.warnings
    ):
        return base

    content = obs
    if collection.failure_ids:
        content += (
            "\\n[Figure pipeline: "
            f"{len(collection.failure_ids)} failed: {', '.join(collection.failure_ids)}]"
        )
    if collection.warnings:
        content += (
            "\\n[Figure manifest ignored: "
            + "; ".join(collection.warnings)
            + "]"
        )

    return ToolResult(
        status="error" if exit_code != 0 else "success",
        content=content,
        payload={
            "figures": [fig.model_dump(mode="json") for fig in collection.figures]
        },
    )
```

- [ ] **Step 4: 重新运行 Bash 聚焦测试与完整 Bash suite**

Run:

```bash
uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py -k "figure_env or legacy_success_string" -v
uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py -v
```

Expected: PASS

- [ ] **Step 5: 提交 Bash 集成**

```bash
git add matmaster/tools/builtin/bash_tool.py tests/matmaster/tools/builtin/test_bash_tool.py
git commit -m "feat: collect bash-generated response figures"
```

---

## Task 5: 聚合回答级 `response_figures` 并接通 SSE / 回放

**Files:**
- Create: `src/services/response_figures_service.py`
- Modify: `src/services/agent_run_service.py`
- Modify: `matmaster/integration/event_payloads.py`
- Modify: `src/models/chat.py`
- Modify: `tests/matmaster/integration/test_event_payloads.py`
- Modify: `tests/matmaster/services/test_agent_run_stream.py`
- Modify: `tests/test_chat_stream_direct.py`
- Modify: `tests/matmaster/integration/test_events_to_messages.py`

- [ ] **Step 1: 写失败测试，锁定顺序、payload 形状与历史回放**

```python
@pytest.mark.asyncio
async def test_run_agent_injects_figure_upload_config_into_pg_ctx_run_meta() -> None:
    run_result = RunResultEvent(
        source="MatMaster",
        status="completed",
        reason="natural",
        final_content="done",
    )

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        controller = CancellationController()
        await svc.run_agent(
            session_id="sess-1",
            user_prompt="make a plot",
            send_cb=lambda payload: None,
            cancel_token=controller.token,
            mode="direct",
            task_id="task-1",
        )

    figure_cfg = svc._test_fake_exp.last_ctx.run_meta["figure_upload_config"]
    assert figure_cfg.session_id == "sess-1"
    assert figure_cfg.task_id == "task-1"
    assert callable(figure_cfg.upload_bytes)
```

```python
def test_response_figures_payload_maps_to_public_content() -> None:
    payload = {
        "type": "response_figures",
        "source": "System",
        "figures": [
            {
                "figure_id": "band",
                "asset_url": "https://oss.example/band.png",
                "caption": "band",
                "importance": "primary",
                "placement_hint": "sidebar_only",
                "source_tool_call_id": "call-band",
            }
        ],
    }

    assert _public_content_for_event("response_figures", payload) == {
        "figures": payload["figures"]
    }
```

```python
@pytest.mark.asyncio
async def test_run_agent_emits_response_figures_before_run_result() -> None:
    tool_result = ToolResultEvent(
        source="MatMaster",
        call_id="call-band",
        tool_name="Bash",
        result="done",
        payload={
            "figures": [
                {
                    "figure_id": "band",
                    "asset_url": "https://oss.example/band.png",
                    "caption": "band",
                    "importance": "primary",
                    "placement_hint": "sidebar_only",
                    "source_tool_call_id": "call-band",
                }
            ]
        },
    )
    run_result = RunResultEvent(
        source="MatMaster",
        status="completed",
        reason="natural",
        final_content="answer",
    )

    async with _patched_service([tool_result, run_result]) as (svc, sse_events, _):
        controller = CancellationController()
        send_payloads = []

        async def send_cb(payload):
            send_payloads.append(payload)

        await svc.run_agent(
            session_id="sess-1",
            user_prompt="show band structure",
            send_cb=send_cb,
            cancel_token=controller.token,
            mode="direct",
            task_id="task-1",
        )

    sse_types = [getattr(evt, "type", None) for evt in sse_events]
    assert "response_figures" in sse_types
    assert sse_types.index("response_figures") < sse_types.index("run_result")
```

```python
def test_replay_keeps_response_figures_but_still_dedupes_run_result_after_response():
    events_service.get_session_events.return_value = [
        {"source": "MatMaster", "type": "response", "content": "answer", "task_id": "task-1", "spawn_id": None},
        {"source": "System", "type": "response_figures", "content": {"figures": [{"figure_id": "band", "asset_url": "https://oss.example/band.png", "caption": "band"}]}, "task_id": "task-1", "spawn_id": None},
        {"source": "MatMaster", "type": "run_result", "content": "answer", "task_id": "task-1", "spawn_id": None},
    ]
    ...
    assert [frame["type"] for frame in frames] == ["status", "response", "response_figures"]
```

```python
def test_response_figures_does_not_enter_dialog_history():
    events = [
        _user_event("q"),
        {"source": "System", "type": "response_figures", "content": {"figures": []}},
        _response_event("answer"),
    ]

    result = ChatHistoryConverter.events_to_messages(events)
    assert len(result) == 2
    assert isinstance(result[-1], AssistantMessage)
    assert result[-1].content == "answer"
```

- [ ] **Step 2: 运行协议与回放测试，确认它们失败**

Run:

```bash
uv run pytest tests/matmaster/integration/test_event_payloads.py tests/matmaster/services/test_agent_run_stream.py tests/test_chat_stream_direct.py tests/matmaster/integration/test_events_to_messages.py -k "response_figures" -v
```

Expected:

- `response_figures` 还没有显式 payload 映射。
- `AgentRunService` 还没有把 `figure_upload_config` 注入 `pg_ctx.run_meta`。
- `AgentRunService` 不会在 `run_result` 前发事件。
- 历史回放中没有这个新事件类型的稳定测试保证。

- [ ] **Step 3: 实现回答级聚合器并在 `run_result` 前发射事件**

```python
# src/services/response_figures_service.py
from __future__ import annotations

from matmaster.types.events import ResponseFiguresEvent, ToolResultEvent
from matmaster.types.figures import FigureDescriptor


class ResponseFiguresAccumulator:
    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._ordered: list[FigureDescriptor] = []

    def add_tool_result(self, event: ToolResultEvent) -> None:
        if event.spawn_id is not None:
            return

        # 保持 tool_result 到达顺序；同一个 tool_result 内再保持 payload.figures 原顺序。
        raw_items = (event.payload or {}).get("figures") or []
        for raw in raw_items:
            figure = FigureDescriptor.model_validate(raw)
            if figure.figure_id in self._seen_ids:
                continue
            self._seen_ids.add(figure.figure_id)
            self._ordered.append(figure)

    def build_event(self) -> ResponseFiguresEvent | None:
        if not self._ordered:
            return None
        return ResponseFiguresEvent(source="System", figures=list(self._ordered))
```

```python
# src/services/agent_run_service.py
from matmaster.types.figures import FigureUploadConfig
from src.dao.oss_io import upload_bytes_to_oss
from src.services.response_figures_service import ResponseFiguresAccumulator


def _build_figure_upload_config(*, session_id: str, task_id: str) -> FigureUploadConfig:
    return FigureUploadConfig(
        session_id=session_id,
        task_id=task_id,
        asset_key_prefix="matmaster/chat_figures",
        upload_bytes=upload_bytes_to_oss,
    )


...
# 在 run_agent() 内、fanout 与 exp 都准备好之后创建。
figure_accumulator = ResponseFiguresAccumulator()

...
pg_ctx = pg_ctx.model_copy(
    update={
        'run_meta': {
            **pg_ctx.run_meta,
            'figure_upload_config': _build_figure_upload_config(
                session_id=session_id,
                task_id=task_id,
            ),
        }
    }
)

...
async for event in stream:
    if isinstance(event, ToolResultEvent):
        figure_accumulator.add_tool_result(event)

    if isinstance(event, RunResultEvent):
        response_figures_event = figure_accumulator.build_event()
        if response_figures_event is not None:
            await fanout.dispatch(response_figures_event)

    await fanout.dispatch(event)
```

```python
# matmaster/integration/event_payloads.py
if event_type == "response_figures":
    return {
        "figures": payload.get("figures") or [],
    }
```

```python
# src/models/chat.py
# 在 ag-ui 协议文档中补充：
# response_figures：回答级图片绑定事件；content.figures 为已上传图片列表，
# 顶层仍带 session_id/task_id/invocation_id/spawn_id。
```

- [ ] **Step 4: 重新运行聚焦测试，再跑相关完整 suite**

Run:

```bash
uv run pytest tests/matmaster/integration/test_event_payloads.py tests/matmaster/services/test_agent_run_stream.py tests/test_chat_stream_direct.py tests/matmaster/integration/test_events_to_messages.py -k "response_figures" -v
uv run pytest tests/matmaster/integration/test_event_payloads.py tests/matmaster/services/test_agent_run_stream.py tests/test_chat_stream_direct.py tests/matmaster/integration/test_events_to_messages.py -v
```

Expected: PASS

- [ ] **Step 5: 跑这次功能的最小回归矩阵**

Run:

```bash
uv run pytest \
  tests/matmaster/types/test_figures.py \
  tests/matmaster/types/test_events.py \
  tests/matmaster/types/test_tool_spec.py \
  tests/matmaster/core/test_full_tool_runner.py \
  tests/matmaster/tools/test_figure_artifacts.py \
  tests/matmaster/tools/builtin/test_bash_tool.py \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/services/test_agent_run_stream.py \
  tests/test_chat_stream_direct.py \
  tests/matmaster/integration/test_events_to_messages.py -v
```

Expected: PASS

- [ ] **Step 6: 提交回答级聚合与协议更新**

```bash
git add src/services/response_figures_service.py src/services/agent_run_service.py matmaster/integration/event_payloads.py src/models/chat.py tests/matmaster/integration/test_event_payloads.py tests/matmaster/services/test_agent_run_stream.py tests/test_chat_stream_direct.py tests/matmaster/integration/test_events_to_messages.py
git commit -m "feat: emit response figures for chat replies"
```

---

## Spec Coverage Check

实施前逐条确认与 spec 对齐：

1. manifest-only：由 `collect_figures_from_session()` 只认 `MANIFEST_PATH` 保证。
2. 本地 + Bohrium：统一通过 `Session.read_file()` / `Session.download()` 读取，避免分叉两套 collector。
3. 同步上传：Bash tool 在返回前完成 upload 并写入 `payload.figures`。
4. `response_figures` 在 `run_result` 前发一次：由 `ResponseFiguresAccumulator` + `AgentRunService` 顺序保证。
5. 历史恢复：依赖 persisted `response_figures` + `stream_service` replay，不把图片写回 `AssistantMessage.content`。
6. 子 agent 不合并：聚合器显式跳过 `spawn_id is not None` 的 tool result。
7. 安全边界：路径穿越、格式白名单、10 MB 上限都在 collector 中锁定。
8. 重试策略：远端下载 1 次短重试、OSS 上传 2 次指数退避重试，由 collector helper 统一实现。

## Execution Notes

1. 本计划只覆盖当前仓库，因为项目约束禁止直接改同级前端仓库。
2. 后端交付完成后，下游消费者需要在其仓库里做两件事：
   - Web：把 `response_figures.content.figures` 绑定到侧边栏卡片。
   - PDF：按 `importance` 把 `primary` 图片附到 Figures 附录。
3. 若后续要给非 Bash 工具接入同一能力，优先复用 `matmaster/tools/figure_artifacts.py`，不要复制 manifest 解析逻辑。
