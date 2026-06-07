# Provider 聚合核心层（阶段二）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 LLM provider 层从「config model 直接生成请求 + if/elif transport 分支 + bedrock/Claude 方言/routes 表」重构为「纯数据 profile + dispatch 表 + `Transport` 基类/`ChatCompletionsTransport` 子类」的聚合核心，一并删除 bedrock、litellm-Claude、prompt cache、routes 表、`llm_override` 旁路，净代码量下降，为阶段三 native transport 搭好接缝。

**Architecture:** 三轴模型严格分离——(A) `LLMProvider` Protocol 是 kernel 唯一门面；(B) `transport` 字符串是 dispatch 标签；(C) `Transport` 基类是实现复用脚手架（生命周期 + 4 个 property + seam 声明），不自满足 Protocol，由具体子类 `ChatCompletionsTransport` 补齐 `chat`/`chat_stream`。factory 用 `_TRANSPORT_BUILDERS` 字典查表构造（→ `LLMProvider`），未命中 fail-fast。config 层只校验配置内部引用，transport tag 合法性下沉到 factory dispatch（避免环形 import）。

**Tech Stack:** Python ≥3.10, Pydantic v2, openai AsyncOpenAI + httpx, pytest（仓库 uv 环境）。

**关键命令前缀（全程用 uv 跑测试）：**
- 单测：`uv run pytest <path> -v`
- 全量：`uv run pytest -q`

---

## 设计澄清（实施前必读，落实施时不得偏离）

这些是 spec 文字在落地时需要锁定的取舍，已逐条定夺，实施时照此执行：

1. **`resolve(...)` 保留 `default_key` 形参。** spec §5.2 的 `resolve(self, model_override)` 代码片段省略了 agent 级默认，但 §3.4 只点名删除 `llm_override`，未提删 agent 默认（`load_agents_general_llm` / `_get_agent_default_llm` 仍在用）。最终签名：
   `def resolve(self, *, model_override: str | None = None, default_key: str | None = None) -> ResolvedModel`，键解析 `key = model_override or default_key or self.default`。
2. **`ChatCompletionsTransport` 持标量、不持 profile 对象。** build_kwargs 读 `self._reasoning_effort` / `self._reasoning_summary` 等实例标量（由 dispatch builder 从 profile 平铺字段传入），不在 transport 内 import config 类型。spec §4.3「读 profile 平铺字段」描述的是数据血缘，stage 3「transport 读 profile 对象」的泛化按 spec §11 延后。
3. **BYOK 的 `extra_body` 黑盒透传** 由 transport 的 `extra_body` 形参承载，在 `build_kwargs` 内合并进请求体（用户 key 优先）。旧 `_merge_byok_extra_kwargs` helper 删除（净代码下降），合并逻辑内联到 `build_kwargs`。
4. **`LLMProviderBundle.model_route` 源** 改为 `resolved.profile_key`（routes 删除后，对外标识即 profile key；BYOK 仍为 `byok:{cred}`）。`LLMProviderBundle.model_family` 字段删除（全仓无功能消费者，见 §3.3）。`provider` 字段类型由 `ChatCompletionsProvider | BedrockProvider` 改为 `LLMProvider`（Protocol）。
5. **绿点边界。** 这是「推翻旧 schema、无兼容兜底」的原子替换，中途无法做到每个 task 都全绿。Phase 1（新 transport）结束全绿；Phase 2 是原子替换，内部 task 允许全量套件红，**仅 Phase 2 最后一个 task 恢复全绿**；Phase 3 删除后全绿。每个 task 仍各自 commit，phase 末尾的 task 跑全量套件作为绿点。
6. **测试纪律。** 本阶段不是纯删死代码，而是引入新聚合核心，故按 spec §10 为新 seam（`build_kwargs`/`normalize_response`/`normalize_stream`/dispatch/`resolve`）写单测、删除失去消费者的旧测、改造签名变化的旧测。净代码量整体下降（删 bedrock 602 行 + prompt cache ~160 行 + routes/旧 schema 抵消新增）。

---

## 文件结构（改动地图）

**新建：**
- `matmaster/providers/transport.py` — `Transport` 基类（轴 C）
- `matmaster/providers/transports/__init__.py`
- `matmaster/providers/transports/chat_completions.py` — `ChatCompletionsTransport(Transport)`，纯 openai
- `tests/matmaster/providers/test_transport_base.py` — 基类 property/生命周期单测
- `tests/matmaster/providers/test_chat_completions_transport_build_kwargs.py` — `build_kwargs` 单测
- `tests/matmaster/providers/test_chat_completions_transport_normalize.py` — `normalize_response`/`normalize_stream` 单测

**全量重写：**
- `matmaster/config/llm.py` — 3 个 Pydantic 模型 + `ResolvedModel` NamedTuple，纯数据 profile
- `matmaster/providers/llm_factory.py` — dispatch 表 + build
- `config/llm_config.yaml` — `providers:` 段 + 重命名 profile key，删 routes/Claude/bedrock
- `tests/matmaster/config/test_llm.py` — 新 schema + `resolve`
- `tests/matmaster/providers/test_llm_factory.py` — dispatch 驱动
- `tests/matmaster/providers/test_byok_provider.py` — 合成 profile/固定 transport

**删除：**
- `matmaster/providers/bedrock_provider.py`（602 行）
- `matmaster/providers/chat_completions_provider.py`（迁入 transports/chat_completions.py 后删）
- `tests/matmaster/providers/test_bedrock_provider.py`
- `tests/matmaster/providers/test_chat_completions_provider_prompt_cache.py`

**修改（重命名/签名收敛）：**
- `matmaster/providers/__init__.py`
- `matmaster/config/__init__.py`
- `src/services/agent_run_service.py`、`src/services/image_input_service.py`、`src/services/stream_service.py`
- `src/apis/chat_api.py`、`src/worker/agent_worker.py`、`src/utils/feishu_notifier.py`、`src/models/chat.py`
- `matmaster/devshell/cli.py`、`matmaster/devshell/debug_run.py`、`matmaster/devshell/repl.py`、`matmaster/devshell/runner.py`
- 多个 provider 测试文件（`ChatCompletionsProvider` → `ChatCompletionsTransport` 重命名 + import 路径）
- `tests/matmaster/config/test_loader.py`、`tests/services/test_image_input_service.py`、若干 integration 测试 patch 路径

---

# Phase 1 — Transport 核心（新文件，结束全绿）

本 phase 只新增文件，不触碰旧 `chat_completions_provider.py` / `llm_factory.py` / config，旧链路保持可用，套件保持全绿。

## Task 1: `Transport` 基类

**Files:**
- Create: `matmaster/providers/transport.py`
- Test: `tests/matmaster/providers/test_transport_base.py`

- [ ] **Step 1: 写失败测试**

`tests/matmaster/providers/test_transport_base.py`:

```python
"""Transport 基类：property 回退 + 生命周期脚手架 + seam 抛 NotImplementedError。

用一个最小具体子类（只实现 _open_client/_close_client）验证基类行为；
基类本身不实现 chat/chat_stream，故不自满足 LLMProvider Protocol。
"""

from __future__ import annotations

import pytest

from matmaster.providers.transport import Transport


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False


class _MiniTransport(Transport):
    """只补生命周期钩子，用于测基类脚手架。"""

    async def _open_client(self) -> _FakeClient:
        return _FakeClient()

    async def _close_client(self, client: _FakeClient) -> None:
        client.closed = True


def test_stream_timeout_falls_back_to_timeout() -> None:
    t = _MiniTransport(timeout=300)
    assert t.stream_timeout == 300
    assert t.stream_idle_timeout == 300


def test_stream_timeout_uses_explicit_values() -> None:
    t = _MiniTransport(
        timeout=300, stream_timeout=120, stream_idle_timeout=60
    )
    assert t.stream_timeout == 120
    assert t.stream_idle_timeout == 60


def test_retry_properties() -> None:
    t = _MiniTransport(timeout=10, max_retries=5, retry_delay=2.0)
    assert t.max_retries == 5
    assert t.retry_delay == 2.0


def test_ensure_client_requires_context_manager() -> None:
    t = _MiniTransport(timeout=10)
    with pytest.raises(RuntimeError, match="async context manager"):
        t._ensure_client()


@pytest.mark.asyncio
async def test_lifecycle_open_and_close() -> None:
    t = _MiniTransport(timeout=10)
    async with t as entered:
        assert entered is t
        client = t._ensure_client()
        assert client.closed is False
    assert t._client is None
    assert client.closed is True


@pytest.mark.asyncio
async def test_reentrant_context_manager_opens_once() -> None:
    t = _MiniTransport(timeout=10)
    async with t:
        first = t._ensure_client()
        async with t:
            assert t._ensure_client() is first
        # 内层退出不应关闭 client（enter_count 仍 > 0）
        assert t._ensure_client() is first
    assert t._client is None


def test_base_seams_raise_not_implemented() -> None:
    t = _MiniTransport(timeout=10)
    with pytest.raises(NotImplementedError):
        t.build_kwargs([], None)
    with pytest.raises(NotImplementedError):
        t.convert_messages([])
    with pytest.raises(NotImplementedError):
        t.normalize_response(object())
    with pytest.raises(NotImplementedError):
        t.classify_error(Exception("x"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/providers/test_transport_base.py -v`
Expected: FAIL（`ModuleNotFoundError: matmaster.providers.transport`）

- [ ] **Step 3: 实现基类**

`matmaster/providers/transport.py`:

