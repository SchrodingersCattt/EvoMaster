# Provider vendor 子类统一实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** chat_completions 协议下 qwen/deepseek 的多轮 reasoning_content 回放（含 qwen `preserve_thinking`），并把已落地的 anthropic `prompt_cache_compat` 枚举重构为 `BedrockAnthropicTransport` 子类，统一为「协议基类 + vendor 子类 + factory 按 (transport, vendor) 查表」架构。

**Architecture:** Transport 基类保持协议骨架 + seam 设计。chat_completions 把 assistant 序列化从模块函数提升为实例 seam `_assistant_to_wire`，回放经单继承中间基类 `_ReasoningReplayChatCompletions` 复用（不用 mixin）；vendor 请求字段经 `_vendor_request_fields()` seam 注入 extra_body（vendor 缺省在前、显式配置在后可覆盖）。anthropic 顶层 cache_control 差异由 `_emit_top_level_auto_cache()` seam 表达，bedrock 子类 override 为 False。`ProviderConfig.vendor` 是协议内请求方言判别（非厂商名），factory 每协议一张 vendor→class 表，未知 vendor 装配期 fail-fast。

**Tech Stack:** Python 3.13、pydantic v2、openai/anthropic SDK、pytest（`.venv/bin/pytest`，asyncio_mode=auto）、pre-commit（black/isort/flake8/autoflake）。

**Spec:** `docs/superpowers/specs/2026-06-10-provider-vendor-subclass-unification-design.md`（含二轮评审修订）

**约束（来自 spec 与用户规范）：**
- 不动 kernel 主循环、IR 字段、持久化 schema、`PromptCacheConfig`。
- 不在主代码写任何 vendor 自动检测/兜底；BYOK 路径不动（恒 vendor=None）。
- Task 5 是兼容位移除任务：只迁移既有测试、不新增测试，主代码净量不增。
- 所有命令在仓库根目录 `/Users/kealdoom/Developer/dp/matmaster/matmaster-evo` 执行。
- 当前分支 `codex/provider-stage1`，每个 Task 一个 commit，提交点必须全绿。

---

## 文件全景

| 文件 | 动作 |
|---|---|
| `matmaster/providers/transports/chat_completions.py` | T1 序列化提升实例 seam；T2 normalize 补 reasoning；T3 加中间基类 + Qwen/DeepSeek 子类 + `_vendor_request_fields` |
| `matmaster/providers/transports/anthropic_messages.py` | T5 删枚举、加 `_emit_top_level_auto_cache` seam + `BedrockAnthropicTransport` |
| `matmaster/config/llm.py` | T4 加 `vendor` 字段；T5 删 `prompt_cache_compat` 字段 |
| `matmaster/providers/llm_factory.py` | T4 vendor 表 + `_vendor_class` 分发；T5 bedrock 表项 + 删枚举穿线 |
| `config/llm_config.yaml` | T5 litellm-anthropic 换 vendor；T6 加 litellm-qwen/litellm-deepseek 并重指 profile |
| `tests/matmaster/providers/test_chat_completions_transport_normalize.py` | T2 新增 1 测试 |
| `tests/matmaster/providers/test_chat_completions_vendor_transports.py` | T3 新建 |
| `tests/matmaster/providers/test_llm_factory.py` | T4 新增 TestVendorDispatch；T5 迁移 bedrock 断言 |
| `tests/matmaster/providers/test_anthropic_messages_prompt_cache.py` | T5 迁移 `_provider` 与 bedrock 测试 |
| `tests/matmaster/config/test_loader.py` | T5 迁移 1 行断言；T6 迁移 qwen 指向断言 + 新增真实配置接线测试 |

---

### Task 1: chat_completions assistant 序列化提升为实例 seam（纯重构）

**Files:**
- Modify: `matmaster/providers/transports/chat_completions.py`（删 :44-73 两个模块函数，类内加实例方法，`convert_messages` 改调用）

- [ ] **Step 1: 删除模块函数 `_assistant_message_to_dict` 与 `_message_to_openai_dict`**

删除 `chat_completions.py` 中这两个完整函数（当前 :44-73，`_user_message_to_dict` 保留不动）：

