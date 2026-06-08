# 阶段三 b：native anthropic_messages transport 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 native `anthropic_messages` transport，接入 Opus 4.6 的 Anthropic Messages API、signed thinking `provider_state` 回放、native prompt cache，并保持现有 `chat_completions` 路径行为不变。

**Architecture:** kernel 继续只搬运中立 `list[Message]` 与不透明 `ProviderState`；Anthropic wire 形状、tool/result 重排、thinking 签名回放、prompt cache 注入和 SDK 错误分类全部收敛在 `AnthropicMessagesTransport`。factory 通过 dispatch 表新增 `anthropic_messages` builder，配置层只新增 `PromptCacheConfig` 与一个 Opus profile，不改持久化 schema、agent 主循环或 BYOK 路径。

**Tech Stack:** Python 3.11+（uv 环境）、Pydantic v2、anthropic SDK 0.107.1、httpx、asyncio、pytest。

---

## 启动前提（硬前置，违反则停止）

本计划基线是阶段三 a 已落地后的仓库状态：

- `matmaster/types/messages.py` 已有 `ProviderState`，且 `AssistantMessage` / `LLMResponse` / `StreamChunk` 已有 `provider_state` 字段。
- `matmaster/providers/transport.py` 已有 `transport_tag` 与 `_claim_provider_state()`。
- `LLMProvider` 协议和 `ChatCompletionsTransport` 已改为接收 `list[Message]`。
- `stream_llm_items` 已能把流末 `StreamChunk.provider_state` 聚合进最终 `LLMResponse`。

执行前先跑：

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest \
  tests/matmaster/types/test_provider_state.py \
  tests/matmaster/providers/test_provider_state_claim.py \
  tests/matmaster/core/test_provider_state_aggregation.py \
  -q
```

Expected: PASS。若失败，先修复 3a 基线；不要在 3b transport 内绕过。

---

## 文件结构与职责

- `matmaster/providers/transports/anthropic_messages.py`：新增 native Anthropic Messages transport；包含 provider-local prompt cache options、wire conversion、request kwargs、response/stream normalization、usage/finish_reason 映射、error classification。
- `matmaster/providers/llm_factory.py`：新增 `_build_anthropic_messages_transport()`，注册 `"anthropic_messages"`；把 `LLMProfileConfig.prompt_cache` 转成 transport-local `AnthropicPromptCacheOptions`。
- `matmaster/config/llm.py`：新增纯数据 `PromptCacheConfig`，`LLMProfileConfig.prompt_cache` 可选字段。
- `config/llm_config.yaml`：新增 `litellm-anthropic` / `anthropic` provider 连接与 `global.anthropic.claude-opus-4-6-v1` profile。
- `tests/matmaster/providers/test_anthropic_messages_convert.py`：system 抽取、image、assistant 重建、tool_result 放置、tool/tool_choice 映射。
- `tests/matmaster/providers/test_anthropic_messages_prompt_cache.py`：cache target 选择和 native 注入。
- `tests/matmaster/providers/test_anthropic_messages_stream.py`：Anthropic SSE 事件归一、thinking/provider_state、usage、finish_reason。
- `tests/matmaster/providers/test_anthropic_messages_chat.py`：非流式 `chat()` 走 `messages.stream().get_final_message()` 与 `normalize_response()`。
- `tests/matmaster/providers/test_anthropic_messages_errors.py`：anthropic SDK 异常到 `LLMError` 分类。
- `tests/matmaster/providers/test_llm_factory.py`、`tests/matmaster/config/test_llm.py`、`tests/matmaster/config/test_loader.py`：factory/config 回归。

---

## Task 1: 配置 schema 加 `PromptCacheConfig`

**Files:**
- Modify: `matmaster/config/llm.py`
- Test: `tests/matmaster/config/test_llm.py`

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/config/test_llm.py` 顶部 import 列表加入 `PromptCacheConfig`，并新增：

```python
class TestPromptCacheConfig:
    def test_defaults_match_anthropic_native_policy(self) -> None:
        cfg = PromptCacheConfig()

        assert cfg.system_prompt_breakpoint is False
        assert cfg.automatic is False
        assert cfg.latest_user_breakpoint is True
        assert cfg.tool_result_breakpoint is False
        assert cfg.flexible_breakpoint is False
        assert cfg.max_breakpoints == 4
        assert cfg.min_flexible_chars == 1000
        assert cfg.ttl == "5m"
        assert cfg.cache_control() == {"type": "ephemeral"}

    def test_cache_control_includes_one_hour_ttl_only_when_requested(self) -> None:
        cfg = PromptCacheConfig(ttl="1h")

        assert cfg.cache_control() == {"type": "ephemeral", "ttl": "1h"}

    def test_profile_accepts_prompt_cache(self) -> None:
        profile = LLMProfileConfig(
            provider="litellm-anthropic",
            model="claude-opus-4-6",
            context_limit=200_000,
            prompt_cache={
                "system_prompt_breakpoint": True,
                "automatic": True,
                "latest_user_breakpoint": True,
                "tool_result_breakpoint": True,
                "flexible_breakpoint": True,
                "max_breakpoints": 4,
                "min_flexible_chars": 1000,
                "ttl": "1h",
            },
        )

        assert isinstance(profile.prompt_cache, PromptCacheConfig)
        assert profile.prompt_cache.cache_control() == {
            "type": "ephemeral",
            "ttl": "1h",
        }
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/config/test_llm.py::TestPromptCacheConfig -q
```

Expected: FAIL，`PromptCacheConfig` 还不存在。

- [ ] **Step 3: 实现 schema**

在 `matmaster/config/llm.py` 中 `ProviderConfig` 后、`LLMProfileConfig` 前加入：

```python
class PromptCacheConfig(BaseModel):
    """Profile-level prompt cache policy consumed by native Anthropic transport."""

    system_prompt_breakpoint: bool = False
    automatic: bool = False
    latest_user_breakpoint: bool = True
    tool_result_breakpoint: bool = False
    flexible_breakpoint: bool = False
    max_breakpoints: int = Field(default=4, ge=1, le=4)
    min_flexible_chars: int = Field(default=1000, ge=1)
    ttl: Literal["5m", "1h"] = "5m"

    def cache_control(self) -> dict[str, str]:
        cc = {"type": "ephemeral"}
        if self.ttl == "1h":
            cc["ttl"] = "1h"
        return cc
```

在 `LLMProfileConfig` 末尾加入：

```python
    prompt_cache: PromptCacheConfig | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/config/test_llm.py::TestPromptCacheConfig -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add matmaster/config/llm.py tests/matmaster/config/test_llm.py
git commit -m "feat(config): add prompt cache profile schema"
```

---

## Task 2: 新建 Anthropic transport 骨架与 factory dispatch

**Files:**
- Create: `matmaster/providers/transports/anthropic_messages.py`
- Modify: `matmaster/providers/llm_factory.py`
- Test: `tests/matmaster/providers/test_llm_factory.py`
- Test: `tests/matmaster/providers/test_anthropic_messages_chat.py`

- [ ] **Step 1: 写失败的 factory 测试**

在 `tests/matmaster/providers/test_llm_factory.py` import 区加入：

```python
from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    AnthropicPromptCacheOptions,
)
```

把 `TestDispatch.test_unknown_transport_fail_fast` 中的 provider transport 改为 `"ghost_transport"`，并新增：