```python
"""Transport 基类（轴 C：实现复用脚手架）。

只收敛真正共享的部分：timeout/retry property + 生命周期骨架 + seam 声明。
**本基类不实现 chat/chat_stream，因此不自满足 LLMProvider Protocol；满足 Protocol
的是具体子类。** chat/chat_stream 不进基类：实际 API 调用与流式迭代在各 wire 协议间
差异过大，硬模板化会变坏抽象。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from matmaster.types.messages import LLMResponse, StreamChunk


class Transport:
    def __init__(
        self,
        *,
        timeout: float,
        stream_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self._timeout = timeout
        self._stream_timeout = stream_timeout
        self._stream_idle_timeout = stream_idle_timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._client: Any = None
        self._enter_count: int = 0

    # ── property（消除子类内重复实现）─────────────────────────────────
    @property
    def stream_timeout(self) -> float:
        return self._stream_timeout if self._stream_timeout is not None else self._timeout

    @property
    def stream_idle_timeout(self) -> float:
        return (
            self._stream_idle_timeout
            if self._stream_idle_timeout is not None
            else self._timeout
        )

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retry_delay(self) -> float:
        return self._retry_delay

    # ── 生命周期脚手架（client 创建/关闭由子类钩子提供）──────────────
    async def __aenter__(self) -> "Transport":
        self._enter_count += 1
        if self._client is None:
            self._client = await self._open_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        self._enter_count -= 1
        if self._enter_count > 0:
            return
        if self._client is not None:
            await self._close_client(self._client)
            self._client = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            raise RuntimeError(
                "Transport must be used as async context manager: 'async with transport:'"
            )
        return self._client

    # ── 子类覆盖契约（seam）───────────────────────────────────────────
    async def _open_client(self) -> Any:
        raise NotImplementedError

    async def _close_client(self, client: Any) -> None:
        raise NotImplementedError

    def build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """语义配置 → 该协议的请求 kwargs。"""
        raise NotImplementedError

    def convert_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """canonical → wire（阶段二 chat_completions = identity 直通）。"""
        raise NotImplementedError

    def normalize_response(self, raw: Any) -> LLMResponse:
        raise NotImplementedError

    def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    def classify_error(self, exc: Exception) -> Any:
        """SDK 异常 → LLMError(retryable, error_category)；未知异常返回 None（由调用方原样抛出）。"""
        raise NotImplementedError
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/providers/test_transport_base.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add matmaster/providers/transport.py tests/matmaster/providers/test_transport_base.py
git commit -m "feat(providers): add Transport base scaffold (lifecycle + properties + seams)"
```

---

## Task 2: `ChatCompletionsTransport` 子类（端口 + 新 seam）

把现有 `chat_completions_provider.py` 的 chat/chat_stream/usage/stream-tool-call 逻辑迁入新文件，**剥离全部 prompt cache**，并把请求 kwargs 生成抽成 `build_kwargs`、响应/流/错误抽成可覆盖方法。

**Files:**
- Create: `matmaster/providers/transports/__init__.py`
- Create: `matmaster/providers/transports/chat_completions.py`
- Test: `tests/matmaster/providers/test_chat_completions_transport_build_kwargs.py`
- Test: `tests/matmaster/providers/test_chat_completions_transport_normalize.py`

- [ ] **Step 1: 写 `build_kwargs` 失败测试**

`tests/matmaster/providers/test_chat_completions_transport_build_kwargs.py`:

```python
"""ChatCompletionsTransport.build_kwargs：profile 平铺 reasoning 字段 → openai kwargs。

纯函数式（不建 client）。验证 reasoning_effort → 顶层、reasoning_summary → extra_body.reasoning、
BYOK extra_body 黑盒透传（用户优先）、tools/tool_choice/stream 形状。
"""

from __future__ import annotations

from matmaster.providers.transports.chat_completions import ChatCompletionsTransport


def _t(**kw):
    base = dict(model="m", api_key="sk", timeout=10)
    base.update(kw)
    return ChatCompletionsTransport(**base)


def test_minimal_kwargs() -> None:
    t = _t(temperature=0.7)
    kw = t.build_kwargs([{"role": "user", "content": "hi"}], None)
    assert kw["model"] == "m"
    assert kw["temperature"] == 0.7
    assert kw["messages"] == [{"role": "user", "content": "hi"}]
    assert "reasoning_effort" not in kw
    assert "extra_body" not in kw
    assert "stream" not in kw


def test_reasoning_effort_goes_top_level() -> None:
    t = _t(reasoning_effort="High")
    kw = t.build_kwargs([], None)
    assert kw["reasoning_effort"] == "high"  # lowercased/stripped
    assert "extra_body" not in kw


def test_reasoning_summary_goes_extra_body_with_effort() -> None:
    t = _t(reasoning_effort="xhigh", reasoning_summary="detailed")
    kw = t.build_kwargs([], None)
    assert kw["reasoning_effort"] == "xhigh"
    assert kw["extra_body"] == {"reasoning": {"summary": "detailed", "effort": "xhigh"}}


def test_reasoning_summary_without_effort() -> None:
    t = _t(reasoning_summary="concise")
    kw = t.build_kwargs([], None)
    assert kw["extra_body"] == {"reasoning": {"summary": "concise"}}
    assert "reasoning_effort" not in kw


def test_max_tokens_and_tools_and_tool_choice() -> None:
    t = _t(max_tokens=128)
    tools = [{"type": "function", "function": {"name": "f"}}]
    kw = t.build_kwargs([], tools, tool_choice="none")
    assert kw["max_tokens"] == 128
    assert kw["tools"] == tools
    assert kw["tool_choice"] == "none"


def test_stream_sets_stream_and_include_usage() -> None:
    t = _t()
    kw = t.build_kwargs([], None, stream=True)
    assert kw["stream"] is True
    assert kw["stream_options"] == {"include_usage": True}
    # 流式不传 tool_choice
    assert "tool_choice" not in kw


def test_byok_extra_body_passthrough_user_wins() -> None:
    # 同时设 reasoning_summary（产 extra_body.reasoning）与 BYOK extra_body（含同名 key 用户优先）
    t = _t(reasoning_summary="auto", extra_body={"enable_thinking": True, "reasoning": {"summary": "x"}})
    kw = t.build_kwargs([], None)
    # 用户 extra_body 覆盖同名 reasoning
    assert kw["extra_body"]["reasoning"] == {"summary": "x"}
    assert kw["extra_body"]["enable_thinking"] is True


def test_convert_messages_is_identity() -> None:
    t = _t()
    msgs = [{"role": "user", "content": "hi"}]
    assert t.convert_messages(msgs) is msgs
```

- [ ] **Step 2: 写 `normalize_response`/`normalize_stream` 失败测试**

`tests/matmaster/providers/test_chat_completions_transport_normalize.py`:

```python
"""ChatCompletionsTransport.normalize_response / normalize_stream：保留现有返回形状。

含 usage scalar dict + usage_vendor（provider-native detail）。用轻量假对象模拟 SDK 返回。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import LLMResponse, StreamChunk


def _t():
    return ChatCompletionsTransport(model="m", api_key="sk", timeout=10)


async def _aiter(items):
    for it in items:
        yield it


def _usage(prompt=10, completion=5, total=15):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        prompt_tokens_details=None,
        completion_tokens_details=None,
        model_dump=lambda **_: {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        },
    )


def test_normalize_response_text_and_usage() -> None:
    message = SimpleNamespace(content="hello", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    raw = SimpleNamespace(choices=[choice], usage=_usage())
    out = _t().normalize_response(raw)
    assert isinstance(out, LLMResponse)
    assert out.content == "hello"
    assert out.tool_calls is None
    assert out.finish_reason == "stop"
    assert out.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert out.usage_vendor == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_normalize_response_tool_calls() -> None:
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="search", arguments='{"q": "x"}'),
    )
    message = SimpleNamespace(content=None, tool_calls=[tc])
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    raw = SimpleNamespace(choices=[choice], usage=None)
    out = _t().normalize_response(raw)
    assert out.tool_calls is not None
    assert out.tool_calls[0].id == "call_1"
    assert out.tool_calls[0].name == "search"
    assert out.tool_calls[0].arguments == {"q": "x"}


@pytest.mark.asyncio
async def test_normalize_stream_yields_content_then_usage() -> None:
    delta = SimpleNamespace(content="hi", reasoning_content=None, tool_calls=None)
    chunk1 = SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)
    chunk2 = SimpleNamespace(choices=[], usage=_usage())
    out = []
    async for sc in _t().normalize_stream(_aiter([chunk1, chunk2])):
        out.append(sc)
    assert all(isinstance(x, StreamChunk) for x in out)
    assert out[0].content == "hi"
    assert out[-1].usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert out[-1].usage_vendor is not None
```

- [ ] **Step 3: 跑两个测试文件确认失败**

Run: `uv run pytest tests/matmaster/providers/test_chat_completions_transport_build_kwargs.py tests/matmaster/providers/test_chat_completions_transport_normalize.py -v`
Expected: FAIL（`ModuleNotFoundError: matmaster.providers.transports.chat_completions`）

- [ ] **Step 4: 创建 transports 包**

`matmaster/providers/transports/__init__.py`:

```python
"""matmaster.providers.transports -- 具体 wire 协议 transport 子类。"""
```

- [ ] **Step 5: 实现 `ChatCompletionsTransport`**

迁移规则：把 `chat_completions_provider.py` 中以下 **module 级 helper 原样搬入**本文件（它们与 prompt cache 无关）：
`_StreamToolCallState`、`_is_complete_json_document`、`_find_existing_tool_call_by_id`、`_reconcile_duplicate_tool_call_arguments`、`_normalize_duplicate_id_delta`、`_should_split_stream_tool_call`、`_dump_usage_detail`、`_openai_usage_to_vendor_dict`、`_extract_cached_tokens`、`_extract_cache_write_tokens`、`_extract_reasoning_tokens`、`_openai_usage_to_scalar_dict`、`_is_non_retryable_tool_protocol_bad_request`、`_is_non_retryable_content_shape_bad_request`。
**不要搬**任何 prompt cache helper（`AnthropicPromptCacheOptions`、`_CacheTarget`、`_add_text_content_cache_control`、`_add_tool_message_cache_control`、`_tool_call_ids`、`_latest_completed_tool_group_tail`、`_message_text_size`、`_select_flexible_cache_target`、`_select_anthropic_cache_targets`）—— 这些随 opus_global 删除。