```python
def _assistant_message_to_dict(message: AssistantMessage) -> dict[str, Any]:
    ...


def _message_to_openai_dict(message: Message) -> dict[str, Any]:
    ...
```

- [ ] **Step 2: 在 `ChatCompletionsTransport` 类内新增实例方法并改写 `convert_messages`**

把现有的 `convert_messages`（当前 :383-386）整体替换为以下三个方法（放在 `_close_client` 之后、`build_kwargs` 之前）：

```python
    def _assistant_to_wire(self, message: AssistantMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": message.role.value,
            "content": message.content,
        }
        if message.tool_calls is not None:
            payload["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments_json},
                }
                for tc in message.tool_calls
            ]
        return payload

    def _message_to_wire(self, message: Message) -> dict[str, Any]:
        if isinstance(message, AssistantMessage):
            payload = self._assistant_to_wire(message)
        elif isinstance(message, UserMessage):
            payload = _user_message_to_dict(message)
        elif isinstance(message, ToolMessage):
            payload = {
                "role": message.role.value,
                "content": message.content,
                "tool_call_id": message.tool_call_id,
            }
        else:
            payload = {"role": message.role.value, "content": message.content}
        if payload.get("content") is None:
            payload["content"] = ""
        return payload

    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """canonical list[Message] -> OpenAI-compatible wire dicts。"""
        validate_tool_turn_sequence(messages)
        return [self._message_to_wire(message) for message in messages]
```

- [ ] **Step 3: 跑 providers 测试确认重构无行为变化**

Run: `.venv/bin/pytest tests/matmaster/providers -q`
Expected: 全部 PASS（纯重构，现有 convert/build_kwargs/normalize/provider 测试不变）

- [ ] **Step 4: Commit**