```python
def test_anthropic_messages_tag_hits_builder() -> None:
    assert "anthropic_messages" in _TRANSPORT_BUILDERS


def test_anthropic_messages_builder_receives_profile_and_cache() -> None:
    cfg = LLMConfig(
        providers={
            "litellm-anthropic": ProviderConfig(
                transport="anthropic_messages",
                api_key="sk-proxy",
                base_url="https://proxy.example/anthropic",
            )
        },
        profiles={
            "global.anthropic.claude-opus-4-6-v1": LLMProfileConfig(
                provider="litellm-anthropic",
                model="claude-opus-4-6",
                reasoning_effort="max",
                context_limit=200_000,
                supports_vision=True,
                timeout=1200,
                stream_timeout=120,
                stream_idle_timeout=60,
                max_retries=3,
                retry_delay=1.0,
                prompt_cache={
                    "system_prompt_breakpoint": True,
                    "automatic": True,
                    "latest_user_breakpoint": True,
                    "tool_result_breakpoint": True,
                    "flexible_breakpoint": True,
                    "max_breakpoints": 4,
                    "min_flexible_chars": 1000,
                    "ttl": "5m",
                },
            )
        },
        default="global.anthropic.claude-opus-4-6-v1",
    )

    provider = build_provider(cfg)

    assert isinstance(provider, AnthropicMessagesTransport)
    assert provider._model == "claude-opus-4-6"
    assert provider._api_key == "sk-proxy"
    assert provider._base_url == "https://proxy.example/anthropic"
    assert provider._reasoning_effort == "max"
    assert provider._max_tokens is None
    assert provider._prompt_cache_options == AnthropicPromptCacheOptions(
        system_prompt_breakpoint=True,
        cache_control={"type": "ephemeral"},
        automatic=True,
        latest_user_breakpoint=True,
        tool_result_breakpoint=True,
        flexible_breakpoint=True,
        max_breakpoints=4,
        min_flexible_chars=1000,
    )
```

- [ ] **Step 2: 写失败的 transport 构造测试**

Create `tests/matmaster/providers/test_anthropic_messages_chat.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.llm_provider import LLMProvider


class TestConstruction:
    def test_protocol_conformance(self) -> None:
        provider = AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")

        assert isinstance(provider, LLMProvider)
        assert provider.transport_tag == "anthropic_messages"

    async def test_client_uses_base_url_and_disables_sdk_retries(self) -> None:
        provider = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
            base_url="https://proxy.example/anthropic",
            timeout=1200.0,
            stream_timeout=120.0,
            stream_idle_timeout=60.0,
        )
        with patch(
            "matmaster.providers.transports.anthropic_messages.anthropic.AsyncAnthropic"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client

            async with provider:
                pass

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["api_key"] == "sk-test"
            assert kwargs["base_url"] == "https://proxy.example/anthropic"
            assert kwargs["max_retries"] == 0
            assert kwargs["http_client"].timeout.read == 130.0
```

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_anthropic_messages_chat.py::TestConstruction \
  tests/matmaster/providers/test_llm_factory.py::TestDispatch \
  -q