`matmaster/providers/transports/chat_completions.py`（完整内容，helper 段以注释占位，落实施时从旧文件原样粘贴）:

```python
"""ChatCompletionsTransport：纯 openai 风格 chat completions wire 协议。

由原 ChatCompletionsProvider 重构而来：从 Transport 基类拿 property/生命周期脚手架；
请求 kwargs 由 build_kwargs 生成；消息经 convert_messages（identity）；响应/流经
normalize_response/normalize_stream；异常经 classify_error。无任何 anthropic 方言、
无 prompt cache、无 thinking/output_config。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import openai

from matmaster.providers.transport import Transport
from matmaster.types.errors import LLMError
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
    parse_tool_arguments,
)

logger = logging.getLogger(__name__)


# ── 从旧 chat_completions_provider.py 原样迁入的 module 级 helper ──────────────
# （粘贴：_StreamToolCallState, _is_complete_json_document,
#   _find_existing_tool_call_by_id, _reconcile_duplicate_tool_call_arguments,
#   _normalize_duplicate_id_delta, _should_split_stream_tool_call,
#   _dump_usage_detail, _openai_usage_to_vendor_dict, _extract_cached_tokens,
#   _extract_cache_write_tokens, _extract_reasoning_tokens,
#   _openai_usage_to_scalar_dict, _is_non_retryable_tool_protocol_bad_request,
#   _is_non_retryable_content_shape_bad_request）
# 注意：这些 helper 用到 json，已在上方 import；不要迁入任何 prompt cache helper。


class ChatCompletionsTransport(Transport):
    """LLMProvider 子类，纯 openai chat completions。满足 LLMProvider Protocol。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        reasoning_summary: str | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout: float = 300.0,
        stream_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        super().__init__(
            timeout=timeout,
            stream_timeout=stream_timeout,
            stream_idle_timeout=stream_idle_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._reasoning_summary = reasoning_summary
        self._extra_body = extra_body

    # ── 生命周期钩子 ──────────────────────────────────────────────────
    async def _open_client(self) -> openai.AsyncOpenAI:
        import httpx

        read_t = float(max(self.stream_idle_timeout, self.stream_timeout) + 10)
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=read_t, write=30.0, pool=15.0)
        )
        return openai.AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def _close_client(self, client: openai.AsyncOpenAI) -> None:
        await client.close()

    # ── seam 实现 ─────────────────────────────────────────────────────
    def convert_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        # 阶段二 identity 直通（消息上游已由 to_api_dict() 转 dict）。
        # 这是为 stage 3 立的接缝点：stage 3 引 IR 时此处填真实转换。
        return messages

    def build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self.convert_messages(messages),
            "temperature": self._temperature,
        }
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if tools:
            kwargs["tools"] = tools

        effort = (self._reasoning_effort or "").strip().lower()
        extra_body: dict[str, Any] = {}
        if effort:
            kwargs["reasoning_effort"] = effort
        if self._reasoning_summary:
            reasoning: dict[str, str] = {"summary": self._reasoning_summary}
            if effort:
                reasoning["effort"] = effort
            extra_body["reasoning"] = reasoning
        if self._extra_body:
            extra_body.update(self._extra_body)  # BYOK 黑盒透传，用户优先
        if extra_body:
            kwargs["extra_body"] = extra_body

        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
        elif tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return kwargs

    def classify_error(self, exc: Exception) -> LLMError | None:
        """已知 SDK 异常 → LLMError；LLMError 原样穿透（返回 None 让调用方重抛）；未知 → None。"""
        import httpx as _httpx

        if isinstance(exc, LLMError):
            return None
        if isinstance(exc, openai.APITimeoutError):
            return LLMError(str(exc), retryable=True, error_category="timeout")
        if isinstance(exc, openai.APIConnectionError):
            return LLMError(str(exc), retryable=True, error_category="connection")
        if isinstance(exc, openai.RateLimitError):
            return LLMError(str(exc), retryable=True, error_category="rate_limit")
        if isinstance(exc, openai.InternalServerError):
            return LLMError(str(exc), retryable=True, error_category="server")
        if isinstance(exc, _httpx.ReadTimeout):
            return LLMError(str(exc), retryable=True, error_category="timeout")
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return LLMError(str(exc), retryable=False, error_category="auth")
        if isinstance(exc, openai.BadRequestError):
            err_str = str(exc)
            err_text = err_str.lower()
            if "context" in err_text and ("length" in err_text or "token" in err_text):
                return LLMError(str(exc), retryable=False, error_category="context_overflow")
            if _is_non_retryable_tool_protocol_bad_request(
                err_str
            ) or _is_non_retryable_content_shape_bad_request(err_str):
                return LLMError(str(exc), retryable=False, error_category="bad_request")
            return LLMError(str(exc), retryable=True, error_category="bad_request")
        return None

    @staticmethod
    def _normalize_stream_tool_call_deltas(
        raw_deltas: list[dict[str, Any]],
        active_calls: dict[int, "_StreamToolCallState"],
        next_output_index: int,
    ) -> tuple[list[dict[str, Any]], int]:
        # 原样迁自 ChatCompletionsProvider._normalize_stream_tool_call_deltas。
        ...  # 落实施：从旧文件粘贴方法体（含 duplicate-id 处理、index collision 重写）

    def normalize_response(self, raw: Any) -> LLMResponse:
        choice = raw.choices[0]
        message = choice.message
        tool_calls: list[ToolCallData] | None = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                args = parse_tool_arguments(tc.function.arguments or "")
                tool_calls.append(
                    ToolCallData(id=tc.id, name=tc.function.name, arguments=args)
                )
        usage: dict[str, int] = {}
        usage_vendor: dict[str, Any] | None = None
        if raw.usage:
            usage = _openai_usage_to_scalar_dict(raw.usage)
            usage_vendor = _openai_usage_to_vendor_dict(raw.usage)
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            usage_vendor=usage_vendor,
        )

    async def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        last_chunk_usage: dict[str, int] | None = None
        last_chunk_usage_vendor: dict[str, Any] | None = None
        active_tool_calls: dict[int, _StreamToolCallState] = {}
        next_tool_call_index = 0

        async for chunk in raw_iter:
            usage = getattr(chunk, "usage", None)
            if (
                isinstance(getattr(usage, "prompt_tokens", None), int)
                and isinstance(getattr(usage, "completion_tokens", None), int)
                and isinstance(getattr(usage, "total_tokens", None), int)
            ):
                last_chunk_usage = _openai_usage_to_scalar_dict(usage)
                last_chunk_usage_vendor = _openai_usage_to_vendor_dict(usage)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason
            reasoning_content = getattr(delta, "reasoning_content", None)

            tool_call_deltas: list[dict[str, Any]] | None = None
            if delta.tool_calls:
                raw_tool_call_deltas: list[dict[str, Any]] = []
                for tc_delta in delta.tool_calls:
                    d: dict[str, Any] = {"index": tc_delta.index}
                    if tc_delta.id:
                        d["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            d["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            d["arguments"] = tc_delta.function.arguments
                    raw_tool_call_deltas.append(d)
                tool_call_deltas, next_tool_call_index = (
                    self._normalize_stream_tool_call_deltas(
                        raw_tool_call_deltas, active_tool_calls, next_tool_call_index
                    )
                )

            yield StreamChunk(
                content=delta.content,
                reasoning_content=reasoning_content,
                tool_call_deltas=tool_call_deltas,
                finish_reason=finish_reason,
            )

        if last_chunk_usage is not None:
            yield StreamChunk(
                usage=last_chunk_usage, usage_vendor=last_chunk_usage_vendor
            )

    # ── Protocol 方法（子类补齐 chat/chat_stream）─────────────────────
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools, tool_choice=tool_choice)
        response = await client.chat.completions.create(**kwargs)
        return self.normalize_response(response)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools, stream=True)
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            raw = await client.chat.completions.create(**kwargs)
            async for sc in self.normalize_stream(raw):
                yield sc
        except Exception as exc:  # noqa: BLE001 — 经 classify_error 收敛
            err = self.classify_error(exc)
            if err is not None:
                raise err from exc
            raise
```

> **粘贴注意：** `_normalize_stream_tool_call_deltas` 方法体与 14 个 module helper 是「逐行粘贴」自旧文件，不改逻辑。`copy` import 仅 prompt cache 用，迁入后**不需要** `import copy`。

- [ ] **Step 6: 跑新 seam 测试 + 确认通过**

Run: `uv run pytest tests/matmaster/providers/test_chat_completions_transport_build_kwargs.py tests/matmaster/providers/test_chat_completions_transport_normalize.py -v`
Expected: PASS

- [ ] **Step 7: Protocol 自满足回归**

Run（确认子类满足 Protocol）:
```bash
uv run python -c "from matmaster.providers.transports.chat_completions import ChatCompletionsTransport; from matmaster.types.llm_provider import LLMProvider; t=ChatCompletionsTransport(model='m', api_key='sk', timeout=10); print(isinstance(t, LLMProvider))"
```
Expected: `True`

- [ ] **Step 8: 全量套件（确认未破坏旧链路）**

Run: `uv run pytest -q`
Expected: PASS（旧 `chat_completions_provider.py` 仍在，新文件只是新增）

- [ ] **Step 9: Commit**