```bash
git add matmaster/providers/transports/chat_completions.py
git commit -m "refactor(providers): lift chat_completions assistant serialization to instance seam

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: chat_completions 非流式提取 reasoning_content（TDD）

**Files:**
- Test: `tests/matmaster/providers/test_chat_completions_transport_normalize.py`
- Modify: `matmaster/providers/transports/chat_completions.py`（`normalize_response` 一行）

- [ ] **Step 1: 写失败测试**

在 `test_chat_completions_transport_normalize.py` 的 `test_normalize_response_tool_calls` 之后新增：

```python
def test_normalize_response_extracts_reasoning_content() -> None:
    message = SimpleNamespace(
        content="answer", reasoning_content="thought", tool_calls=None
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    raw = SimpleNamespace(choices=[choice], usage=None)
    out = _t().normalize_response(raw)
    assert out.reasoning_content == "thought"
    assert out.content == "answer"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/matmaster/providers/test_chat_completions_transport_normalize.py -q`
Expected: 新测试 FAIL（`out.reasoning_content` 为 None ≠ "thought"），其余 PASS

- [ ] **Step 3: 实现**

`normalize_response` 末尾的 `LLMResponse(...)` 构造（当前 :544-550）加一行：

```python
        return LLMResponse(
            content=message.content,
            reasoning_content=getattr(message, "reasoning_content", None),
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            usage_vendor=usage_vendor,
        )
```

（SDK message 对象无该属性时 getattr 缺省 None，等于现行为；既有两个 normalize_response 测试的 SimpleNamespace 不带该属性，自动覆盖缺省分支。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/matmaster/providers/test_chat_completions_transport_normalize.py -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/matmaster/providers/test_chat_completions_transport_normalize.py matmaster/providers/transports/chat_completions.py
git commit -m "feat(providers): extract reasoning_content in chat_completions normalize_response

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 回放中间基类 + Qwen/DeepSeek 子类 + `_vendor_request_fields` seam（TDD）

**Files:**
- Create: `tests/matmaster/providers/test_chat_completions_vendor_transports.py`
- Modify: `matmaster/providers/transports/chat_completions.py`

- [ ] **Step 1: 新建测试文件（失败测试）**

创建 `tests/matmaster/providers/test_chat_completions_vendor_transports.py`，完整内容：

```python
"""chat_completions vendor 子类：reasoning_content 回放 + vendor 请求字段。"""

from __future__ import annotations

from matmaster.providers.transports.chat_completions import (
    ChatCompletionsTransport,
    DeepSeekChatCompletionsTransport,
    QwenChatCompletionsTransport,
)
from matmaster.types.messages import AssistantMessage, ToolCallData, UserMessage


def _convert(cls, messages):
    t = cls.__new__(cls)
    return t.convert_messages(messages)


def _replayed_payload(cls):
    return _convert(
        cls,
        [
            AssistantMessage(
                content="ans",
                reasoning_content="thought",
                tool_calls=[ToolCallData(id="c1", name="f", arguments={})],
            )
        ],
    )[0]


def _t(cls, **kw):
    base = dict(model="m", api_key="sk", timeout=10)
    base.update(kw)
    return cls(**base)


def test_base_does_not_replay_reasoning_content() -> None:
    payload = _replayed_payload(ChatCompletionsTransport)
    assert "reasoning_content" not in payload


def test_deepseek_replays_reasoning_content() -> None:
    payload = _replayed_payload(DeepSeekChatCompletionsTransport)
    assert payload["reasoning_content"] == "thought"
    assert payload["content"] == "ans"
    assert payload["tool_calls"][0]["id"] == "c1"


def test_qwen_replays_reasoning_content() -> None:
    payload = _replayed_payload(QwenChatCompletionsTransport)
    assert payload["reasoning_content"] == "thought"


def test_replay_skips_when_reasoning_is_none() -> None:
    payload = _convert(
        DeepSeekChatCompletionsTransport, [AssistantMessage(content="ans")]
    )[0]
    assert "reasoning_content" not in payload


def test_qwen_build_kwargs_sends_preserve_thinking() -> None:
    kw = _t(QwenChatCompletionsTransport).build_kwargs(
        [UserMessage(content="hi")], None
    )
    assert kw["extra_body"] == {"preserve_thinking": True}


def test_base_and_deepseek_send_no_vendor_fields() -> None:
    for cls in (ChatCompletionsTransport, DeepSeekChatCompletionsTransport):
        kw = _t(cls).build_kwargs([UserMessage(content="hi")], None)
        assert "extra_body" not in kw


def test_explicit_extra_body_overrides_vendor_fields() -> None:
    kw = _t(
        QwenChatCompletionsTransport, extra_body={"preserve_thinking": False}
    ).build_kwargs([UserMessage(content="hi")], None)
    assert kw["extra_body"] == {"preserve_thinking": False}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/matmaster/providers/test_chat_completions_vendor_transports.py -q`
Expected: 收集即 FAIL（ImportError：`DeepSeekChatCompletionsTransport` 不存在）

- [ ] **Step 3: 实现 seam 与子类**

3a. `ChatCompletionsTransport` 类内、`_message_to_wire` 之后加 seam：

```python
    def _vendor_request_fields(self) -> dict[str, Any]:
        """vendor 子类的请求体附加字段（经 extra_body 平铺进请求顶层）。"""
        return {}
```

3b. `build_kwargs` 的 extra_body 组装段（当前 :406-418）改为——在 `self._extra_body` 合并**之前**注入 vendor 字段（vendor 缺省在前、显式配置在后可覆盖，与既有 `test_byok_extra_body_passthrough_user_wins` 的「user wins」惯例一致）：

```python
        effort = (self._reasoning_effort or "").strip().lower()
        extra_body: dict[str, Any] = {}
        if effort:
            kwargs["reasoning_effort"] = effort
        if self._reasoning_summary:
            reasoning: dict[str, str] = {"summary": self._reasoning_summary}
            if effort:
                reasoning["effort"] = effort
            extra_body["reasoning"] = reasoning
        extra_body.update(self._vendor_request_fields())
        if self._extra_body:
            extra_body.update(self._extra_body)
        if extra_body:
            kwargs["extra_body"] = extra_body
```

3c. 文件末尾（`ChatCompletionsTransport` 类定义结束后）加三个类：

```python
class _ReasoningReplayChatCompletions(ChatCompletionsTransport):
    """中间基类：把前轮 reasoning_content 以同级字段回放进 assistant 消息。"""

    def _assistant_to_wire(self, message: AssistantMessage) -> dict[str, Any]:
        payload = super()._assistant_to_wire(message)
        if message.reasoning_content is not None:
            payload["reasoning_content"] = message.reasoning_content
        return payload


class DeepSeekChatCompletionsTransport(_ReasoningReplayChatCompletions):
    """deepseek-v4 系：tool call 链之间必须回传 reasoning_content（缺失则 400）。"""


class QwenChatCompletionsTransport(_ReasoningReplayChatCompletions):
    """qwen3 系（百炼 OpenAI 兼容端点）：回放 + preserve_thinking 服务端拼接。"""

    def _vendor_request_fields(self) -> dict[str, Any]:
        return {"preserve_thinking": True}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/matmaster/providers -q`
Expected: 全部 PASS（含新文件 7 个测试）

- [ ] **Step 5: Commit**

```bash
git add tests/matmaster/providers/test_chat_completions_vendor_transports.py matmaster/providers/transports/chat_completions.py
git commit -m "feat(providers): add qwen/deepseek reasoning replay vendor transports

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `ProviderConfig.vendor` 字段 + factory vendor 分发（TDD）

**Files:**
- Test: `tests/matmaster/providers/test_llm_factory.py`
- Modify: `matmaster/config/llm.py`、`matmaster/providers/llm_factory.py`

本 Task 保留 `prompt_cache_compat` 字段与穿线不动（Task 5 移除）；anthropic vendor 表暂只有 None 项。

- [ ] **Step 1: 写失败测试**

`tests/matmaster/providers/test_llm_factory.py` 顶部 import 块改为（新增两个子类 import）：

```python
from matmaster.providers.transports.chat_completions import (
    ChatCompletionsTransport,
    DeepSeekChatCompletionsTransport,
    QwenChatCompletionsTransport,
)
```

文件末尾新增测试类：

```python
class TestVendorDispatch:
    def _cfg(self, provider: ProviderConfig) -> LLMConfig:
        return LLMConfig(
            providers={"p": provider},
            profiles={
                "m": LLMProfileConfig(provider="p", model="m", context_limit=1)
            },
            default="m",
        )

    def test_qwen_vendor_builds_qwen_transport(self) -> None:
        provider = build_provider(
            self._cfg(
                ProviderConfig(
                    transport="chat_completions", api_key="k", vendor="qwen"
                )
            )
        )
        assert isinstance(provider, QwenChatCompletionsTransport)

    def test_deepseek_vendor_builds_deepseek_transport(self) -> None:
        provider = build_provider(
            self._cfg(
                ProviderConfig(
                    transport="chat_completions", api_key="k", vendor="deepseek"
                )
            )
        )
        assert isinstance(provider, DeepSeekChatCompletionsTransport)

    def test_no_vendor_builds_protocol_base(self) -> None:
        provider = build_provider(
            self._cfg(ProviderConfig(transport="chat_completions", api_key="k"))
        )
        assert type(provider) is ChatCompletionsTransport

    def test_unknown_vendor_fail_fast(self) -> None:
        with pytest.raises(ValueError, match="unsupported vendor"):
            build_provider(
                self._cfg(
                    ProviderConfig(
                        transport="chat_completions", api_key="k", vendor="ghost"
                    )
                )
            )

    def test_vendor_transport_mismatch_fail_fast(self) -> None:
        with pytest.raises(ValueError, match="unsupported vendor"):
            build_provider(
                self._cfg(
                    ProviderConfig(
                        transport="chat_completions", api_key="k", vendor="bedrock"
                    )
                )
            )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/matmaster/providers/test_llm_factory.py -q`
Expected: TestVendorDispatch 5 个中 4 个 FAIL——注意 pydantic v2 默认忽略未知字段，vendor 入参不会报 ValidationError，失败形态是 isinstance 断言不成立、`pytest.raises` 报 DID NOT RAISE；`test_no_vendor_builds_protocol_base` 从一开始就 PASS（行为钉子）。其余既有测试 PASS

- [ ] **Step 3: `config/llm.py` 加 vendor 字段**

`ProviderConfig` 改为（`prompt_cache_compat` 本 Task 保留）：

```python
class ProviderConfig(BaseModel):
    """一个后端连接：怎么连到 provider。

    vendor 是协议内的请求方言判别（非厂商名）：chat_completions 认 qwen/deepseek，
    anthropic_messages 认 bedrock；None = 协议基本实现。
    """

    transport: str
    api_key: str
    base_url: str | None = None
    vendor: str | None = None
    prompt_cache_compat: Literal["anthropic_native", "bedrock_blocks"] = (
        "anthropic_native"
    )
```

- [ ] **Step 4: `llm_factory.py` 加 vendor 表与分发**

4a. import 调整：

```python
from typing import Literal, TypeVar

from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    AnthropicPromptCacheOptions,
)
from matmaster.providers.transports.chat_completions import (
    ChatCompletionsTransport,
    DeepSeekChatCompletionsTransport,
    QwenChatCompletionsTransport,
)
```

4b. 在 `BYOK_PROFILE_KEY` 常量区之后加表与 helper：

```python
_TransportClassT = TypeVar("_TransportClassT")

_CHAT_COMPLETIONS_BY_VENDOR: dict[str | None, type[ChatCompletionsTransport]] = {
    None: ChatCompletionsTransport,
    "qwen": QwenChatCompletionsTransport,
    "deepseek": DeepSeekChatCompletionsTransport,
}
_ANTHROPIC_BY_VENDOR: dict[str | None, type[AnthropicMessagesTransport]] = {
    None: AnthropicMessagesTransport,
}
_RESPONSES_BY_VENDOR: dict[str | None, type[ResponsesTransport]] = {
    None: ResponsesTransport,
}


def _vendor_class(
    by_vendor: dict[str | None, type[_TransportClassT]],
    provider: ProviderConfig,
) -> type[_TransportClassT]:
    """(transport, vendor) -> transport 类；未知 vendor 装配期 fail-fast。"""
    try:
        return by_vendor[provider.vendor]
    except KeyError as exc:
        raise ValueError(
            f"unsupported vendor {provider.vendor!r} for transport "
            f"{provider.transport!r}, available: {list(by_vendor)}"
        ) from exc
```

4c. 三个 builder 的构造行改为查表（其余 kwargs 不变）：

```python
def _build_chat_completions_transport(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> ChatCompletionsTransport:
    """profile 平铺字段 + provider 连接到 ChatCompletionsTransport（vendor 分发）。"""
    cls = _vendor_class(_CHAT_COMPLETIONS_BY_VENDOR, provider)
    return cls(
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
```

`_build_anthropic_messages_transport`：`return AnthropicMessagesTransport(` 前加 `cls = _vendor_class(_ANTHROPIC_BY_VENDOR, provider)`，构造改 `return cls(`（`prompt_cache_compat=provider.prompt_cache_compat` 本 Task 保留）。
`_build_responses_transport`：同样加 `cls = _vendor_class(_RESPONSES_BY_VENDOR, provider)`，构造改 `return cls(`。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/pytest tests/matmaster/providers tests/matmaster/config tests/matmaster/integration -q`
Expected: 全部 PASS（BYOK 构造 ProviderConfig 不带 vendor → None → 基类，既有测试不受影响）

- [ ] **Step 6: Commit**

```bash
git add matmaster/config/llm.py matmaster/providers/llm_factory.py tests/matmaster/providers/test_llm_factory.py
git commit -m "feat(providers): add ProviderConfig.vendor and factory vendor dispatch

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: anthropic bedrock 子类化，移除 `prompt_cache_compat` 枚举（迁移既有测试，不新增）

**Files:**
- Modify: `matmaster/providers/transports/anthropic_messages.py`、`matmaster/providers/llm_factory.py`、`matmaster/config/llm.py`、`config/llm_config.yaml`
- Test (迁移): `tests/matmaster/providers/test_anthropic_messages_prompt_cache.py`、`tests/matmaster/providers/test_llm_factory.py`、`tests/matmaster/config/test_loader.py`

枚举的三层穿线（config 字段 → factory 实参 → transport 分支）与其测试互相咬合，本 Task 为单个原子 commit；中间步骤允许红，提交点必须全绿。主代码净量：删 ~12 行、增 ~11 行，不增。

- [ ] **Step 1: 迁移 `test_anthropic_messages_prompt_cache.py`**

1a. import 块加 `BedrockAnthropicTransport`：

```python
from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    AnthropicPromptCacheOptions,
    BedrockAnthropicTransport,
)
```

1b. `_provider` helper 整体替换（删 `prompt_cache_compat` pop 与实参，加 `transport_cls` 首位参数）：

```python
def _provider(
    transport_cls: type[AnthropicMessagesTransport] = AnthropicMessagesTransport,
    **overrides,
) -> AnthropicMessagesTransport:
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
    return transport_cls(
        model="claude-opus-4-6",
        api_key="sk-test",
        prompt_cache_options=options,
    )
```

1c. `test_bedrock_cache_compat_converts_automatic_to_block_checkpoint` 整体替换为（改名 + 构造方式，断言不变——bedrock 在 max_breakpoints=2 时块级目标可打满 2 个；与之对照的 native `test_cache_respects_max_breakpoints_after_automatic_slot` 仍断言只打 1 个，两者合并覆盖 spec §6.5 对 `emit_top_level_auto` 槽位语义的要求）：

```python
def test_bedrock_transport_converts_automatic_to_block_checkpoint() -> None:
    provider = _provider(BedrockAnthropicTransport, max_breakpoints=2)

    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            UserMessage(content="current"),
        ],
        tools=None,
    )

    assert "extra_body" not in kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][-1]["content"][0]["cache_control"] == {
        "type": "ephemeral"
    }
    assert "cache_control" not in kwargs["messages"][0]["content"][0]
```

- [ ] **Step 2: 迁移 `test_llm_factory.py` 的 bedrock 接线断言**

2a. import 块加 `BedrockAnthropicTransport`（与 `AnthropicMessagesTransport` 同一 from 块）。

2b. `test_anthropic_messages_builder_receives_profile_and_cache` 中：

```python
                "litellm-anthropic": ProviderConfig(
                    transport="anthropic_messages",
                    api_key="sk-proxy",
                    base_url="https://proxy.example/anthropic",
                    prompt_cache_compat="bedrock_blocks",
                )
```

改为：

```python
                "litellm-anthropic": ProviderConfig(
                    transport="anthropic_messages",
                    api_key="sk-proxy",
                    base_url="https://proxy.example/anthropic",
                    vendor="bedrock",
                )
```

断言区：`assert isinstance(provider, AnthropicMessagesTransport)` 改为 `assert isinstance(provider, BedrockAnthropicTransport)`；**删除** `assert provider._prompt_cache_compat == "bedrock_blocks"` 一行。

- [ ] **Step 3: 迁移 `test_loader.py` 一行**

`test_repo_llm_config_includes_native_anthropic_opus` 中：

```python
        assert resolved.provider.prompt_cache_compat == "bedrock_blocks"
```

改为：

```python
        assert resolved.provider.vendor == "bedrock"
```

- [ ] **Step 4: `anthropic_messages.py` 子类化改造**

4a. 删除类型别名（当前 :41）：`PromptCacheCompat = Literal["anthropic_native", "bedrock_blocks"]`；顶部 import 改 `from typing import Any, Literal` → `from typing import Any`（Literal 仅此处使用）。

4b. `_select_anthropic_cache_targets` 签名与首段改为：

```python
def _select_anthropic_cache_targets(
    *,
    has_system: bool,
    messages: list[dict[str, Any]],
    options: AnthropicPromptCacheOptions,
    emit_top_level_auto: bool = True,
) -> list[_CacheTarget]:
    targets: list[_CacheTarget] = []
    used_slots: set[tuple[int, int | None]] = set()
    used_whole_message_indexes: set[int] = set()
    automatic_uses_top_level = options.automatic and emit_top_level_auto
```

（函数体其余不变。）

4c. `AnthropicMessagesTransport.__init__`：删形参 `prompt_cache_compat: PromptCacheCompat = "anthropic_native",` 与赋值 `self._prompt_cache_compat = prompt_cache_compat`。

4d. 类内 `_close_client` 之后加 seam：

```python
    def _emit_top_level_auto_cache(self) -> bool:
        """automatic 时是否随请求发顶层 cache_control（native 默认发）。"""
        return True
```

4e. `build_kwargs` 的 cache 段改为（`options is not None` 分支内）：

```python
        if options is not None:
            emit_top_level_auto = self._emit_top_level_auto_cache()
            targets = _select_anthropic_cache_targets(
                has_system=bool(system_value),
                messages=converted_messages,
                options=options,
                emit_top_level_auto=emit_top_level_auto,
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
            if options.automatic and emit_top_level_auto:
                kwargs_extra_body = {"cache_control": dict(options.cache_control)}
            else:
                kwargs_extra_body = {}
        else:
            kwargs_extra_body = {}
```

4f. 文件末尾加子类：

```python
class BedrockAnthropicTransport(AnthropicMessagesTransport):
    """Bedrock 后端方言：不接受顶层 cache_control，automatic 全走块级断点。"""

    def _emit_top_level_auto_cache(self) -> bool:
        return False
```

- [ ] **Step 5: `llm_factory.py` 接入 bedrock 表项、删枚举穿线**

5a. import 块加 `BedrockAnthropicTransport`；anthropic 表改为：

```python
_ANTHROPIC_BY_VENDOR: dict[str | None, type[AnthropicMessagesTransport]] = {
    None: AnthropicMessagesTransport,
    "bedrock": BedrockAnthropicTransport,
}
```

5b. `_build_anthropic_messages_transport` 中**删除**一行：`prompt_cache_compat=provider.prompt_cache_compat,`。

- [ ] **Step 6: `config/llm.py` 删字段**

`ProviderConfig` 删除：

```python
    prompt_cache_compat: Literal["anthropic_native", "bedrock_blocks"] = (
        "anthropic_native"
    )
```

（`Literal` import 保留——`PromptCacheConfig.ttl` 等仍在用。）

- [ ] **Step 7: `config/llm_config.yaml` 换字段**

`litellm-anthropic` 条目：`prompt_cache_compat: bedrock_blocks` 一行替换为 `vendor: bedrock`：

```yaml
  litellm-anthropic:
    transport: anthropic_messages
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_BASE}"
    vendor: bedrock
```

- [ ] **Step 8: 全绿确认**

Run: `.venv/bin/pytest tests/matmaster/providers tests/matmaster/config tests/matmaster/integration -q`
Expected: 全部 PASS。另跑残留检查：

Run: `grep -rn 'prompt_cache_compat\|PromptCacheCompat' --include='*.py' --include='*.yaml' matmaster/ config/ tests/`
Expected: 无输出（枚举痕迹清零）

- [ ] **Step 9: Commit**

```bash
git add matmaster/providers/transports/anthropic_messages.py matmaster/providers/llm_factory.py matmaster/config/llm.py config/llm_config.yaml tests/matmaster/providers/test_anthropic_messages_prompt_cache.py tests/matmaster/providers/test_llm_factory.py tests/matmaster/config/test_loader.py
git commit -m "refactor(providers): replace prompt_cache_compat enum with BedrockAnthropicTransport

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: yaml 接线 qwen/deepseek vendor provider（TDD）

**Files:**
- Test: `tests/matmaster/config/test_loader.py`
- Modify: `config/llm_config.yaml`

- [ ] **Step 1: 更新/新增 loader 真实配置测试（失败）**

1a. `test_litellm_responses_provider_and_gpt_profile_migrated` 末行：

```python
        assert cfg.profiles["matmaster/qwen3.7-max"].provider == "litellm"
```

改为：

```python
        assert cfg.profiles["matmaster/qwen3.7-max"].provider == "litellm-qwen"
```

1b. `TestRealLlmConfigResponsesMigration` 类之后新增：

```python
class TestRealLlmConfigVendorWiring:
    def test_vendor_providers_and_profile_pointing(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")

        assert cfg.providers["litellm-qwen"].transport == "chat_completions"
        assert cfg.providers["litellm-qwen"].vendor == "qwen"
        assert cfg.providers["litellm-deepseek"].transport == "chat_completions"
        assert cfg.providers["litellm-deepseek"].vendor == "deepseek"
        assert cfg.providers["litellm"].vendor is None
        assert cfg.providers["litellm-anthropic"].vendor == "bedrock"

        assert cfg.profiles["matmaster/qwen3.7-max"].provider == "litellm-qwen"
        assert cfg.profiles["matmaster/dsk-v4p"].provider == "litellm-deepseek"
        assert (
            cfg.profiles["matmaster/DeepSeek-v4-Pro"].provider == "litellm-deepseek"
        )
        assert cfg.profiles["gemini-3.1-pro-preview"].provider == "litellm"

    def test_default_profile_builds_qwen_vendor_transport(self) -> None:
        from matmaster.providers.llm_factory import build_provider
        from matmaster.providers.transports.chat_completions import (
            QwenChatCompletionsTransport,
        )

        repo_root = Path(__file__).resolve().parents[3]
        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")

        provider = build_provider(cfg)
        assert isinstance(provider, QwenChatCompletionsTransport)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/matmaster/config/test_loader.py -q`
Expected: 上述三处 FAIL（litellm-qwen 不存在），其余 PASS

- [ ] **Step 3: 改 `config/llm_config.yaml`**

3a. providers 段（`litellm` 之后）插入两个条目，并在段首注释补一行 vendor 说明：

```yaml
# providers: 后端连接（按 provider 去重，transport 在此声明）
#   vendor: 协议内请求方言判别（chat_completions: qwen/deepseek；anthropic_messages: bedrock）

providers:
  litellm:
    transport: chat_completions
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"

  litellm-qwen:
    transport: chat_completions
    vendor: qwen
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"

  litellm-deepseek:
    transport: chat_completions
    vendor: deepseek
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_API_BASE}"
```

3b. 三个 profile 重指（其余字段不动）：`matmaster/qwen3.7-max` 的 `provider: litellm` → `provider: litellm-qwen`；`matmaster/dsk-v4p` 与 `matmaster/DeepSeek-v4-Pro` 的 `provider: litellm` → `provider: litellm-deepseek`。`gemini-3.1-pro-preview` 保持 `litellm` 不动。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/matmaster/config -q`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add config/llm_config.yaml tests/matmaster/config/test_loader.py
git commit -m "feat(config): wire qwen/deepseek profiles to vendor providers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 全量验证

**Files:** 无新改动（只验证）

- [ ] **Step 1: 全量测试**

Run: `.venv/bin/pytest tests -q`
Expected: 全部 PASS（无 skip 之外的非绿项）

- [ ] **Step 2: 风格检查**

Run: `.venv/bin/python -m pre_commit run --all-files`（若 venv 无 pre-commit 则 `pre-commit run --all-files`）
Expected: black/isort/flake8/autoflake 全过；若 black/isort 自动改写了文件，复跑 Step 1 后用 `git commit -a -m "style: format provider vendor changes"` 收尾（消息同样附 Co-Authored-By 行）

- [ ] **Step 3: 行为对照自查（对 spec §7 净效果）**

逐条核对（代码阅读级，不跑真实 API）：
- qwen profile（litellm-qwen）→ `QwenChatCompletionsTransport`：assistant 回放带 `reasoning_content`，extra_body 带 `preserve_thinking: true`；
- deepseek 两个 profile → `DeepSeekChatCompletionsTransport`：仅回放；
- gemini / BYOK → 基类：零变化；
- `global.anthropic.claude-opus-4-6-v1` → `BedrockAnthropicTransport`：块级断点可打满 `max_breakpoints`，无顶层 `extra_body.cache_control`；
- `grep -rn 'prompt_cache_compat' matmaster/ config/ tests/` 无输出。

确认后向用户汇报完成，建议走 superpowers:finishing-a-development-branch。