```

Expected: FAIL，`anthropic_messages.py` 还不存在或 builder 未注册。

- [ ] **Step 4: 新增 transport 骨架**

Create `matmaster/providers/transports/anthropic_messages.py`:

```python
"""Native Anthropic Messages transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic

from matmaster.providers.transport import Transport
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import LLMResponse, Message, StreamChunk


@dataclass(frozen=True)
class AnthropicPromptCacheOptions:
    """Provider-local Anthropic prompt cache controls."""

    system_prompt_breakpoint: bool
    cache_control: dict[str, str]
    automatic: bool = False
    latest_user_breakpoint: bool = True
    tool_result_breakpoint: bool = False
    flexible_breakpoint: bool = False
    max_breakpoints: int = 4
    min_flexible_chars: int = 1000


class AnthropicMessagesTransport(Transport):
    """Native Anthropic Messages API transport."""

    transport_tag = "anthropic_messages"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_options: AnthropicPromptCacheOptions | None = None,
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
        self._max_tokens = max_tokens
        self._reasoning_effort = reasoning_effort
        self._prompt_cache_options = prompt_cache_options

    async def _open_client(self) -> anthropic.AsyncAnthropic:
        import httpx

        read_t = float(max(self.stream_idle_timeout, self.stream_timeout) + 10)
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=read_t, write=30.0, pool=15.0)
        )
        return anthropic.AsyncAnthropic(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def _close_client(self, client: anthropic.AsyncAnthropic) -> None:
        await client.close()

    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        validate_tool_turn_sequence(messages)
        return []

    def build_kwargs(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def normalize_response(self, raw: Any) -> LLMResponse:
        raise NotImplementedError

    async def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError
        yield StreamChunk()

    def classify_error(self, exc: Exception) -> LLMError | None:
        if isinstance(exc, LLMError):
            return None
        return None

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        raise NotImplementedError

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError
        yield StreamChunk()
```

- [ ] **Step 5: 注册 factory builder**

在 `matmaster/providers/llm_factory.py` import 区加入：

```python
from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    AnthropicPromptCacheOptions,
)
```

在 `_build_chat_completions_transport` 后加入：

```python
def _build_anthropic_prompt_cache_options(
    profile: LLMProfileConfig,
) -> AnthropicPromptCacheOptions | None:
    prompt_cache = profile.prompt_cache
    if prompt_cache is None:
        return None
    return AnthropicPromptCacheOptions(
        system_prompt_breakpoint=prompt_cache.system_prompt_breakpoint,
        cache_control=prompt_cache.cache_control(),
        automatic=prompt_cache.automatic,
        latest_user_breakpoint=prompt_cache.latest_user_breakpoint,
        tool_result_breakpoint=prompt_cache.tool_result_breakpoint,
        flexible_breakpoint=prompt_cache.flexible_breakpoint,
        max_breakpoints=prompt_cache.max_breakpoints,
        min_flexible_chars=prompt_cache.min_flexible_chars,
    )


def _build_anthropic_messages_transport(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> AnthropicMessagesTransport:
    if extra_body:
        raise ValueError("anthropic_messages transport does not support extra_body")
    return AnthropicMessagesTransport(
        model=profile.model,
        api_key=provider.api_key,
        base_url=provider.base_url,
        max_tokens=profile.max_tokens,
        reasoning_effort=profile.reasoning_effort,
        prompt_cache_options=_build_anthropic_prompt_cache_options(profile),
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
    )
```

更新 dispatch 表：

```python
_TRANSPORT_BUILDERS: dict[str, Callable[..., LLMProvider]] = {
    "chat_completions": _build_chat_completions_transport,
    "anthropic_messages": _build_anthropic_messages_transport,
}
```

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_anthropic_messages_chat.py::TestConstruction \
  tests/matmaster/providers/test_llm_factory.py \
  -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add \
  matmaster/providers/transports/anthropic_messages.py \
  matmaster/providers/llm_factory.py \
  tests/matmaster/providers/test_anthropic_messages_chat.py \
  tests/matmaster/providers/test_llm_factory.py
git commit -m "feat(providers): register anthropic messages transport"
```

---

## Task 3: `convert_messages` 与 request kwargs

**Files:**
- Modify: `matmaster/providers/transports/anthropic_messages.py`
- Test: `tests/matmaster/providers/test_anthropic_messages_convert.py`

- [ ] **Step 1: 写转换测试**

Create `tests/matmaster/providers/test_anthropic_messages_convert.py`:

```python
from __future__ import annotations

import pytest

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.errors import LLMError
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    ProviderState,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _provider(**kwargs) -> AnthropicMessagesTransport:
    return AnthropicMessagesTransport(
        model="claude-opus-4-6",
        api_key="sk-test",
        reasoning_effort="max",
        **kwargs,
    )


def test_build_kwargs_extracts_system_and_omits_temperature() -> None:
    kwargs = _provider().build_kwargs(
        [SystemMessage(content="sys"), UserMessage(content="hi")],
        tools=None,
    )

    assert kwargs["model"] == "claude-opus-4-6"
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "max"}
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs


def test_user_text_and_images_convert_to_anthropic_blocks() -> None:
    msg = UserMessage(
        content="look",
        images=[
            ImageContentPart(url="data:image/png;base64,AAAA", mime_type="image/png"),
            ImageContentPart(url="https://example.com/a.png"),
        ],
    )

    assert _provider().convert_messages([msg]) == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "AAAA",
                    },
                },
                {
                    "type": "image",
                    "source": {"type": "url", "url": "https://example.com/a.png"},
                },
            ],
        }
    ]


def test_assistant_replays_matching_thinking_before_tool_use() -> None:
    provider = _provider()
    state = ProviderState(
        transport="anthropic_messages",
        payload={
            "thinking": [
                {"type": "thinking", "thinking": "plan", "signature": "sig-1"},
            ]
        },
    )
    msg = AssistantMessage(
        content="",
        provider_state=state,
        tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={"q": "x"})],
    )

    assert provider.convert_messages([msg]) == [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "plan", "signature": "sig-1"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search",
                    "input": {"q": "x"},
                },
            ],
        }
    ]


def test_mismatched_provider_state_is_discarded_but_content_and_tools_remain() -> None:
    msg = AssistantMessage(
        content="visible",
        provider_state=ProviderState(
            transport="chat_completions",
            payload={"thinking": [{"type": "thinking", "thinking": "bad", "signature": "x"}]},
        ),
        tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={})],
    )

    assert _provider().convert_messages([msg]) == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "visible"},
                {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {}},
            ],
        }
    ]


def test_parallel_tool_results_are_merged_into_single_user_message_before_text() -> None:
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallData(id="toolu_a", name="a", arguments={}),
                ToolCallData(id="toolu_b", name="b", arguments={}),
            ],
        ),
        ToolMessage(content="A", tool_call_id="toolu_a", tool_name="a"),
        ToolMessage(content="B", tool_call_id="toolu_b", tool_name="b"),
        UserMessage(content="next"),
    ]

    assert _provider().convert_messages(messages) == [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_a", "name": "a", "input": {}},
                {"type": "tool_use", "id": "toolu_b", "name": "b", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_a", "content": "A"},
                {"type": "tool_result", "tool_use_id": "toolu_b", "content": "B"},
                {"type": "text", "text": "next"},
            ],
        },
    ]


@pytest.mark.parametrize("tool_choice", ["required", "any", {"type": "function", "function": {"name": "x"}}])
def test_tool_choice_forced_modes_fail_fast_under_thinking(tool_choice) -> None:
    with pytest.raises(LLMError) as exc_info:
        _provider().build_kwargs([UserMessage(content="hi")], tools=[], tool_choice=tool_choice)

    assert exc_info.value.retryable is False
    assert exc_info.value.error_category == "bad_request"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/providers/test_anthropic_messages_convert.py -q
```

Expected: FAIL，conversion 还返回空列表或 kwargs 未实现。

- [ ] **Step 3: 增加 conversion helper**

在 `anthropic_messages.py` 中 import message classes：

```python
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    LLMResponse,
    Message,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
```

加入 helper：

```python
def _text_block(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    return [{"type": "text", "text": text}]


def _image_block(image: ImageContentPart) -> dict[str, Any]:
    url = image.url
    if url.startswith("data:") and ";base64," in url:
        header, data = url.split(";base64,", 1)
        media_type = image.mime_type or header.removeprefix("data:") or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _user_content_blocks(message: UserMessage) -> list[dict[str, Any]]:
    blocks = _text_block(message.content)
    blocks.extend(_image_block(image) for image in message.images)
    return blocks


def _tool_use_blocks(tool_calls: list[ToolCallData] | None) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_use",
            "id": tc.id,
            "name": tc.name,
            "input": tc.arguments,
        }
        for tc in (tool_calls or [])
    ]


def _tool_result_block(message: ToolMessage) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": message.content or "",
    }
```

- [ ] **Step 4: 实现 assistant 重建**

在 class 前加入：

```python
def _thinking_blocks_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    raw = payload.get("thinking")
    if not isinstance(raw, list):
        return []
    return [dict(block) for block in raw if isinstance(block, dict)]
```

在 `AnthropicMessagesTransport` 内加入：

```python
    def _assistant_to_wire(self, message: AssistantMessage) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        blocks.extend(_thinking_blocks_from_payload(self._claim_provider_state(message)))
        blocks.extend(_text_block(message.content))
        blocks.extend(_tool_use_blocks(message.tool_calls))
        return {"role": "assistant", "content": blocks}
```

- [ ] **Step 5: 实现 tool_result 放置扫描**

替换 `convert_messages`：

```python
    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        validate_tool_turn_sequence(messages)
        out: list[dict[str, Any]] = []
        idx = 0
        while idx < len(messages):
            message = messages[idx]
            if isinstance(message, SystemMessage):
                idx += 1
                continue
            if isinstance(message, UserMessage):
                out.append({"role": "user", "content": _user_content_blocks(message)})
                idx += 1
                continue
            if isinstance(message, AssistantMessage):
                out.append(self._assistant_to_wire(message))
                idx += 1
                if not message.tool_calls:
                    continue
                result_blocks: list[dict[str, Any]] = []
                while idx < len(messages) and isinstance(messages[idx], ToolMessage):
                    result_blocks.append(_tool_result_block(messages[idx]))
                    idx += 1
                if idx < len(messages) and isinstance(messages[idx], UserMessage):
                    result_blocks.extend(_user_content_blocks(messages[idx]))
                    idx += 1
                if result_blocks:
                    out.append({"role": "user", "content": result_blocks})
                continue
            if isinstance(message, ToolMessage):
                out.append({"role": "user", "content": [_tool_result_block(message)]})
                idx += 1
                continue
            idx += 1
        return out
```

- [ ] **Step 6: 实现 tools/tool_choice/build_kwargs**

加入 helper：

```python
def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", {})
        item = {
            "name": function["name"],
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        }
        if function.get("description"):
            item["description"] = function["description"]
        converted.append(item)
    return converted


def _map_tool_choice(tool_choice: str | dict | None) -> dict[str, str] | None:
    if tool_choice is None or tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "none"}
    raise LLMError(
        "anthropic_messages with thinking enabled supports only tool_choice auto/none",
        retryable=False,
        error_category="bad_request",
    )
```

替换 `build_kwargs`：

```python
    def build_kwargs(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        system_messages = [m for m in messages if isinstance(m, SystemMessage)]
        system_text = "\n\n".join(m.content or "" for m in system_messages).strip()
        converted_tools = _convert_tools(tools)
        mapped_tool_choice = _map_tool_choice(tool_choice)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self.convert_messages(messages),
            "thinking": {"type": "adaptive", "display": "summarized"},
        }
        if system_text:
            kwargs["system"] = system_text
        if self._reasoning_effort:
            kwargs["output_config"] = {"effort": self._reasoning_effort}
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        if converted_tools:
            kwargs["tools"] = converted_tools
            if mapped_tool_choice is not None:
                kwargs["tool_choice"] = mapped_tool_choice
        elif tool_choice == "none":
            kwargs["tool_choice"] = {"type": "none"}
        if stream:
            kwargs["stream"] = True
        return kwargs
```

- [ ] **Step 7: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/providers/test_anthropic_messages_convert.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add \
  matmaster/providers/transports/anthropic_messages.py \
  tests/matmaster/providers/test_anthropic_messages_convert.py
git commit -m "feat(providers): convert messages for anthropic transport"
```

---

## Task 4: native prompt cache 选择与注入

**Files:**
- Modify: `matmaster/providers/transports/anthropic_messages.py`
- Test: `tests/matmaster/providers/test_anthropic_messages_prompt_cache.py`

- [ ] **Step 1: 写 prompt cache 测试**

Create `tests/matmaster/providers/test_anthropic_messages_prompt_cache.py`:

```python
from __future__ import annotations

from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    AnthropicPromptCacheOptions,
)
from matmaster.types.messages import AssistantMessage, SystemMessage, ToolCallData, ToolMessage, UserMessage


def _provider(**overrides) -> AnthropicMessagesTransport:
    values = {
        "system_prompt_breakpoint": True,
        "cache_control": {"type": "ephemeral"},
        "automatic": True,
        "latest_user_breakpoint": True,
        "tool_result_breakpoint": True,
        "flexible_breakpoint": True,
        "max_breakpoints": 4,
        "min_flexible_chars": 10,
    }
    values.update(overrides)
    options = AnthropicPromptCacheOptions(**values)
    return AnthropicMessagesTransport(
        model="claude-opus-4-6",
        api_key="sk-test",
        prompt_cache_options=options,
    )


def test_cache_marks_system_latest_user_and_tool_result_with_automatic_slot() -> None:
    provider = _provider()
    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={})],
            ),
            ToolMessage(content="tool result", tool_call_id="toolu_1", tool_name="search"),
            UserMessage(content="current"),
        ],
        tools=[{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}],
    )

    assert kwargs["system"] == [
        {"type": "text", "text": "system prompt", "cache_control": {"type": "ephemeral"}}
    ]
    assert "cache_control" not in kwargs["messages"][0]["content"][0]
    assert kwargs["messages"][2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][2]["content"][1]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["extra_body"]["cache_control"] == {"type": "ephemeral"}


def test_cache_uses_flexible_when_fixed_targets_leave_a_slot() -> None:
    provider = _provider(tool_result_breakpoint=False)

    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            UserMessage(content="current"),
        ],
        tools=None,
    )

    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_cache_respects_max_breakpoints_after_automatic_slot() -> None:
    provider = _provider(max_breakpoints=2)

    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            UserMessage(content="current"),
        ],
        tools=None,
    )

    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kwargs["messages"][-1]["content"][0]
    assert "cache_control" not in kwargs["messages"][0]["content"][0]


def test_one_hour_ttl_is_copied_to_all_cache_controls() -> None:
    provider = _provider(cache_control={"type": "ephemeral", "ttl": "1h"})

    kwargs = provider.build_kwargs(
        [SystemMessage(content="system prompt"), UserMessage(content="current")],
        tools=None,
    )

    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert kwargs["messages"][0]["content"][0]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert kwargs["extra_body"]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_no_prompt_cache_options_leaves_payload_unmarked() -> None:
    provider = AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")

    kwargs = provider.build_kwargs(
        [SystemMessage(content="system prompt"), UserMessage(content="current")],
        tools=None,
    )

    assert kwargs["system"] == "system prompt"
    assert kwargs["messages"][0]["content"] == [{"type": "text", "text": "current"}]
    assert "extra_body" not in kwargs
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/providers/test_anthropic_messages_prompt_cache.py -q
```

Expected: FAIL，cache 注入尚未实现。

- [ ] **Step 3: 添加 cache target helper**

在 `anthropic_messages.py` 中加入：

```python
@dataclass(frozen=True)
class _CacheTarget:
    section: str  # "system" | "message"
    index: int
    content_index: int | None
    priority: int


def _message_text_size(message: dict[str, Any]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content.strip())
    if isinstance(content, list):
        return sum(
            len(part["text"].strip())
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return 0


def _select_flexible_cache_target(
    messages: list[dict[str, Any]],
    used: set[int],
    options: AnthropicPromptCacheOptions,
) -> _CacheTarget | None:
    candidates: list[tuple[int, int]] = []
    for idx, message in enumerate(messages):
        if idx in used:
            continue
        if message.get("role") != "user":
            continue
        size = _message_text_size(message)
        if size >= options.min_flexible_chars:
            candidates.append((size, idx))
    if not candidates:
        return None
    _, idx = max(candidates)
    return _CacheTarget("message", idx, None, 3)


def _select_anthropic_cache_targets(
    *,
    has_system: bool,
    messages: list[dict[str, Any]],
    options: AnthropicPromptCacheOptions,
) -> list[_CacheTarget]:
    targets: list[_CacheTarget] = []
    used_slots: set[tuple[int, int | None]] = set()
    max_block_targets = options.max_breakpoints - (1 if options.automatic else 0)
    max_block_targets = max(0, max_block_targets)

    def append(target: _CacheTarget) -> None:
        if len(targets) >= max_block_targets:
            return
        slot = (target.index, target.content_index)
        if target.section == "message" and slot in used_slots:
            return
        targets.append(target)
        if target.section == "message":
            used_slots.add(slot)

    if options.system_prompt_breakpoint and has_system:
        append(_CacheTarget("system", 0, None, 0))
    if options.automatic and options.latest_user_breakpoint:
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "user":
                append(_CacheTarget("message", idx, None, 1))
                break
    if options.automatic and options.tool_result_breakpoint:
        for idx in range(len(messages) - 1, -1, -1):
            blocks = messages[idx].get("content")
            if messages[idx].get("role") != "user" or not isinstance(blocks, list):
                continue
            result_indexes = [
                block_idx
                for block_idx, block in enumerate(blocks)
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            if result_indexes:
                append(_CacheTarget("message", idx, result_indexes[-1], 2))
                break
    if options.automatic and options.flexible_breakpoint:
        used_message_indexes = {idx for idx, block_idx in used_slots if block_idx is None}
        flexible = _select_flexible_cache_target(messages, used_message_indexes, options)
        if flexible is not None:
            append(flexible)
    return targets
```

- [ ] **Step 4: 添加注入 helper**

加入：

```python
def _with_cache_control(block: dict[str, Any], cache_control: dict[str, str]) -> dict[str, Any]:
    out = dict(block)
    out["cache_control"] = dict(cache_control)
    return out


def _mark_content_block(
    message: dict[str, Any],
    cache_control: dict[str, str],
    content_index: int | None = None,
) -> None:
    content = message.get("content")
    if isinstance(content, list) and content:
        idx = content_index if content_index is not None else len(content) - 1
        content[idx] = _with_cache_control(content[idx], cache_control)
        return
    if isinstance(content, str):
        message["content"] = [
            {"type": "text", "text": content, "cache_control": dict(cache_control)}
        ]
```

- [ ] **Step 5: 在 `build_kwargs` 中应用 cache**

在 `build_kwargs` 构造 `kwargs` 前，将 system/messages 分离为变量：

```python
        converted_messages = self.convert_messages(messages)
        system_value: str | list[dict[str, Any]] | None = system_text or None
        options = self._prompt_cache_options
        if options is not None:
            targets = _select_anthropic_cache_targets(
                has_system=bool(system_value),
                messages=converted_messages,
                options=options,
            )
            for target in targets:
                if target.section == "system" and isinstance(system_value, str):
                    system_value = [
                        {
                            "type": "text",
                            "text": system_value,
                            "cache_control": dict(options.cache_control),
                        }
                    ]
                elif target.section == "message":
                    _mark_content_block(
                        converted_messages[target.index],
                        options.cache_control,
                        target.content_index,
                    )
            if options.automatic:
                kwargs_extra_body = {"cache_control": dict(options.cache_control)}
            else:
                kwargs_extra_body = {}
        else:
            kwargs_extra_body = {}
```

然后 `kwargs["messages"]` 使用 `converted_messages`，`kwargs["system"]` 使用 `system_value`，并在末尾加入：

```python
        if kwargs_extra_body:
            kwargs["extra_body"] = kwargs_extra_body
```

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/providers/test_anthropic_messages_prompt_cache.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add \
  matmaster/providers/transports/anthropic_messages.py \
  tests/matmaster/providers/test_anthropic_messages_prompt_cache.py
git commit -m "feat(providers): add native anthropic prompt cache"
```

---

## Task 5: `normalize_response()` 与 `chat()`

**Files:**
- Modify: `matmaster/providers/transports/anthropic_messages.py`
- Test: `tests/matmaster/providers/test_anthropic_messages_chat.py`

- [ ] **Step 1: 增加非流式响应测试**

在 `tests/matmaster/providers/test_anthropic_messages_chat.py` 追加：

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

from matmaster.types.messages import ProviderState, ToolCallData, UserMessage


def _usage(**kwargs):
    return SimpleNamespace(**kwargs)


class TestNormalizeResponse:
    def test_extracts_text_tool_calls_thinking_state_usage_and_finish_reason(self) -> None:
        raw = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking="plan", signature="sig-1"),
                SimpleNamespace(type="text", text="hello"),
                SimpleNamespace(type="tool_use", id="toolu_1", name="search", input={"q": "x"}),
            ],
            stop_reason="tool_use",
            usage=_usage(
                input_tokens=10,
                output_tokens=5,
                cache_read_input_tokens=3,
                cache_creation_input_tokens=4,
            ),
        )

        result = AnthropicMessagesTransport(
            model="claude-opus-4-6",
            api_key="sk-test",
        ).normalize_response(raw)

        assert result.content == "hello"
        assert result.reasoning_content == "plan"
        assert result.tool_calls == [ToolCallData(id="toolu_1", name="search", arguments={"q": "x"})]
        assert result.finish_reason == "tool_calls"
        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 3,
            "cache_write_tokens": 4,
        }
        assert result.provider_state == ProviderState(
            transport="anthropic_messages",
            payload={"thinking": [{"type": "thinking", "thinking": "plan", "signature": "sig-1"}]},
        )
        assert result.usage_vendor is not None


class TestChat:
    async def test_chat_uses_stream_get_final_message(self) -> None:
        provider = AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")
        final = SimpleNamespace(content=[], stop_reason="end_turn", usage=None)
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        stream_cm.get_final_message = AsyncMock(return_value=final)
        mock_client = MagicMock()
        mock_client.messages.stream.return_value = stream_cm
        provider._client = mock_client

        result = await provider.chat([UserMessage(content="hi")], tool_choice="none")

        assert result.finish_reason == "stop"
        assert mock_client.messages.stream.call_args.kwargs["tool_choice"] == {"type": "none"}
        stream_cm.get_final_message.assert_awaited_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/providers/test_anthropic_messages_chat.py -q
```

Expected: FAIL，response normalization 和 chat 尚未实现。

- [ ] **Step 3: 添加 usage 与 finish_reason helper**

在 `anthropic_messages.py` 中加入：

```python
def _dump_model(value: Any) -> Any:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json", exclude_none=True)
        except TypeError:
            return model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    out: dict[str, Any] = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        try:
            item = getattr(value, key)
        except Exception:
            continue
        if isinstance(item, (str, int, float, bool, type(None), dict, list)):
            out[key] = item
    return out


def _anthropic_usage_to_scalar_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    out = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    cache_read = getattr(usage, "cache_read_input_tokens", None)
    if isinstance(cache_read, int) and cache_read > 0:
        out["cache_read_tokens"] = cache_read
    cache_write = getattr(usage, "cache_creation_input_tokens", None)
    if isinstance(cache_write, int) and cache_write > 0:
        out["cache_write_tokens"] = cache_write
    details = getattr(usage, "output_tokens_details", None)
    reasoning = getattr(details, "thinking_tokens", None) if details is not None else None
    if isinstance(reasoning, int) and reasoning > 0:
        out["reasoning_tokens"] = reasoning
    return out


def _map_stop_reason(stop_reason: str | None) -> str | None:
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "refusal": "content_filter",
        "pause_turn": "stop",
    }.get(stop_reason, stop_reason)
```

- [ ] **Step 4: 实现 `normalize_response()`**

替换 `normalize_response`：

```python
    def normalize_response(self, raw: Any) -> LLMResponse:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        thinking_blocks: list[dict[str, Any]] = []
        tool_calls: list[ToolCallData] = []
        for block in getattr(raw, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "thinking":
                thinking = getattr(block, "thinking", "")
                signature = getattr(block, "signature", None)
                payload = {"type": "thinking", "thinking": thinking}
                if signature:
                    payload["signature"] = signature
                thinking_blocks.append(payload)
                if thinking:
                    reasoning_parts.append(thinking)
            elif block_type == "redacted_thinking":
                dumped = _dump_model(block)
                if isinstance(dumped, dict):
                    thinking_blocks.append(dumped)
            elif block_type == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCallData(
                        id=getattr(block, "id"),
                        name=getattr(block, "name"),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )
        provider_state = None
        if thinking_blocks:
            from matmaster.types.messages import ProviderState

            provider_state = ProviderState(
                transport=self.transport_tag,
                payload={"thinking": thinking_blocks},
            )
        usage = getattr(raw, "usage", None)
        return LLMResponse(
            content="".join(text_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls or None,
            finish_reason=_map_stop_reason(getattr(raw, "stop_reason", None)),
            usage=_anthropic_usage_to_scalar_dict(usage),
            usage_vendor=_dump_model(usage) if usage is not None else None,
            provider_state=provider_state,
        )
```

- [ ] **Step 5: 实现 `chat()`**

替换 `chat`：

```python
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools, tool_choice=tool_choice)
        async with client.messages.stream(**kwargs) as stream:
            final = await stream.get_final_message()
        return self.normalize_response(final)
```

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/providers/test_anthropic_messages_chat.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add \
  matmaster/providers/transports/anthropic_messages.py \
  tests/matmaster/providers/test_anthropic_messages_chat.py
git commit -m "feat(providers): normalize anthropic messages responses"
```

---

## Task 6: streaming normalization 与 signed thinking provider_state

**Files:**
- Modify: `matmaster/providers/transports/anthropic_messages.py`
- Test: `tests/matmaster/providers/test_anthropic_messages_stream.py`
- Test: `tests/matmaster/core/test_provider_state_aggregation.py`

- [ ] **Step 1: 写 stream 测试**

Create `tests/matmaster/providers/test_anthropic_messages_stream.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.messages import ProviderState


async def _aiter(items):
    for item in items:
        yield item


def _event(event_type: str, **kwargs):
    return SimpleNamespace(type=event_type, **kwargs)


class TestNormalizeStream:
    async def test_stream_emits_reasoning_text_tool_delta_state_and_usage(self) -> None:
        provider = AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")
        events = [
            _event("message_start", message=SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=0))),
            _event("content_block_start", index=0, content_block=SimpleNamespace(type="thinking")),
            _event("content_block_delta", index=0, delta=SimpleNamespace(type="thinking_delta", thinking="plan")),
            _event("content_block_delta", index=0, delta=SimpleNamespace(type="signature_delta", signature="sig-1")),
            _event("content_block_stop", index=0),
            _event("content_block_start", index=1, content_block=SimpleNamespace(type="text")),
            _event("content_block_delta", index=1, delta=SimpleNamespace(type="text_delta", text="hello")),
            _event("content_block_stop", index=1),
            _event("content_block_start", index=2, content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="search")),
            _event("content_block_delta", index=2, delta=SimpleNamespace(type="input_json_delta", partial_json='{"q"')),
            _event("content_block_delta", index=2, delta=SimpleNamespace(type="input_json_delta", partial_json=':"x"}')),
            _event("content_block_stop", index=2),
            _event("message_delta", delta=SimpleNamespace(stop_reason="tool_use"), usage=SimpleNamespace(output_tokens=5)),
        ]

        chunks = [chunk async for chunk in provider.normalize_stream(_aiter(events))]

        assert chunks[0].reasoning_content == "plan"
        assert chunks[1].content == "hello"
        assert chunks[2].tool_call_deltas == [{"index": 0, "id": "toolu_1", "name": "search"}]
        assert chunks[3].tool_call_deltas == [{"index": 0, "arguments": '{"q"'}]
        assert chunks[4].tool_call_deltas == [{"index": 0, "arguments": ':"x"}'}]
        assert chunks[5].finish_reason == "tool_calls"
        assert chunks[6].provider_state == ProviderState(
            transport="anthropic_messages",
            payload={"thinking": [{"type": "thinking", "thinking": "plan", "signature": "sig-1"}]},
        )
        assert chunks[7].usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
```

- [ ] **Step 2: 增加聚合回归测试**

在 `tests/matmaster/core/test_provider_state_aggregation.py` 追加：

```python
@pytest.mark.asyncio
async def test_anthropic_style_provider_state_overwrites_prior_none_chunks():
    from types import SimpleNamespace

    from matmaster.core.agent_llm_stream import stream_llm_items

    class _Provider:
        stream_timeout = 30.0
        stream_idle_timeout = 30.0
        max_retries = 1
        retry_delay = 0.0

        async def chat_stream(self, messages, tools=None, *, timeout=None):
            yield StreamChunk(reasoning_content="thinking")
            yield StreamChunk(content="answer")
            yield StreamChunk(finish_reason="stop")
            yield StreamChunk(
                provider_state=ProviderState(
                    transport="anthropic_messages",
                    payload={"thinking": [{"type": "thinking", "thinking": "thinking", "signature": "sig"}]},
                )
            )

    final = None
    async for item in stream_llm_items(SimpleNamespace(llm_provider=_Provider()), [], None):
        if item.llm_response is not None:
            final = item.llm_response

    assert final is not None
    assert final.provider_state == ProviderState(
        transport="anthropic_messages",
        payload={"thinking": [{"type": "thinking", "thinking": "thinking", "signature": "sig"}]},
    )
```

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_anthropic_messages_stream.py \
  tests/matmaster/core/test_provider_state_aggregation.py \
  -q
```

Expected: provider stream 测试 FAIL；core 聚合测试 PASS 或保持现有行为。

- [ ] **Step 4: 添加 stream block state**

在 `anthropic_messages.py` 中加入：

```python
@dataclass
class _StreamBlockState:
    type: str
    id: str | None = None
    name: str | None = None
    output_index: int | None = None
    thinking: str = ""
    signature: str | None = None
    arguments: str = ""
```

- [ ] **Step 5: 实现 `normalize_stream()`**

替换 `normalize_stream`：

```python
    async def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        blocks: dict[int, _StreamBlockState] = {}
        thinking_payload: list[dict[str, Any]] = []
        next_tool_call_index = 0
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None

        async for event in raw_iter:
            event_type = getattr(event, "type", None)
            if event_type == "message_start":
                usage = getattr(getattr(event, "message", None), "usage", None)
                input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                continue
            if event_type == "content_block_start":
                block = getattr(event, "content_block", None)
                block_type = getattr(block, "type", "")
                state = _StreamBlockState(
                    type=block_type,
                    id=getattr(block, "id", None),
                    name=getattr(block, "name", None),
                )
                if block_type == "tool_use":
                    state.output_index = next_tool_call_index
                    next_tool_call_index += 1
                blocks[int(getattr(event, "index", 0))] = state
                if block_type == "tool_use":
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                "index": state.output_index,
                                "id": state.id,
                                "name": state.name,
                            }
                        ]
                    )
                continue
            if event_type == "content_block_delta":
                idx = int(getattr(event, "index", 0))
                state = blocks.setdefault(idx, _StreamBlockState(type=""))
                delta = getattr(event, "delta", None)
                delta_type = getattr(delta, "type", None)
                if delta_type == "thinking_delta":
                    text = getattr(delta, "thinking", "") or ""
                    state.thinking += text
                    yield StreamChunk(reasoning_content=text)
                elif delta_type == "signature_delta":
                    state.signature = getattr(delta, "signature", None)
                elif delta_type == "text_delta":
                    yield StreamChunk(content=getattr(delta, "text", "") or "")
                elif delta_type == "input_json_delta":
                    part = getattr(delta, "partial_json", "") or ""
                    state.arguments += part
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                "index": state.output_index
                                if state.output_index is not None
                                else idx,
                                "arguments": part,
                            }
                        ]
                    )
                continue
            if event_type == "content_block_stop":
                idx = int(getattr(event, "index", 0))
                state = blocks.get(idx)
                if state is not None and state.type == "thinking":
                    payload = {"type": "thinking", "thinking": state.thinking}
                    if state.signature:
                        payload["signature"] = state.signature
                    thinking_payload.append(payload)
                continue
            if event_type == "message_delta":
                finish_reason = _map_stop_reason(
                    getattr(getattr(event, "delta", None), "stop_reason", None)
                )
                usage = getattr(event, "usage", None)
                output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                if finish_reason:
                    yield StreamChunk(finish_reason=finish_reason)

        if thinking_payload:
            from matmaster.types.messages import ProviderState

            yield StreamChunk(
                provider_state=ProviderState(
                    transport=self.transport_tag,
                    payload={"thinking": thinking_payload},
                )
            )
        if input_tokens or output_tokens:
            yield StreamChunk(
                usage={
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                }
            )
```

- [ ] **Step 6: 实现 `chat_stream()`**

替换 `chat_stream`：

```python
    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        client = self._ensure_client()
        kwargs = self.build_kwargs(messages, tools, stream=True)
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for chunk in self.normalize_stream(stream):
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            err = self.classify_error(exc)
            if err is not None:
                raise err from exc
            raise
```

- [ ] **Step 7: 运行测试确认通过**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_anthropic_messages_stream.py \
  tests/matmaster/core/test_provider_state_aggregation.py \
  -q
```

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add \
  matmaster/providers/transports/anthropic_messages.py \
  tests/matmaster/providers/test_anthropic_messages_stream.py \
  tests/matmaster/core/test_provider_state_aggregation.py
git commit -m "feat(providers): stream anthropic thinking state"
```

---

## Task 7: anthropic SDK 错误分类

**Files:**
- Modify: `matmaster/providers/transports/anthropic_messages.py`
- Test: `tests/matmaster/providers/test_anthropic_messages_errors.py`

- [ ] **Step 1: 写错误分类测试**

Create `tests/matmaster/providers/test_anthropic_messages_errors.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

import anthropic

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.errors import LLMError


def _provider() -> AnthropicMessagesTransport:
    return AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")


def _bad_request(message: str) -> anthropic.BadRequestError:
    return anthropic.BadRequestError(
        message=message,
        response=MagicMock(status_code=400, headers={}),
        body=None,
    )


def test_existing_llm_error_is_not_rewrapped() -> None:
    assert _provider().classify_error(LLMError("x", retryable=False, error_category="bad_request")) is None


def test_timeout_is_retryable() -> None:
    err = _provider().classify_error(anthropic.APITimeoutError(request=MagicMock()))
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "timeout"


def test_auth_is_non_retryable() -> None:
    err = _provider().classify_error(
        anthropic.AuthenticationError(
            message="invalid key",
            response=MagicMock(status_code=401, headers={}),
            body=None,
        )
    )
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "auth"


def test_context_overflow_bad_request_is_non_retryable() -> None:
    err = _provider().classify_error(_bad_request("context window exceeds token limit"))
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "context_overflow"


def test_signature_bad_request_is_non_retryable() -> None:
    err = _provider().classify_error(_bad_request("thinking signature is invalid"))
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "bad_request"


def test_generic_bad_request_is_retryable() -> None:
    err = _provider().classify_error(_bad_request("temporary invalid request from gateway"))
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "bad_request"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/providers/test_anthropic_messages_errors.py -q
```

Expected: FAIL，分类尚未实现。

- [ ] **Step 3: 实现 helper 与 `classify_error()`**

在 `anthropic_messages.py` 中加入：

```python
def _is_context_overflow(text: str) -> bool:
    lowered = text.lower()
    return "context" in lowered and ("token" in lowered or "length" in lowered or "window" in lowered)


def _is_non_retryable_anthropic_bad_request(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        "signature",
        "thinking",
        "cache_control",
        "tool_result",
        "tool_use",
        "must be immediately after",
        "input_schema",
    )
    return any(pattern in lowered for pattern in patterns)
```

替换 `classify_error`：

```python
    def classify_error(self, exc: Exception) -> LLMError | None:
        if isinstance(exc, LLMError):
            return None
        if isinstance(exc, anthropic.APITimeoutError):
            return LLMError(str(exc), retryable=True, error_category="timeout")
        if isinstance(exc, anthropic.APIConnectionError):
            return LLMError(str(exc), retryable=True, error_category="connection")
        if isinstance(exc, anthropic.RateLimitError):
            return LLMError(str(exc), retryable=True, error_category="rate_limit")
        if isinstance(exc, (anthropic.InternalServerError, anthropic.OverloadedError)):
            return LLMError(str(exc), retryable=True, error_category="server")
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return LLMError(str(exc), retryable=False, error_category="auth")
        if isinstance(exc, anthropic.BadRequestError):
            text = str(exc)
            if _is_context_overflow(text):
                return LLMError(text, retryable=False, error_category="context_overflow")
            if _is_non_retryable_anthropic_bad_request(text):
                return LLMError(text, retryable=False, error_category="bad_request")
            return LLMError(text, retryable=True, error_category="bad_request")
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/providers/test_anthropic_messages_errors.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add \
  matmaster/providers/transports/anthropic_messages.py \
  tests/matmaster/providers/test_anthropic_messages_errors.py
git commit -m "feat(providers): classify anthropic messages errors"
```

---

## Task 8: 项目配置接入 Opus profile

**Files:**
- Modify: `config/llm_config.yaml`
- Modify: `tests/matmaster/config/test_loader.py`

- [ ] **Step 1: 写 loader 回归测试**

在 `tests/matmaster/config/test_loader.py` 的 `TestLoadLlmConfigNormalized` 中追加：

```python
def test_repo_llm_config_includes_native_anthropic_opus(self) -> None:
    repo_root = Path(__file__).resolve().parents[3]

    cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")
    resolved = cfg.resolve(model_override="global.anthropic.claude-opus-4-6-v1")

    assert resolved.provider.transport == "anthropic_messages"
    # NOTE: 不要断言 provider.api_key / base_url 的字面 "${...}"：
    # load_llm_config 的 _expand_env_vars 会无条件展开 ${VAR}（命中环境变量取值，
    # 未命中取空串），占位符绝不原样保留，此类断言随环境漂移、必然失败。
    assert resolved.profile.model == "claude-opus-4-6"
    assert resolved.profile.reasoning_effort == "max"
    assert resolved.profile.supports_vision is True
    assert resolved.profile.prompt_cache is not None
    assert resolved.profile.prompt_cache.system_prompt_breakpoint is True
    assert resolved.profile.prompt_cache.automatic is True
    assert resolved.profile.prompt_cache.latest_user_breakpoint is True
    assert resolved.profile.prompt_cache.tool_result_breakpoint is True
    assert resolved.profile.prompt_cache.flexible_breakpoint is True
    assert resolved.profile.prompt_cache.max_breakpoints == 4
    assert resolved.profile.prompt_cache.min_flexible_chars == 1000
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/config/test_loader.py::TestLoadLlmConfigNormalized -q
```

Expected: FAIL，repo config 尚无 anthropic profile。

- [ ] **Step 3: 更新 `config/llm_config.yaml` providers**

在 `providers:` 下保留现有 `litellm`，追加：

```yaml
  litellm-anthropic:
    transport: anthropic_messages
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_ANTHROPIC_BASE}"

  anthropic:
    transport: anthropic_messages
    api_key: "${ANTHROPIC_API_KEY}"
```

- [ ] **Step 4: 更新 `config/llm_config.yaml` profiles**

在 `profiles:` 下追加：

```yaml
  global.anthropic.claude-opus-4-6-v1:
    provider: litellm-anthropic
    model: claude-opus-4-6
    reasoning_effort: max
    context_limit: 200000
    supports_vision: true
    timeout: 1200
    stream_timeout: 120
    stream_idle_timeout: 60
    max_retries: 3
    retry_delay: 1.0
    prompt_cache:
      system_prompt_breakpoint: true
      automatic: true
      latest_user_breakpoint: true
      tool_result_breakpoint: true
      flexible_breakpoint: true
      max_breakpoints: 4
      min_flexible_chars: 1000
      ttl: "5m"
```

不修改 `default`，继续指向当前默认 profile。

- [ ] **Step 5: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/config/test_loader.py::TestLoadLlmConfigNormalized -q
```

Expected: PASS。

- [ ] **Step 6: 本地环境变量说明**

确认部署环境提供 `LITELLM_PROXY_ANTHROPIC_BASE`。本地 `.env` 可添加该变量与 `ANTHROPIC_API_KEY` 直连备选，但 `.env` 不提交。

Run:

```bash
uv run python - <<'PY'
from matmaster.config.loader import load_llm_config

cfg = load_llm_config("config/llm_config.yaml")
resolved = cfg.resolve(model_override="global.anthropic.claude-opus-4-6-v1")
print(resolved.provider.transport, resolved.profile.model)
PY
```

Expected output contains:

```text
anthropic_messages claude-opus-4-6
```

- [ ] **Step 7: 提交**

```bash
git add config/llm_config.yaml tests/matmaster/config/test_loader.py
git commit -m "feat(config): add native anthropic opus profile"
```

---

## Task 9: 跨协议 provider_state 丢弃与全量回归

**Files:**
- Modify: `tests/matmaster/providers/test_provider_state_claim.py`
- Test-only verification across provider/config/core suites.

- [ ] **Step 1: 增加反向 tag 丢弃测试**

在 `tests/matmaster/providers/test_provider_state_claim.py` import 区加入：

```python
from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.types.messages import ToolCallData
```

追加：

```python
def test_anthropic_convert_discards_chat_completions_state() -> None:
    t = AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")
    msg = AssistantMessage(
        content="visible",
        provider_state=ProviderState(
            transport="chat_completions",
            payload={"thinking": [{"type": "thinking", "thinking": "wrong", "signature": "x"}]},
        ),
        tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={})],
    )

    assert t.convert_messages([msg]) == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "visible"},
                {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {}},
            ],
        }
    ]
```

- [ ] **Step 2: 运行 provider_state tests**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_provider_state_claim.py \
  tests/matmaster/types/test_provider_state.py \
  tests/matmaster/core/test_provider_state_aggregation.py \
  tests/matmaster/core/test_natural_finish_provider_state_persist.py \
  tests/services/test_events_to_messages_provider_state.py \
  -q
```

Expected: PASS。

- [ ] **Step 3: 运行 Anthropic transport suite**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_anthropic_messages_convert.py \
  tests/matmaster/providers/test_anthropic_messages_prompt_cache.py \
  tests/matmaster/providers/test_anthropic_messages_chat.py \
  tests/matmaster/providers/test_anthropic_messages_stream.py \
  tests/matmaster/providers/test_anthropic_messages_errors.py \
  -q
```

Expected: PASS。

- [ ] **Step 4: 运行 existing chat_completions/factory/config 回归**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_chat_completions_provider.py \
  tests/matmaster/providers/test_chat_completions_provider_tool_choice.py \
  tests/matmaster/providers/test_chat_completions_provider_errors.py \
  tests/matmaster/providers/test_llm_factory.py \
  tests/matmaster/providers/test_byok_provider.py \
  tests/matmaster/config/test_llm.py \
  tests/matmaster/config/test_loader.py \
  -q
```

Expected: PASS；BYOK 仍构造 `ChatCompletionsTransport`，不受 `anthropic_messages` 影响。

- [ ] **Step 5: 运行全量测试**

Run:

```bash
uv run pytest tests/ -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add tests/matmaster/providers/test_provider_state_claim.py
git commit -m "test(providers): cover cross-transport provider state discard"
```

---

## Task 10: 手动 smoke 验证（需要真实 Anthropic 端点）

**Files:**
- 不修改仓库文件。

- [ ] **Step 1: 确认环境变量**

Run:

```bash
uv run python - <<'PY'
import os

for key in ("LITELLM_PROXY_API_KEY", "LITELLM_PROXY_ANTHROPIC_BASE"):
    value = os.getenv(key)
    print(key, "set" if value else "missing")
PY
```

Expected: 两行均为 `set`。若 `LITELLM_PROXY_ANTHROPIC_BASE` missing，先在本地 `.env` 或部署 secret 中配置；不要提交 `.env`。

- [ ] **Step 2: 运行一次无工具流式调用 smoke**

Run:

```bash
uv run python - <<'PY'
import asyncio

from matmaster.config.loader import load_llm_config
from matmaster.providers.llm_factory import build_provider
from matmaster.types.messages import UserMessage


async def main():
    cfg = load_llm_config("config/llm_config.yaml")
    provider = build_provider(cfg, model_override="global.anthropic.claude-opus-4-6-v1")
    async with provider:
        chunks = []
        async for chunk in provider.chat_stream([UserMessage(content="Say one short sentence.")]):
            chunks.append(chunk)
        print("content_chunks", sum(1 for c in chunks if c.content))
        print("reasoning_chunks", sum(1 for c in chunks if c.reasoning_content))
        print("state_chunks", sum(1 for c in chunks if c.provider_state))
        print("usage_chunks", sum(1 for c in chunks if c.usage))


asyncio.run(main())
PY
```

Expected: `content_chunks` > 0, `usage_chunks` == 1。`reasoning_chunks` and `state_chunks` should be > 0 when the endpoint returns thinking/signature events; if both are 0, inspect whether the gateway strips Anthropic native thinking.

- [ ] **Step 3: 运行一次 tool 回合 smoke**

Run a small local script that:

1. Calls `chat_stream()` with one OpenAI-style function tool.
2. Accumulates `tool_call_deltas` into a `ToolCallData`.
3. Builds the next request with the prior `AssistantMessage(provider_state=...)`, a matching `ToolMessage`, and a new `UserMessage`.
4. Confirms the second request does not return Anthropic 400.

Expected: 第二轮成功返回 content 或 tool_call；若出现 signature/tool_result 400，优先检查 `convert_messages()` 中 thinking blocks 是否排在 `tool_use` 前，以及 tool_result user message 是否紧跟 assistant tool_use。

---

## 完成标准

- `AnthropicMessagesTransport` 满足 `LLMProvider` Protocol，并显式 `AsyncAnthropic(max_retries=0)`。
- `convert_messages()` 正确处理 system 顶层抽取、User text/image、Assistant thinking/text/tool_use 重建、ToolMessage 合并进紧邻 user 回合，且 tool_result blocks 排在 text/image blocks 前。
- thinking 恒开：`thinking={"type":"adaptive","display":"summarized"}`，`output_config.effort` 来自 profile，temperature 不发送，`max_tokens` 仅显式配置时发送。
- `tool_choice` 在 thinking 恒开下只放行 auto/none；required/any/指定函数 fail-fast 为 non-retryable `bad_request`。
- native prompt cache 只在请求边界注入；block-level `cache_control` 与 `extra_body.cache_control` 均按 `PromptCacheConfig`，不回写 IR/历史。
- stream 与 chat 均能产出 text、reasoning_content、tool_calls/tool_call_deltas、usage、finish_reason，以及 signed thinking `ProviderState(transport="anthropic_messages", payload={"thinking":[...]})`。
- provider_state tag 丢弃在 `anthropic_messages` 与 `chat_completions` 双向生效。
- factory/config 支持 `global.anthropic.claude-opus-4-6-v1`，BYOK 仍固定 `chat_completions`。
- `uv run pytest tests/ -q` 通过；真实端点 smoke 能证明网关是 Anthropic native passthrough 或暴露需要运维处理的 gateway 风险。

---

## Spec Coverage Self-Review

- Spec §2 / §3 native transport + factory dispatch: Tasks 2、3、5、6、7 覆盖。
- Spec §4 config + `PromptCacheConfig`: Tasks 1、8 覆盖。
- Spec §5 message conversion/tool_result/tool_choice: Task 3 覆盖。
- Spec §6 thinking/effort/temperature/max_tokens: Task 3 覆盖。
- Spec §7 signed thinking provider_state/tag discard: Tasks 3、5、6、9 覆盖。
- Spec §8 prompt cache: Task 4 覆盖。
- Spec §9 normalize_response/normalize_stream/chat/usage/finish_reason: Tasks 5、6 覆盖。
- Spec §10 classify_error: Task 7 覆盖。
- Spec §11 non-changes: Tasks 8、9 的 regression suites 覆盖 BYOK、chat_completions、provider_state persistence 不受影响。
- Spec §12 risks: Task 10 覆盖 gateway passthrough 与 real endpoint smoke。
- Spec §13 tests: Tasks 1-9 覆盖所有列出的 test categories。
- Spec §14 out-of-scope: 计划未引入 `responses`、BYOK anthropic、fallback、Gemini native、persist migration、kernel main loop 改动。