```bash
git add matmaster/providers/transports/ tests/matmaster/providers/test_chat_completions_transport_build_kwargs.py tests/matmaster/providers/test_chat_completions_transport_normalize.py
git commit -m "feat(providers): add ChatCompletionsTransport (build_kwargs/convert/normalize/classify seams)"
```

---

# Phase 2 — 原子替换（config schema + factory + 调用方 + yaml；中途套件红，phase 末恢复全绿）

> ⚠️ 本 phase 推翻旧 config schema 并删除旧 provider，**Task 3 起到 Task 13 完成前，全量套件处于红/import-error 状态属预期**。每个 task 仍单独 commit（WIP），Task 13 跑全量套件作为本 phase 绿点。建议在一个 git 分支上连续完成本 phase 再切走。

## Task 3: 重写 config schema（`matmaster/config/llm.py`）

**Files:**
- Modify (full rewrite): `matmaster/config/llm.py`
- Modify: `matmaster/config/__init__.py`

- [ ] **Step 1: 全量重写 `matmaster/config/llm.py`**

```python
"""LLM provider 配置（纯数据 schema）。

3 个 Pydantic 模型 + 1 个 ResolvedModel NamedTuple：
- ProviderConfig：一个后端连接（怎么连）
- LLMProfileConfig：一个对外可选模型（纯数据，无 effective_* / build_* 方法）
- LLMConfig：连接池 + 模型表 + 默认（无 routes）
语义 → 请求 kwargs 的翻译全部移到 transport.build_kwargs。
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import BaseModel, Field, model_validator


class ProviderConfig(BaseModel):
    """一个后端连接：怎么连到 provider。"""

    transport: str  # 轴 B dispatch 标签
    api_key: str
    base_url: str | None = None


class LLMProfileConfig(BaseModel):
    """一个对外可选模型（profile key = 对外标识）。纯数据。"""

    provider: str  # → providers[...] 的键
    model: str  # 下发 wire 的模型 id
    # 推理意图（仅声明，无 protocol/kind；transport.build_kwargs 负责翻译）
    reasoning_effort: str | None = None
    reasoning_summary: Literal["auto", "concise", "detailed"] | None = None
    # 采样
    temperature: float = 0.7
    max_tokens: int | None = None
    # 限制 / 能力
    context_limit: int = Field(..., gt=0)
    supports_vision: bool = False
    vision_detail: Literal["low", "high", "auto"] | None = "high"
    # 超时 / 重试
    timeout: float = 300
    stream_timeout: float | None = None
    stream_idle_timeout: float | None = None
    max_retries: int = 3
    retry_delay: float = 1.0


class ResolvedModel(NamedTuple):
    """解析结果：键 + 两个源对象引用，零反规范化。

    下游按需读 resolved.profile.model / resolved.provider.transport /
    resolved.profile.provider。
    """

    profile_key: str
    profile: LLMProfileConfig
    provider: ProviderConfig


class LLMConfig(BaseModel):
    """顶层：连接池 + 模型表 + 默认。无 routes。"""

    providers: dict[str, ProviderConfig]
    profiles: dict[str, LLMProfileConfig]
    default: str

    @model_validator(mode="after")
    def _check_refs(self) -> "LLMConfig":
        if self.default not in self.profiles:
            raise ValueError(
                f"default profile '{self.default}' not found, "
                f"available: {list(self.profiles)}"
            )
        for key, profile in self.profiles.items():
            if profile.provider not in self.providers:
                raise ValueError(
                    f"profile '{key}' references provider '{profile.provider}' "
                    f"which is not declared in providers, "
                    f"available: {list(self.providers)}"
                )
        return self

    def resolve(
        self,
        *,
        model_override: str | None = None,
        default_key: str | None = None,
    ) -> ResolvedModel:
        """对外标识（profile key）→ ResolvedModel。miss → KeyError，fail-fast。

        key 解析：model_override > default_key（agent 级默认）> self.default。
        transport tag 合法性不在此校验（见 factory dispatch）。
        """
        key = model_override or default_key or self.default
        try:
            profile = self.profiles[key]
        except KeyError as exc:
            raise KeyError(
                f"LLM profile '{key}' not found, available: {list(self.profiles)}"
            ) from exc
        return ResolvedModel(
            profile_key=key,
            profile=profile,
            provider=self.providers[profile.provider],
        )
```

> 说明：相对旧 schema，删除了 `PromptCacheConfig`/`LLMRouteConfig`/`ResolvedLLMRoute`、`MODEL_FAMILY_DEFAULTS`/`PROVIDER_TRANSPORT`/`PLATFORM_PROVIDERS`/`_infer_model_family`、profile 上全部 `effective_*`/`build_extra_kwargs`/死字段、`LLMConfig.get_profile`/`resolve_route`。

- [ ] **Step 2: 更新 `matmaster/config/__init__.py`**

把第 13 行与 `__all__` 改为：

```python
from .llm import LLMConfig, LLMProfileConfig, ProviderConfig, ResolvedModel
```

`__all__` 中 `"LLMRouteConfig", "ResolvedLLMRoute"` 两项替换为 `"ProviderConfig", "ResolvedModel"`。

- [ ] **Step 3: 重写 `tests/matmaster/config/test_llm.py`**

删除全部针对旧 schema 的测试（`PromptCacheConfig`、`effective_*`、`build_extra_kwargs`、`LLMRouteConfig`、`resolve_route`、`effective_transport` 等）。新文件内容：

```python
"""LLMConfig 新 schema：providers/profiles/default + resolve（无 routes）。"""

from __future__ import annotations

import pytest

from matmaster.config.llm import (
    LLMConfig,
    LLMProfileConfig,
    ProviderConfig,
    ResolvedModel,
)


def _cfg() -> LLMConfig:
    return LLMConfig(
        providers={
            "litellm": ProviderConfig(
                transport="chat_completions",
                api_key="sk-test",
                base_url="http://litellm-proxy",
            )
        },
        profiles={
            "matmaster/qwen3.7-max": LLMProfileConfig(
                provider="litellm",
                model="matmaster/qwen3.7-max",
                reasoning_effort="high",
                context_limit=1_000_000,
                supports_vision=True,
            ),
            "matmaster/dsk-v4p": LLMProfileConfig(
                provider="litellm",
                model="aliyun/deepseek-v4-pro",
                reasoning_effort="max",
                context_limit=200_000,
            ),
        },
        default="matmaster/qwen3.7-max",
    )


class TestResolve:
    def test_default_path(self) -> None:
        r = _cfg().resolve()
        assert isinstance(r, ResolvedModel)
        assert r.profile_key == "matmaster/qwen3.7-max"
        assert r.profile.model == "matmaster/qwen3.7-max"
        assert r.provider.transport == "chat_completions"

    def test_model_override_is_profile_key(self) -> None:
        r = _cfg().resolve(model_override="matmaster/dsk-v4p")
        assert r.profile_key == "matmaster/dsk-v4p"
        assert r.profile.model == "aliyun/deepseek-v4-pro"  # 对外名 ≠ wire 名

    def test_default_key_used_when_no_override(self) -> None:
        r = _cfg().resolve(default_key="matmaster/dsk-v4p")
        assert r.profile_key == "matmaster/dsk-v4p"

    def test_override_beats_default_key(self) -> None:
        r = _cfg().resolve(
            model_override="matmaster/qwen3.7-max", default_key="matmaster/dsk-v4p"
        )
        assert r.profile_key == "matmaster/qwen3.7-max"

    def test_unknown_key_fail_fast(self) -> None:
        with pytest.raises(KeyError, match="not found"):
            _cfg().resolve(model_override="nope")


class TestValidation:
    def test_default_must_exist(self) -> None:
        with pytest.raises(ValueError, match="default profile"):
            LLMConfig(
                providers={"litellm": ProviderConfig(transport="chat_completions", api_key="k")},
                profiles={
                    "a": LLMProfileConfig(provider="litellm", model="m", context_limit=1)
                },
                default="missing",
            )

    def test_profile_provider_must_be_declared(self) -> None:
        with pytest.raises(ValueError, match="not declared in providers"):
            LLMConfig(
                providers={"litellm": ProviderConfig(transport="chat_completions", api_key="k")},
                profiles={
                    "a": LLMProfileConfig(provider="ghost", model="m", context_limit=1)
                },
                default="a",
            )
```

- [ ] **Step 4: 跑 config 测试**

Run: `uv run pytest tests/matmaster/config/test_llm.py -v`
Expected: PASS

- [ ] **Step 5: Commit (WIP — factory 暂未跟进，整体套件会红)**

```bash
git add matmaster/config/llm.py matmaster/config/__init__.py tests/matmaster/config/test_llm.py
git commit -m "refactor(config): rewrite LLM schema to providers/profiles/resolve, drop routes/effective_*"
```

---

## Task 4: 迁移 `config/llm_config.yaml`

**Files:**
- Modify (full rewrite): `config/llm_config.yaml`
- Modify: `tests/matmaster/config/test_loader.py`

- [ ] **Step 1: 全量重写 `config/llm_config.yaml`**

```yaml
# LLM 配置（独立文件）
# providers: 后端连接（按 provider 去重，transport 在此声明）
# profiles: 每个对外可选模型（profile key = 前端 model_override = 对外标识）
# default: 未指定时使用的默认 profile key
# 鉴权凭据从 .env 读取，使用 ${VAR} 引用

providers:
  litellm:
    transport: chat_completions
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"

profiles:
  matmaster/qwen3.7-max:
    provider: litellm
    model: matmaster/qwen3.7-max
    reasoning_effort: high
    context_limit: 1000000
    supports_vision: true
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0

  gemini-3.1-pro-preview:
    provider: litellm
    model: gemini-3.1-pro-preview
    reasoning_effort: high
    temperature: 1.0
    context_limit: 200000
    supports_vision: true
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0

  matmaster/gpt-5.5:
    provider: litellm
    model: matmaster/gpt-5.5
    reasoning_effort: xhigh
    reasoning_summary: detailed
    context_limit: 256000
    supports_vision: true
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0

  matmaster/dsk-v4p:
    provider: litellm
    model: aliyun/deepseek-v4-pro
    reasoning_effort: max
    context_limit: 200000
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0

  matmaster/DeepSeek-v4-Pro:
    provider: litellm
    model: matmaster/DeepSeek-v4-Pro
    reasoning_effort: max
    context_limit: 200000
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0

default: matmaster/qwen3.7-max
```

> 变更对照：删 `sonnet`/`opus_global`/`opus_bedrock` 三个 profile；删整个 `routes:` 段（含遗留别名 `cds/GPT-5.5`）；profile key 重命名为对外模型 id（`qwen_3_7_max`→`matmaster/qwen3.7-max`、`gemini-pro`→`gemini-3.1-pro-preview`、`gpt55`→`matmaster/gpt-5.5`、`deepseek_v4_pro`→`matmaster/dsk-v4p`（对外名≠wire 名）、`deepseek_v4_pro_mm`→`matmaster/DeepSeek-v4-Pro`）；连接提到 `providers.litellm`；`thinking_effort`→`reasoning_effort`；删 `reasoning_protocol`/`temperature_policy`/`prompt_cache`/`model_family`/`api_version`；`default` 改指对外 id。

- [ ] **Step 2: 改造 `tests/matmaster/config/test_loader.py`**

删除涉及 route 表查找的解析断言。把加载后断言改读新结构。先看现有断言：

Run: `uv run pytest tests/matmaster/config/test_loader.py -v` 看哪些失败，然后把：
- 任何 `cfg.resolve_route(model_override="...")` → `cfg.resolve(model_override="...")`，断言改 `r.profile_key` / `r.profile.model` / `r.provider.transport`。
- 任何对 `cfg.routes` / `LLMRouteConfig` / `ResolvedLLMRoute` 的引用删除。
- env 展开测试（`${VAR}`）若断言落在 profile.api_key 上，改为断言 `cfg.providers["litellm"].api_key`。

（具体断言数量取决于现有文件；逐条改到绿。）

- [ ] **Step 3: 跑 loader 测试**

Run: `uv run pytest tests/matmaster/config/test_loader.py -v`
Expected: PASS

- [ ] **Step 4: Commit (WIP)**

```bash
git add config/llm_config.yaml tests/matmaster/config/test_loader.py
git commit -m "refactor(config): migrate llm_config.yaml to providers section, drop routes/claude/bedrock"
```

---

## Task 5: 重写 factory（dispatch 表）+ 删旧 provider

**Files:**
- Modify (full rewrite): `matmaster/providers/llm_factory.py`
- Modify: `matmaster/providers/__init__.py`
- Delete: `matmaster/providers/chat_completions_provider.py`
- Delete: `matmaster/providers/bedrock_provider.py`
- Delete: `tests/matmaster/providers/test_bedrock_provider.py`
- Delete: `tests/matmaster/providers/test_chat_completions_provider_prompt_cache.py`
- Modify: `tests/matmaster/providers/test_llm_factory.py`
- Modify: `tests/matmaster/providers/test_byok_provider.py`
- Modify (rename only): `tests/matmaster/providers/test_chat_completions_provider.py`、`test_chat_completions_provider_errors.py`、`test_chat_completions_provider_tool_choice.py`、`tests/matmaster/types/test_llm_provider.py`、`tests/matmaster/integration/test_tool_protocol_guardrails.py`、`tests/matmaster/devshell/test_devshell_mcp_skill_filter.py`

- [ ] **Step 1: 全量重写 `matmaster/providers/llm_factory.py`**

```python
"""LLM Provider factory：dispatch 表驱动构造（tag → builder，→ LLMProvider）。

build_provider_bundle 从 if/elif 改为查表；未知 transport → 配置错误 fail-fast。
这是 transport tag 合法性的唯一校验点（避免环形 import）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal

from matmaster.config.llm import (
    LLMConfig,
    LLMProfileConfig,
    ProviderConfig,
    ResolvedModel,
)
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.llm_provider import LLMProvider

BYOK_PROFILE_KEY = "byok"
BYOK_DEFAULT_CONTEXT_LIMIT = 200_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMProviderBundle:
    """Provider 加上用于 run 持久化的解析模型身份。"""

    provider: LLMProvider
    model: str
    model_profile: str
    model_route: str | None
    provider_name: str
    context_limit: int
    context_limit_source: Literal["profile", "byok_credential", "byok_default"]


def _build_chat_completions_transport(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> ChatCompletionsTransport:
    """profile 平铺字段 + provider 连接 → ChatCompletionsTransport。

    标准路径与 BYOK 路径共用：reasoning/temperature/timeout/retry 来自 profile，
    连接（api_key/base_url）来自 provider，extra_body 为 BYOK 黑盒透传。
    """
    return ChatCompletionsTransport(
        model=profile.model,
        api_key=provider.api_key,
        base_url=provider.base_url,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        reasoning_effort=profile.reasoning_effort,
        reasoning_summary=profile.reasoning_summary,
        extra_body=extra_body,
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
    )


# tag(轴B) → builder（→ LLMProvider，轴A）
_TRANSPORT_BUILDERS: dict[
    str, Callable[[LLMProfileConfig, ProviderConfig], LLMProvider]
] = {
    "chat_completions": _build_chat_completions_transport,
}


def _dispatch(profile: LLMProfileConfig, provider: ProviderConfig) -> LLMProvider:
    try:
        builder = _TRANSPORT_BUILDERS[provider.transport]
    except KeyError as exc:
        raise ValueError(
            f"unsupported transport: {provider.transport!r}, "
            f"available: {list(_TRANSPORT_BUILDERS)}"
        ) from exc
    return builder(profile, provider)


def build_provider(
    llm_config: LLMConfig,
    *,
    model_override: str | None = None,
    default_profile_key: str | None = None,
) -> LLMProvider:
    """解析并构造 LLM provider 后端。"""
    return build_provider_bundle(
        llm_config,
        model_override=model_override,
        default_profile_key=default_profile_key,
    ).provider


def build_provider_bundle(
    llm_config: LLMConfig,
    *,
    model_override: str | None = None,
    default_profile_key: str | None = None,
) -> LLMProviderBundle:
    """解析一个 profile 并同时构造 provider 与持久化身份。"""
    resolved: ResolvedModel = llm_config.resolve(
        model_override=model_override, default_key=default_profile_key
    )
    logger.info(
        "build_provider: profile=%s model=%s transport=%s provider=%s",
        resolved.profile_key,
        resolved.profile.model,
        resolved.provider.transport,
        resolved.profile.provider,
    )
    provider = _dispatch(resolved.profile, resolved.provider)
    return LLMProviderBundle(
        provider=provider,
        model=resolved.profile.model,
        model_profile=resolved.profile_key,
        model_route=resolved.profile_key,  # routes 删除后对外标识即 profile key
        provider_name=resolved.profile.provider,
        context_limit=resolved.profile.context_limit,
        context_limit_source="profile",
    )


def build_byok_provider_bundle(
    *,
    model: str,
    api_key: str,
    base_url: str,
    credential_id: str | None = None,
    context_limit: int | None = None,
    extra_body: dict | None = None,
) -> LLMProviderBundle:
    """用用户自带 Key（BYOK）构造 OpenAI 兼容 transport。

    不读 llm_config：model/api_key/base_url 全来自 tools-server 下发的凭证。
    合成 profile + provider 固定 transport=chat_completions（不依赖任何映射表）。
    extra_body 为凭证侧黑盒透传，原样合并进请求体；内容正确性由用户负责。
    身份固定为 provider=byok、billing_mode=byok（阶段一已收束）。
    """
    if context_limit is not None and context_limit <= 0:
        raise ValueError("BYOK context_limit must be a positive integer")
    effective_context_limit = context_limit or BYOK_DEFAULT_CONTEXT_LIMIT
    context_limit_source = (
        "byok_credential" if context_limit is not None else "byok_default"
    )
    profile = LLMProfileConfig(
        provider="byok", model=model, context_limit=effective_context_limit
    )
    provider_conn = ProviderConfig(
        transport="chat_completions", api_key=api_key, base_url=base_url
    )
    logger.info(
        "build_byok_provider: model=%s base_url_host=%s extra_body_keys=%s",
        model,
        (base_url.split("//", 1)[-1].split("/", 1)[0] if base_url else ""),
        sorted((extra_body or {}).keys()),
    )
    provider = _build_chat_completions_transport(
        profile, provider_conn, extra_body=extra_body
    )
    return LLMProviderBundle(
        provider=provider,
        model=model,
        model_profile=BYOK_PROFILE_KEY,
        model_route=f"byok:{credential_id}" if credential_id else BYOK_PROFILE_KEY,
        provider_name="byok",
        context_limit=effective_context_limit,
        context_limit_source=context_limit_source,
    )
```

- [ ] **Step 2: 更新 `matmaster/providers/__init__.py`**

```python
"""matmaster.providers -- Concrete LLM provider implementations."""

from .llm_factory import build_provider
from .transports.chat_completions import ChatCompletionsTransport

__all__ = ["ChatCompletionsTransport", "build_provider"]
```

- [ ] **Step 3: 删除旧文件**

```bash
git rm matmaster/providers/chat_completions_provider.py \
       matmaster/providers/bedrock_provider.py \
       tests/matmaster/providers/test_bedrock_provider.py \
       tests/matmaster/providers/test_chat_completions_provider_prompt_cache.py
```

- [ ] **Step 4: 重命名残留 provider 测试的类名与 import 路径**

下列文件把 `from matmaster.providers.chat_completions_provider import ChatCompletionsProvider`（及 `AnthropicPromptCacheOptions` 等）改为 `from matmaster.providers.transports.chat_completions import ChatCompletionsTransport`，并将文件内 `ChatCompletionsProvider(` → `ChatCompletionsTransport(`：
- `tests/matmaster/providers/test_chat_completions_provider.py`
- `tests/matmaster/providers/test_chat_completions_provider_errors.py`
- `tests/matmaster/providers/test_chat_completions_provider_tool_choice.py`
- `tests/matmaster/types/test_llm_provider.py`（第 123 行 import）
- `tests/matmaster/integration/test_tool_protocol_guardrails.py`（第 8 行 import）
- `tests/matmaster/devshell/test_devshell_mcp_skill_filter.py`（第 15 行 import）

构造参数对照（去掉已删参数）：旧 `ChatCompletionsProvider(model=..., api_key=..., temperature=..., max_tokens=..., timeout=..., extra_kwargs={...}, prompt_cache_options=...)` → 新 `ChatCompletionsTransport(model=..., api_key=..., temperature=..., max_tokens=..., timeout=..., reasoning_effort=..., reasoning_summary=..., extra_body=...)`。`extra_kwargs`/`prompt_cache_options` 参数已不存在；任何断言 `provider._extra_kwargs` / `provider._prompt_cache_options` 的用例删除（prompt cache 用例整文件已删）。

> 在 `test_chat_completions_provider*.py` 中，凡是直接给 `provider._client = <mock>` 再调 `chat`/`chat_stream` 的用例，逻辑不变（属性名一致）。涉及 `extra_kwargs` 请求体断言的，改为构造时传 `reasoning_effort=`/`reasoning_summary=` 并断言 `build_kwargs` 输出或请求 kwargs 中的 `reasoning_effort`/`extra_body`。

- [ ] **Step 5: 重写 `tests/matmaster/providers/test_llm_factory.py`**

```python
"""llm_factory：dispatch 表驱动 + providers 段解析 + bundle 身份。

build_provider 同步、返回未初始化 transport（无 client）。
"""

from __future__ import annotations

import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig, ProviderConfig
from matmaster.providers.llm_factory import (
    _TRANSPORT_BUILDERS,
    build_provider,
    build_provider_bundle,
)
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport


@pytest.fixture()
def llm_config() -> LLMConfig:
    return LLMConfig(
        providers={
            "litellm": ProviderConfig(
                transport="chat_completions",
                api_key="sk-test",
                base_url="http://litellm-proxy",
            )
        },
        profiles={
            "matmaster/qwen3.7-max": LLMProfileConfig(
                provider="litellm",
                model="matmaster/qwen3.7-max",
                reasoning_effort="high",
                context_limit=1_000_000,
                stream_timeout=120.0,
                stream_idle_timeout=60.0,
            ),
            "matmaster/dsk-v4p": LLMProfileConfig(
                provider="litellm",
                model="aliyun/deepseek-v4-pro",
                reasoning_effort="max",
                context_limit=200_000,
            ),
        },
        default="matmaster/qwen3.7-max",
    )


class TestBuildProvider:
    def test_default_path(self, llm_config: LLMConfig) -> None:
        p = build_provider(llm_config)
        assert isinstance(p, ChatCompletionsTransport)
        assert p._model == "matmaster/qwen3.7-max"
        assert p._reasoning_effort == "high"
        assert p._client is None  # lazy init

    def test_model_override_is_profile_key(self, llm_config: LLMConfig) -> None:
        p = build_provider(llm_config, model_override="matmaster/dsk-v4p")
        assert p._model == "aliyun/deepseek-v4-pro"  # 对外名 ≠ wire 名

    def test_unknown_key_raises(self, llm_config: LLMConfig) -> None:
        with pytest.raises(KeyError, match="not found"):
            build_provider(llm_config, model_override="nonexistent")

    def test_custom_default_key(self, llm_config: LLMConfig) -> None:
        p = build_provider(llm_config, default_profile_key="matmaster/dsk-v4p")
        assert p._model == "aliyun/deepseek-v4-pro"

    def test_stream_timeout_passed(self, llm_config: LLMConfig) -> None:
        p = build_provider(llm_config)
        assert p.stream_timeout == 120.0
        assert p.stream_idle_timeout == 60.0

    def test_bundle_identity(self, llm_config: LLMConfig) -> None:
        b = build_provider_bundle(llm_config, model_override="matmaster/dsk-v4p")
        assert b.provider._model == "aliyun/deepseek-v4-pro"
        assert b.model == "aliyun/deepseek-v4-pro"
        assert b.model_profile == "matmaster/dsk-v4p"
        assert b.model_route == "matmaster/dsk-v4p"
        assert b.provider_name == "litellm"
        assert b.context_limit == 200_000
        assert b.context_limit_source == "profile"


class TestDispatch:
    def test_chat_completions_tag_hits_builder(self) -> None:
        assert "chat_completions" in _TRANSPORT_BUILDERS

    def test_unknown_transport_fail_fast(self) -> None:
        cfg = LLMConfig(
            providers={
                "x": ProviderConfig(transport="anthropic_messages", api_key="k")
            },
            profiles={
                "p": LLMProfileConfig(provider="x", model="m", context_limit=1)
            },
            default="p",
        )
        with pytest.raises(ValueError, match="unsupported transport"):
            build_provider(cfg)
```

- [ ] **Step 6: 重写 `tests/matmaster/providers/test_byok_provider.py`**

```python
"""BYOK：合成 profile + 固定 transport=chat_completions，extra_body 黑盒透传。"""

from __future__ import annotations

from matmaster.providers.llm_factory import BYOK_PROFILE_KEY, build_byok_provider_bundle
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport

_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_basic_identity_and_passthrough() -> None:
    b = build_byok_provider_bundle(
        model="qwen-max",
        api_key="sk-user",
        base_url=_BASE_URL,
        credential_id="cred-1",
        extra_body={"enable_thinking": True, "thinking_budget": 1024},
    )
    assert b.model == "qwen-max"
    assert b.model_profile == BYOK_PROFILE_KEY
    assert b.model_route == "byok:cred-1"
    assert b.provider_name == "byok"
    assert isinstance(b.provider, ChatCompletionsTransport)
    assert b.provider._model == "qwen-max"
    assert b.provider._api_key == "sk-user"
    assert b.provider._base_url == _BASE_URL
    assert b.provider._extra_body == {"enable_thinking": True, "thinking_budget": 1024}


def test_no_extra_body_default_context_limit() -> None:
    b = build_byok_provider_bundle(
        model="qwen-max", api_key="sk-user", base_url=_BASE_URL, credential_id="cred-2"
    )
    assert b.provider._extra_body is None
    assert b.context_limit == 200_000
    assert b.context_limit_source == "byok_default"


def test_explicit_context_limit_from_credential() -> None:
    b = build_byok_provider_bundle(
        model="qwen-max",
        api_key="sk-user",
        base_url=_BASE_URL,
        credential_id="cred-3",
        context_limit=1_000_000,
    )
    assert b.context_limit == 1_000_000
    assert b.context_limit_source == "byok_credential"


def test_route_falls_back_to_profile_key_without_credential_id() -> None:
    b = build_byok_provider_bundle(model="qwen-max", api_key="sk", base_url=_BASE_URL)
    assert b.model_route == BYOK_PROFILE_KEY


def test_extra_body_passthrough_in_build_kwargs() -> None:
    b = build_byok_provider_bundle(
        model="qwen-max",
        api_key="sk",
        base_url=_BASE_URL,
        extra_body={"enable_thinking": True},
    )
    kw = b.provider.build_kwargs([], None)
    assert kw["extra_body"] == {"enable_thinking": True}
```

- [ ] **Step 7: 跑全部 provider 测试**

Run: `uv run pytest tests/matmaster/providers/ tests/matmaster/types/test_llm_provider.py -v`
Expected: PASS（bedrock/prompt_cache 文件已删，其余绿）

- [ ] **Step 8: Commit (WIP)**

```bash
git add -A matmaster/providers/ tests/matmaster/providers/ tests/matmaster/types/test_llm_provider.py tests/matmaster/integration/test_tool_protocol_guardrails.py tests/matmaster/devshell/test_devshell_mcp_skill_filter.py
git commit -m "refactor(providers): dispatch-table factory; drop bedrock + prompt cache; rename to ChatCompletionsTransport"
```

---

## Task 6: 收敛 `agent_run_service.py`（删 llm_override）

**Files:**
- Modify: `src/services/agent_run_service.py:230,239,404-416`

- [ ] **Step 1: 删 `run_agent` 的 `llm_override` 形参**

在 `async def run_agent(...)` 签名（约第 230-245 行）中删除 `llm_override: str | None = None,` 这一形参。

- [ ] **Step 2: 改 image detail + bundle 调用（约 404-416 行）**

把：
```python
                image_detail = image_service.resolve_image_detail(
                    llm_config=llm_config,
                    images=current_images,
                    llm_override=llm_override,
                    model_override=model_override,
                    default_profile_key=agent_default_llm,
                )
                llm_bundle = build_provider_bundle(
                    llm_config,
                    model_override=model_override,
                    llm_override=llm_override,
                    default_profile_key=agent_default_llm,
                )
```
改为（删两处 `llm_override=` 行）：
```python
                image_detail = image_service.resolve_image_detail(
                    llm_config=llm_config,
                    images=current_images,
                    model_override=model_override,
                    default_profile_key=agent_default_llm,
                )
                llm_bundle = build_provider_bundle(
                    llm_config,
                    model_override=model_override,
                    default_profile_key=agent_default_llm,
                )
```

- [ ] **Step 3: 验证 import 与构造可用**

Run: `uv run python -c "import src.services.agent_run_service"`
Expected: 无异常（若报其它模块未跟进的 import 错，记录留待后续 task，本步只确认本文件语法正确）

- [ ] **Step 4: Commit (WIP)**

```bash
git add src/services/agent_run_service.py
git commit -m "refactor(services): drop llm_override from run_agent path"
```

---

## Task 7: 收敛 `image_input_service.py`（删 llm_override + 用 resolve）

**Files:**
- Modify: `src/services/image_input_service.py:184-231`

- [ ] **Step 1: 改 `ensure_vision_supported`**

```python
    def ensure_vision_supported(
        self,
        *,
        llm_config: LLMConfig,
        model_override: str | None,
        default_profile_key: str | None,
    ) -> LLMProfileConfig:
        resolved = llm_config.resolve(
            model_override=model_override,
            default_key=default_profile_key,
        )
        profile = resolved.profile
        if not profile.supports_vision:
            raise ImageInputError(
                VISION_MODEL_NOT_SUPPORTED,
                "当前模型不支持图片输入，请切换到支持图片的模型后重试。",
            )
        return profile
```

- [ ] **Step 2: 改 `resolve_image_detail`**

删 `llm_override: str | None,` 形参，并把内部调用改为不传 `llm_override`：
```python
    def resolve_image_detail(
        self,
        *,
        llm_config: LLMConfig,
        images: tuple[str, ...],
        model_override: str | None,
        default_profile_key: str | None,
    ) -> ImageDetail | None:
        if not images:
            return None
        profile = self.ensure_vision_supported(
            llm_config=llm_config,
            model_override=model_override,
            default_profile_key=default_profile_key,
        )
        return profile.vision_detail
```

- [ ] **Step 3: 改 `tests/services/test_image_input_service.py`**

删除/改造涉及 `llm_override=` 的调用与 route 表的 fixture。把测试用 `LLMConfig` 构造改为新 schema（providers/profiles），调用 `ensure_vision_supported`/`resolve_image_detail` 时去掉 `llm_override=`。涉及 `resolve_route` mock 的改为 `resolve`。

Run 看现状: `uv run pytest tests/services/test_image_input_service.py -v` → 逐条改到绿。

- [ ] **Step 4: 跑该测试**

Run: `uv run pytest tests/services/test_image_input_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit (WIP)**

```bash
git add src/services/image_input_service.py tests/services/test_image_input_service.py
git commit -m "refactor(services): image_input uses resolve(); drop llm_override"
```

---

## Task 8: 收敛 `feishu_notifier.format_llm_model_for_notify` 为单参数

**Files:**
- Modify: `src/utils/feishu_notifier.py:24-34`

- [ ] **Step 1: 改函数签名为单参数**

```python
def format_llm_model_for_notify(model: str | None) -> str:
    """渲染本轮模型名，供飞书卡片「模型」行展示。"""
    m = (model or '').strip()
    return m or '默认'
```

> 三处调用方（worker/stream_service/worker completion card）在 Task 9/10 改为单参数；本步只改定义。

- [ ] **Step 2: 语法检查**

Run: `uv run python -c "from src.utils.feishu_notifier import format_llm_model_for_notify; print(format_llm_model_for_notify('m'), format_llm_model_for_notify(None))"`
Expected: `m 默认`

- [ ] **Step 3: Commit (WIP)**

```bash
git add src/utils/feishu_notifier.py
git commit -m "refactor(utils): format_llm_model_for_notify takes single model arg"
```

---

## Task 9: 收敛 `stream_service.py`（删 llm 管道）

**Files:**
- Modify: `src/services/stream_service.py:288-304,334,411-428,663-784`

- [ ] **Step 1: `_prepare_run` 删 `llm` 形参与 job 字段**

找到 `_prepare_run` 定义（含 `llm` 形参，约 263-304 行附近），删除 `llm` 形参；job dict（约 288-304 行）删除 `'llm': llm,` 这一行。

- [ ] **Step 2: 排队通知改单参数（约 334 行）**

```python
                        format_llm_model_for_notify(job.get('model')),
```

- [ ] **Step 3: `trigger_run` 内 `_prepare_run(...)` 去掉 `llm=llm_val`（约 421 行）**

删除 `llm=llm_val,` 行；若 `llm_val` 局部变量随之无引用，一并删除其赋值。

- [ ] **Step 4: `prepare_send_message` 去掉 `llm` 提取与传递**

删除约 689 行 `llm = (req.llm or '').strip() or None`；删除约 773 行 `_prepare_run(... llm=llm, ...)` 中的 `llm=llm,`。

- [ ] **Step 5: 全文确认无残留 `req.llm` / `job.get('llm')` / `llm=`**

Run: `grep -n "req\.llm\|\.get('llm')\|llm=" src/services/stream_service.py`
Expected: 无输出（或仅剩与本改动无关的同名子串，逐一确认）

- [ ] **Step 6: 语法检查**

Run: `uv run python -c "import src.services.stream_service"`
Expected: 无异常

- [ ] **Step 7: Commit (WIP)**

```bash
git add src/services/stream_service.py
git commit -m "refactor(services): drop llm plumbing from stream_service (job/notify/prepare)"
```

---

## Task 10: 收敛 `agent_worker.py` + `chat_api.py` + `chat.py`（删 req.llm / llm_override）

**Files:**
- Modify: `src/worker/agent_worker.py:107,332,422,444,540-541`
- Modify: `src/apis/chat_api.py:160,422`
- Modify: `src/models/chat.py:385`

- [ ] **Step 1: `agent_worker.py` 删 `llm_override` 提取（约 332 行）**

删除 `llm_override = (payload.get('llm') or '').strip() or None`。

- [ ] **Step 2: worker 开始通知改单参数（约 422 行）**

```python
                    ('模型', format_llm_model_for_notify(model_override)),
```

- [ ] **Step 3: 删 run_agent_kwargs 的 llm_override（约 444 行）**

删除 `"llm_override": llm_override,` 行。

- [ ] **Step 4: 删 logging dict 的 llm_override（约 444 行附近的日志字典）**

删除 `"llm_override": llm_override,`（若存在于日志结构里）。

- [ ] **Step 5: 完成卡片改单参数（约 540-541 行）**

`_build_completion_card(...)` 调用处把 `llm=llm_override,` 删除；同时改 `_build_completion_card` 定义（约 83-107 行）删除 `llm` 形参，第 107 行 `format_llm_model_for_notify(llm, model)` → `format_llm_model_for_notify(model)`。

- [ ] **Step 6: `chat_api.py` 删 `req.llm` 透传（约 160 行）**

`stream_svc.trigger_run(...)` 调用删除 `llm=req.llm,` 行（保留 `model=req.model,`）。

> 注意：`trigger_run` 定义在 stream_service，其 `llm` 形参需同步删除——见本步附加。

- [ ] **Step 7: `chat_api.py` vision 校验去 llm_override（约 420-424 行）**

```python
            image_service.ensure_vision_supported(
                llm_config=llm_config,
                model_override=(req.model or "").strip() or None,
                default_profile_key=_get_agent_default_llm(),
            )
```

- [ ] **Step 8: `trigger_run` 形参删 `llm`**

在 stream_service `trigger_run` 定义里删除 `llm` 形参（chat_api 已不再传）。确认其内部 `llm_val` 已在 Task 9 处理。

- [ ] **Step 9: `chat.py` 删 `ChatSendRequest.llm` 字段（约 385 行）**

删除 `llm: str | None = (... )` 整个字段定义（保留 `model` 字段）。

- [ ] **Step 10: 全仓确认无 `llm_override` / `req.llm` 残留**

Run:
```bash
grep -rn "llm_override\|req\.llm\|payload.get('llm')\|\.get(\"llm\")" src/ matmaster/
```
Expected: 无输出（devshell 在 Task 11 处理；若此处仍有 devshell 命中是预期，其余应为空）

- [ ] **Step 11: 语法检查**

Run: `uv run python -c "import src.worker.agent_worker; import src.apis.chat_api; import src.models.chat"`
Expected: 无异常

- [ ] **Step 12: Commit (WIP)**

```bash
git add src/worker/agent_worker.py src/apis/chat_api.py src/models/chat.py src/services/stream_service.py
git commit -m "refactor(api/worker): remove req.llm / llm_override end-to-end"
```

---

## Task 11: 收敛 devshell（resolve 重命名 + ResolvedModel 读法 + 删 get_profile）

**Files:**
- Modify: `matmaster/devshell/cli.py:209-218`
- Modify: `matmaster/devshell/debug_run.py:59-68`
- Modify: `matmaster/devshell/repl.py:78-80,193-197`
- Modify: `matmaster/devshell/runner.py:97-109`

- [ ] **Step 1: `cli.py` 改 resolve 调用（约 209-218 行）**

`build_provider_bundle(...)` 已无 `llm_override`，保持；把：
```python
        resolved = llm_config.resolve_route(
            model_override=model_override,
            default_key=agent_default_llm,
        )
```
改为：
```python
        resolved = llm_config.resolve(
            model_override=model_override,
            default_key=agent_default_llm,
        )
```

- [ ] **Step 2: `debug_run.py` 同样改（约 65-68 行）**

`resolve_route(` → `resolve(`，参数不变（`model_override=MODEL_OVERRIDE, default_key=agent_default_llm`）。

- [ ] **Step 3: `runner.py` 修 ResolvedModel 字段读法（约 97-109 行）**

`ResolvedModel` 无 `.model` / `.route_key`。把 `getattr(resolved_route, "model", None)` 改为 `getattr(getattr(resolved_route, "profile", None), "model", None)`；`getattr(resolved_route, "route_key", None)` 改为 `getattr(resolved_route, "profile_key", None)`。`profile_key` 字段名保持可用。

> 这些读取都在 `getattr(llm_bundle, ..., <fallback>)` 的 fallback 位置；`llm_bundle` 已提供 `model`/`model_profile`/`model_route`，故 fallback 几乎不触发，但仍需修正避免取到 None/AttributeError。

- [ ] **Step 4: `repl.py` 修 `_show_config` 与 banner（约 78-80、193-197 行）**

第 78-80 行 banner：`getattr(rr, "model", "?")` → `getattr(getattr(rr, "profile", None), "model", "?")`；`getattr(rr, "profile_key", "?")` 保持。

第 193-197 行 `_show_config`：删 `prof = runner._llm_config.get_profile(rr.profile_key)`，改读 `rr.profile`：
```python
    rr = runner._resolved_route
    if rr is not None:
        prof = rr.profile
        bu = (prof.base_url or "").strip()
        print(f"LLM: model={rr.profile.model} profile={rr.profile_key} base_url={bu}")
    else:
        print("LLM: (unavailable)")
```
> 注意：连接信息现在在 provider 上，不在 profile 上。`prof.base_url` 已不存在（profile 不再持连接）。改为读 `rr.provider.base_url`：
```python
        prof = rr.profile
        bu = (rr.provider.base_url or "").strip()
        print(f"LLM: model={rr.profile.model} profile={rr.profile_key} base_url={bu}")
```

- [ ] **Step 5: 语法检查 + devshell 测试**

Run: `uv run python -c "import matmaster.devshell.cli, matmaster.devshell.debug_run, matmaster.devshell.repl, matmaster.devshell.runner"`
Expected: 无异常
Run: `uv run pytest tests/matmaster/devshell/ -v`
Expected: PASS（test_repl 的 `build_provider_bundle` patch 路径不变；若 patch 了 `get_profile` 则删除该 patch）

- [ ] **Step 6: Commit (WIP)**

```bash
git add matmaster/devshell/
git commit -m "refactor(devshell): resolve() + ResolvedModel reads, drop get_profile"
```

---

## Task 12: 收尾 integration 测试 patch + 全量绿点

**Files:**
- Modify: `tests/matmaster/integration/test_image_input_e2e.py`、`test_e2e_mat_master.py`、`test_bohrium_execution_contract.py`、`test_quota_pipeline.py`、`test_llm_factory.py`（integration 版）、`tests/matmaster/services/test_agent_run_stream*.py`、`agent_run_stream_fixtures.py`、`tests/matmaster/devshell/test_repl.py`

- [ ] **Step 1: 跑全量套件，收集失败清单**

Run: `uv run pytest -q`
Expected: 一批失败，集中在：
- 仍用旧 `LLMConfig(routes=..., LLMRouteConfig)` 构造的 fixture；
- mock `resolve_route` / `get_profile`；
- 断言 `model_family` / `model_route` 旧值；
- patch `matmaster.providers.chat_completions_provider` 路径；
- 传 `llm_override=` 给 `build_provider_bundle` / `run_agent`。

- [ ] **Step 2: 逐文件修正**

对每个失败：
- `LLMConfig(profiles={...}, routes={...}, default=...)` → 新 schema（加 `providers={"litellm": ProviderConfig(transport="chat_completions", api_key=..., base_url=...)}`，profile 去掉连接/`model_family`/`reasoning_protocol`/`temperature_policy`/`thinking_effort`→`reasoning_effort`，删 `routes`）。
- mock `llm_config.resolve_route.return_value=<ResolvedLLMRoute>` → `llm_config.resolve.return_value=ResolvedModel(profile_key=..., profile=<LLMProfileConfig>, provider=<ProviderConfig>)`。
- mock `llm_config.get_profile.return_value=...` → 删除（改用 `resolve(...).profile`）。
- patch `"matmaster.providers.chat_completions_provider..."` → `"matmaster.providers.transports.chat_completions..."`。
- 断言 `bundle.model_family` → 删除该断言（字段已移除）。
- 任何 `build_provider_bundle(..., llm_override=...)` / `run_agent(..., llm_override=...)` → 删 `llm_override=`。
- `model_family=` 关键字传给 `LLMProfileConfig(...)` → 删除（字段已移除）。

> `test_image_input_e2e.py` 第 134 行 `model_family="vision"`、`test_bohrium_execution_contract.py` 第 387 行 `model_family="test-family"`：删除该关键字。

- [ ] **Step 3: 全量套件转绿**

Run: `uv run pytest -q`
Expected: PASS（全绿）

- [ ] **Step 4: 残留符号全仓扫描（确认死代码清零）**

Run:
```bash
grep -rn "resolve_route\|ResolvedLLMRoute\|LLMRouteConfig\|PromptCacheConfig\|AnthropicPromptCacheOptions\|effective_family\|effective_transport\|effective_temperature\|build_extra_kwargs\|model_family\|reasoning_protocol\|temperature_policy\|thinking_effort\|bedrock\|BedrockProvider\|PROVIDER_TRANSPORT\|PLATFORM_PROVIDERS\|MODEL_FAMILY_DEFAULTS\|get_profile\|llm_override\|chat_completions_provider" src/ matmaster/ tests/ config/
```
Expected: 无输出（仅允许 docs/ 残留；若有命中，回到对应 task 清理）。
**例外（不在本阶段 grep 范围、见下方「越界但受影响」）**：`evaluation/` 下仍有 bedrock fallback 启发式与默认 model 字符串，本阶段不强制清理，但需知晓其会因迁移而失效。

- [ ] **Step 5: Phase 2 绿点 Commit**

```bash
git add -A
git commit -m "test: migrate integration/service tests to new provider schema; suite green"
```

---

# Phase 3 — 验收

## Task 13: 完成标准核对 + lint

**Files:** 无新增，仅核对。

- [ ] **Step 1: 净代码量核对**

Run: `git diff --stat <stage2-base>..HEAD -- matmaster/ src/ config/`
Expected: provider/config 主代码净减少（删 bedrock 602 + prompt cache ~160 + 旧 schema/方言 > 新增 transport/dispatch）。记录数字。

- [ ] **Step 2: 完成标准逐条核对（spec §9）**

逐条确认并在本步打勾：
- factory 由 dispatch 表驱动，无 if/elif（看 `llm_factory.py`）。
- `Transport` 基类不自满足 Protocol、子类满足（Task 1/2 已验证）。
- chat_completions 请求 kwargs 由 `build_kwargs` 生成（`chat`/`chat_stream` 调用之）。
- `reasoning_protocol`/`temperature_policy`/`MODEL_FAMILY_DEFAULTS`/`_infer_model_family`/`model_family`/bedrock 全删（Step 4 扫描已确认）。
- config schema 仅 3 模型 + `ResolvedModel`；profile 纯数据、reasoning 平铺。
- `providers:` 段落地、连接去重；loader 只认新结构。
- `routes:` 删除，profile key 即对外标识；`resolve` 单查找、无表；`llm_override`/`req.llm` 旁路删除。
- 现有 openai 风格 profile（qwen/gemini/gpt55/deepseek×2）+ BYOK 行为等价。
- `convert_messages`/`build_kwargs`/`normalize_response`/`normalize_stream` 各有独立测试。

- [ ] **Step 3: 类型/风格检查（按仓库工具）**

Run: `uv run ruff check matmaster/providers matmaster/config src && uv run mypy matmaster/providers/transport.py matmaster/providers/transports/chat_completions.py matmaster/providers/llm_factory.py matmaster/config/llm.py`
Expected: 无新增错误（若仓库未配 mypy/ruff，跳过并说明）。

- [ ] **Step 4: 全量套件最终确认**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: 最终 Commit（如有 lint 修整）**

```bash
git add -A
git commit -m "chore(providers): stage-2 aggregation core acceptance (lint + checklist)"
```

---

## 越界但受影响（本阶段不改，但迁移会使其失效，须知会）

迁移删除了 `bedrock-claude-opus` / `claude-sonnet-4-6` / `global.anthropic.claude-opus-4-6-v1` 等对外标识。`evaluation/` 评测工具链对此有硬编码默认，本阶段**不在范围内修改**，但会在运行时因「未知 profile key → KeyError」而失效，须在评测前另行处理：

- `evaluation/scripts/devshell/run_devshell_eval.py:44`、`run_devshell_agent_loop.py`、`evaluation/docs/devshell/devshell_agent_sdk_loop.md` 的 `--model` / `--fallback-model` 默认（`bedrock-claude-opus` / `global.anthropic.claude-opus-4-6-v1`）需改指存活 profile key（如 `matmaster/qwen3.7-max`）。
- `evaluation/scripts/devshell/run_devshell_eval_helpers.py:134-150` 的 bedrock/botocore 传输错误启发式（`bedrock-runtime` / `converse-stream`）成为死分支，可后续随评测清理删除（本阶段保留，不影响生产链路）。
- `evaluation/skill_trigger/runner.py:204` 的 `build_provider(llm_config, model_override=model_route)` 调用签名不变、机制可用，只要 `model_route` 传存活 profile key 即可。

> 处置建议：作为本阶段后的独立小改动（评测侧默认 model 选择属产品决策），不并入本计划的绿点判定。

## 阶段三衔接备忘（本阶段产出的接缝，无需本阶段实现）

- dispatch 表 `_TRANSPORT_BUILDERS` + `Transport` 基类 seam = 加 `anthropic_messages`/`responses` 子类的插入点（加表项 + 写子类，不改 factory 控制流）。
- `convert_messages` identity 接缝已立：stage 3 引中立 IR 时在此填真实转换、移除 `to_api_dict()`。
- `providers:` 段就位：stage 3 直接加 `anthropic: {transport: anthropic_messages, ...}`，并把 sonnet/opus 作为 `provider: anthropic` 的 profile 加回（key 为对外模型 id，不复活 routes）。
- `usage_vendor` 单字段为 stage 3 泛化为 hermes 式 `provider_data` 袋留口；**不抄** hermes 的 backward-compat 影子属性（本项目禁止兜底）。
