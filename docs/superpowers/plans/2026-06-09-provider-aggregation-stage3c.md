# 阶段三 c：native OpenAI `responses` transport 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 native OpenAI `responses` transport，把 `matmaster/gpt-5.5` 从 litellm/`chat_completions` **原地迁移**到 native Responses（encrypted reasoning items 回放，stateless），第二次产真实 `provider_state`，并让 chat_completions / anthropic_messages / responses 三 transport 并存使 tag-丢弃再压测。

**Architecture:** kernel 继续只搬运中立 `list[Message]` 与不透明 `ProviderState`；Responses 的 `input` item 列表形状、native reasoning（summary 展示 + encrypted_content 回放）、function_call/function_call_output 转换、stream/usage/finish_reason 归一、SDK 错误分类全部收敛在 `ResponsesTransport`。factory 通过 dispatch 表新增 `responses` builder；配置层**零 schema 改动**（复用 `LLMProfileConfig` 现有 `reasoning_effort`/`reasoning_summary`/`max_tokens`），只在 `config/llm_config.yaml` 新增一个 `litellm-responses` provider 连接并把 gpt-5.5 profile 翻 `provider` 字段。回放是 stateless（`store=false` + `include=["reasoning.encrypted_content"]`，不用 `previous_response_id`）。

**Tech Stack:** Python 3.11+（uv 环境）、Pydantic v2、openai SDK 2.20.0（Responses 面）、httpx、asyncio、pytest（`asyncio_mode=auto`，async 测试无需装饰器）。

---

## 启动前提（硬前置，违反则停止）

### 1. 3a/3b 基线已落地

本计划基线是阶段三 a + 三 b 已落地后的仓库状态（当前分支 `codex/provider-stage1` 已落地并提交）：

- `matmaster/types/messages.py` 已有 `ProviderState`，`AssistantMessage` / `LLMResponse` / `StreamChunk` 已有 `provider_state` 字段。
- `matmaster/providers/transport.py` 已有 `transport_tag` / `_claim_provider_state()` / 生命周期骨架。
- `matmaster/providers/transports/anthropic_messages.py`（native anthropic transport）已落地，是本计划的结构模板。
- `stream_llm_items` 已把流末 `StreamChunk.provider_state` 聚合进最终 `LLMResponse`（"最后非 None 胜"）。
- factory dispatch 表已有 `chat_completions` + `anthropic_messages`。

执行前先跑基线：

```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
uv run pytest \
  tests/matmaster/types/test_provider_state.py \
  tests/matmaster/providers/test_provider_state_claim.py \
  tests/matmaster/core/test_provider_state_aggregation.py \
  tests/matmaster/providers/test_anthropic_messages_chat.py \
  tests/matmaster/providers/test_llm_factory.py \
  -q
```

Expected: PASS。若失败，先修复基线；不要在 3c transport 内绕过。

### 2. openai SDK 版本与 Responses 面（已实测固化，作为实现硬约束）

本计划的 wire 形状已对 **openai 2.20.0** 实测确认，下列事实在 Task 2–6 中直接使用、**不要在 TDD 时再质疑**（如对不上，先确认 SDK 版本）：

```bash
uv run python -c "import openai; print(openai.__version__)"   # 期望 2.20.0
```

- `client.responses.stream(**kwargs)` 可用，**无 `stream` 形参**（传 `stream=True` 会 `TypeError`）；其接受 `input`/`instructions`/`reasoning`/`include`/`store`/`tools`/`tool_choice`/`max_output_tokens`/`temperature`/`timeout`/`model` 等。`async with client.responses.stream(...) as s: final = await s.get_final_response()` 可用。
- `ResponseInputImageParam.detail` 是 `Required[Literal["low","high","auto"]]`（None/缺省非法）；`image_url` 是 `Optional[str]`（**字符串**，非嵌套对象）。
- `FunctionToolParam`：`type`/`name`/`parameters`/`strict` 为 Required，`description` 可选；`strict` 是 `Required[Optional[bool]]`。
- `EasyInputMessageParam`：仅 REQ `role`/`content`（`content` 可为 bare str）；assistant 文本走它，**不用** output message（`ResponseOutputMessageParam` REQ `id`/`status`，其 `output_text` part REQ `annotations`）。
- function_call input item（`ResponseFunctionToolCallParam`）：REQ `arguments`/`call_id`/`name`/`type`，`id`/`status` 可选。
- function_call_output input item（`FunctionCallOutput`）：REQ `call_id`/`output`/`type`，`id`/`status` 可选。
- reasoning input item（`ResponseReasoningItemParam`）：`id`/`summary`/`type` 为主，`encrypted_content`/`content`/`status` 可选。
- 流事件 `.type` 字面值：`response.output_text.delta`(`.delta`,`.item_id`)、`response.reasoning_summary_text.delta`(`.delta`)、`response.reasoning_text.delta`(`.delta`)、`response.function_call_arguments.delta`(`.delta`,`.item_id`)、`response.output_item.added`(`.item`)、`response.output_item.done`(`.item`)、`response.completed`/`response.incomplete`/`response.failed`(`.response`)、`response.refusal.delta`(`.delta`)/`response.refusal.done`。
- 终态 `Response`：`.output`（item 列表）、`.usage`、`.status`、`.incomplete_details`(`.reason`)、`.error`(`.code`,`.message`)。output 中 `message` item 的 `.content` 是 part 列表，`output_text` part 有 `.text`/`.type=="output_text"`；reasoning item 的 `.summary` 是 part 列表，`summary_text` part 有 `.text`。
- usage：`input_tokens`/`output_tokens`/`total_tokens`、`input_tokens_details.cached_tokens`、`output_tokens_details.reasoning_tokens`。
- `finish_diagnostics.py` 消费 OpenAI 风格 `finish_reason`（`"stop"`/`"length"`/`"content_filter"`/`"tool_calls"`），与本 transport 归一目标一致，**无需改 finish_diagnostics**。

### 3. 网关透明性（运维前置，最高风险 §13.1）

本次**仅 litellm `/v1/responses` 网关一条连接、无直连 openai A/B 对照**。合并前必须由运维确认 `LITELLM_PROXY_RESPONSES_BASE` 是 OpenAI SDK `base_url` 根路径（例如 `https://<litellm-host>/v1`；**不要**带尾部 `/responses`，SDK 会自动拼 `/responses`）且 Responses 原始协议透传（不改写/不剥 `encrypted_content`、不强制 `store=true`、尊重 `include`、保留 `call_id`），并通过 **Task 1 的 spike**。

### 4. 记录执行基线（用于最终 diff 核对）

执行 Task 1 前记录本计划的起始 commit，后续 Task 9 用它检查禁止改动文件。不要使用 `HEAD~N` 这类提交数假设，因为执行者可能 squash、拆 commit 或从已有 ahead 分支继续。

```bash
export PROVIDER_STAGE3C_BASE="$(git rev-parse HEAD)"
```

---

## 文件结构与职责

- `matmaster/providers/transports/responses.py`：**新增** native Responses transport。包含 `input` item 转换（instructions 抽取、user input_text/input_image、assistant 重建 reasoning+easy message+function_call、function_call_output）、`build_kwargs`（reasoning/include/store）、`normalize_response`/`normalize_stream`、usage/finish_reason 归一、encrypted reasoning `provider_state` 存取、error classification。结构对照 `anthropic_messages.py`（native 模板）与 `chat_completions.py`（openai SDK / classify_error 形态）。
- `matmaster/providers/llm_factory.py`：新增 `_build_responses_transport()`，注册 `"responses"`。`extra_body is not None` 时 raise（同 anthropic builder）。
- `config/llm_config.yaml`：新增 `litellm-responses` provider 连接；`matmaster/gpt-5.5` profile 翻 `provider`（litellm → litellm-responses，其余字段不变）。
- `.env`（仓库外）：新增 `LITELLM_PROXY_RESPONSES_BASE`（OpenAI SDK `base_url` 根路径，例如 `https://<litellm-host>/v1`；SDK 会在其后拼 `/responses`）。
- **不改**：`matmaster/config/llm.py`（零 schema 演进）、kernel 主循环、IR 字段、持久化 schema、`chat_completions.py`、`anthropic_messages.py`、`transport.py`、`finish_diagnostics.py`。
- 新增测试：`tests/matmaster/providers/test_responses_convert.py`、`test_responses_chat.py`、`test_responses_stream.py`、`test_responses_errors.py`；增改 `tests/matmaster/providers/test_llm_factory.py`、`tests/matmaster/core/test_provider_state_aggregation.py`、`tests/matmaster/config/test_loader.py`、`scripts/spike_responses_roundtrip.py`（spike，非 pytest）。

---

## Task 1: 真实 gateway round-trip spike（方案 A 验证门，最高风险）

> 这是 3c 最不确定处（spec §3 / §7.4 / §13.1）。本 task 用真实 gateway 验证 **方案 A**：`input` 里回放 `[reasoning item, easy assistant message, function_call]` 是否被接受、`encrypted_content` 是否回得到、`call_id` 是否被网关保留。Task 2–9 的 wire 形状在 client 侧已由 openai 2.20.0 TypedDict 实测确认（见启动前提 §2），spike 验证的是**网关/服务端接受度**。spike 通过则维持方案 A；若因顺序/缺元数据被拒，按 §7.4 降级方案 B（payload 改存原始 output item 数组，需回头改 Task 3/4/5——先停下与设计者确认）。
>
> 本 task 是**唯一需要 live gateway 的步骤**。无 gateway 访问时，可先做 Task 2–9（纯函数 TDD，全程 mock SDK 对象、不联网），但 Task 1 **必须在合并前通过**。

**Files:**
- Create: `scripts/spike_responses_roundtrip.py`

- [ ] **Step 1: 运维确认网关透传**

与运维确认 `.env` 中 `LITELLM_PROXY_RESPONSES_BASE` 指向 OpenAI SDK `base_url` 根路径（通常是 `https://<litellm-host>/v1`，不要包含尾部 `/responses`），且该根路径下的 `/responses` 是 Responses 原始协议透传（见启动前提 §3）。未确认前不要继续。

- [ ] **Step 2: 写 spike 脚本**

Create `scripts/spike_responses_roundtrip.py`:

```python
"""3c spike: 验证 litellm Responses 网关的 reasoning 回放（方案 A）。

手动运行（需 live gateway + .env 已加载 LITELLM_PROXY_API_KEY / LITELLM_PROXY_RESPONSES_BASE）：

    uv run python scripts/spike_responses_roundtrip.py

PASS 条件：
  1) 纯文本 round 的 reasoning item 带非空 encrypted_content，并且
     [reasoning, easy assistant message, new user] 回放不报 400。
  2) 工具 round 的 reasoning item 带非空 encrypted_content，并且
     [reasoning, easy assistant message, function_call, function_call_output,
     new user] 回放不报 400（§7.4 顺序约束被满足）。
"""

from __future__ import annotations

import asyncio
import os

import openai


def _dump(item: object) -> dict:
    return item.model_dump(mode="json", exclude_none=True)  # type: ignore[attr-defined]


def _input_text(text: str) -> dict:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _output_text(response: object) -> str:
    texts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "output_text":
                texts.append(getattr(part, "text", "") or "")
    return "".join(texts)


def _reasoning_items(response: object) -> list[dict]:
    return [
        _dump(item)
        for item in getattr(response, "output", None) or []
        if getattr(item, "type", None) == "reasoning"
    ]


def _assert_encrypted(items: list[dict], label: str) -> None:
    assert items, f"FAIL: {label} 未返回 reasoning item"
    assert all(item.get("encrypted_content") for item in items), (
        f"FAIL: {label} reasoning item 缺 encrypted_content"
        "（网关未尊重 include/store=false，§13.3）"
    )


async def main() -> None:
    base_url = os.environ["LITELLM_PROXY_RESPONSES_BASE"]
    api_key = os.environ["LITELLM_PROXY_API_KEY"]
    model = "matmaster/gpt-5.5"
    tools = [
        {
            "type": "function",
            "name": "get_weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            "strict": False,
        }
    ]
    client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    # --- text round 1: 触发 reasoning + easy assistant message ---
    text_prompt = "用一句话说出北京是中国的首都。"
    async with client.responses.stream(
        model=model,
        input=[_input_text(text_prompt)],
        instructions="You are a helpful assistant.",
        reasoning={"effort": "xhigh", "summary": "detailed"},
        include=["reasoning.encrypted_content"],
        store=False,
    ) as stream:
        final_text1 = await stream.get_final_response()

    text_reasoning_items = _reasoning_items(final_text1)
    _assert_encrypted(text_reasoning_items, "text round 1")
    assistant_text = _output_text(final_text1).strip()
    assert assistant_text, "FAIL: text round 1 未返回可回放的 assistant message"

    # --- text round 2: 验证 [reasoning, easy assistant message, new user] ---
    text_replay_input: list[dict] = [
        _input_text(text_prompt),
        *text_reasoning_items,
        {"role": "assistant", "content": assistant_text},
        _input_text("再用一句话继续。"),
    ]
    try:
        async with client.responses.stream(
            model=model,
            input=text_replay_input,
            instructions="You are a helpful assistant.",
            reasoning={"effort": "xhigh", "summary": "detailed"},
            include=["reasoning.encrypted_content"],
            store=False,
        ) as stream:
            final_text2 = await stream.get_final_response()
        print(f"OK text replay: 方案 A 纯文本回放被接受, status={final_text2.status}")
    except openai.BadRequestError as exc:
        print(f"SPIKE FAIL text replay (BadRequest): {exc}")
        print("-> 按 spec §7.4 降级方案 B（payload 存原始 output item 数组），先停下与设计者确认")
        raise

    # --- tool round 1: 强制 function_call，避免模型选择行为污染 spike ---
    tool_prompt = "先简短说明你要调用工具，再调用 get_weather 查北京天气。"
    async with client.responses.stream(
        model=model,
        input=[_input_text(tool_prompt)],
        instructions="You are a helpful assistant.",
        reasoning={"effort": "xhigh", "summary": "detailed"},
        include=["reasoning.encrypted_content"],
        store=False,
        tools=tools,
        tool_choice={"type": "function", "name": "get_weather"},
    ) as stream:
        final_tool1 = await stream.get_final_response()

    tool_reasoning_items = _reasoning_items(final_tool1)
    _assert_encrypted(tool_reasoning_items, "tool round 1")
    function_calls = [
        item
        for item in final_tool1.output
        if getattr(item, "type", None) == "function_call"
    ]
    assert function_calls, "FAIL: tool round 1 未产生 function_call"
    fc = function_calls[0]
    assistant_probe = _output_text(final_tool1).strip() or (
        "我将调用 get_weather 查询北京天气。"
    )
    print(
        "OK tool round1: "
        f"{len(tool_reasoning_items)} reasoning item(s) w/ encrypted_content; "
        f"call_id={fc.call_id}"
    )

    # --- tool round 2: 方案 A 顺序回放，显式覆盖 easy message + function_call ---
    tool_replay_input: list[dict] = [
        _input_text(tool_prompt),
        *tool_reasoning_items,
        {"role": "assistant", "content": assistant_probe},
        {
            "type": "function_call",
            "call_id": fc.call_id,
            "name": fc.name,
            "arguments": fc.arguments,
        },
        {
            "type": "function_call_output",
            "call_id": fc.call_id,
            "output": "晴，25°C",
        },
        _input_text("谢谢"),
    ]
    try:
        async with client.responses.stream(
            model=model,
            input=tool_replay_input,
            instructions="You are a helpful assistant.",
            reasoning={"effort": "xhigh", "summary": "detailed"},
            include=["reasoning.encrypted_content"],
            store=False,
            tools=tools,
            tool_choice="auto",
        ) as stream:
            final_tool2 = await stream.get_final_response()
        print(f"OK tool replay: 方案 A 工具回放被接受, status={final_tool2.status}")
        print("SPIKE PASS -> 维持方案 A")
    except openai.BadRequestError as exc:
        print(f"SPIKE FAIL tool replay (BadRequest): {exc}")
        print("-> 按 spec §7.4 降级方案 B（payload 存原始 output item 数组），先停下与设计者确认")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 运行 spike（live gateway）**

Run:

```bash
uv run python scripts/spike_responses_roundtrip.py
```

Expected: 打印 `OK text replay` / `OK tool round1` / `OK tool replay` / `SPIKE PASS -> 维持方案 A`。

**决策门**：
- PASS → 维持方案 A，继续 Task 2。
- `encrypted_content` 缺失 → 网关未尊重 `include`/`store`（§13.3）；回运维，不要继续。
- text replay 或 tool replay 400（顺序/缺元数据）→ 方案 A 被拒；按 §7.4 切方案 B（需改 Task 3/4/5），先停下与设计者确认。

- [ ] **Step 4: 提交 spike 脚本**

```bash
git add scripts/spike_responses_roundtrip.py
git commit -m "chore(spike): responses gateway reasoning-replay round-trip"
```

---

## Task 2: factory dispatch + transport 骨架 + 构造测试

**Files:**
- Create: `matmaster/providers/transports/responses.py`
- Modify: `matmaster/providers/llm_factory.py`
- Create: `tests/matmaster/providers/test_responses_chat.py`
- Modify: `tests/matmaster/providers/test_llm_factory.py`

- [ ] **Step 1: 写失败的构造测试**

Create `tests/matmaster/providers/test_responses_chat.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from matmaster.providers.transports.responses import ResponsesTransport
from matmaster.types.llm_provider import LLMProvider


class TestConstruction:
    def test_protocol_conformance(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")

        assert isinstance(provider, LLMProvider)
        assert provider.transport_tag == "responses"

    async def test_client_uses_base_url_and_disables_sdk_retries(self) -> None:
        provider = ResponsesTransport(
            model="matmaster/gpt-5.5",
            api_key="sk-test",
            base_url="https://proxy.example/v1",
            timeout=1200.0,
            stream_timeout=120.0,
            stream_idle_timeout=60.0,
        )
        with patch(
            "matmaster.providers.transports.responses.openai.AsyncOpenAI"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client

            async with provider:
                pass

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["api_key"] == "sk-test"
            assert kwargs["base_url"] == "https://proxy.example/v1"
            assert kwargs["max_retries"] == 0
            assert kwargs["http_client"].timeout.read == 130.0
```

- [ ] **Step 2: 写失败的 factory 测试**

在 `tests/matmaster/providers/test_llm_factory.py` 的 import 区加入：

```python
from matmaster.providers.transports.responses import ResponsesTransport
```

在 `class TestDispatch` 末尾追加：

```python
    def test_responses_tag_hits_builder(self) -> None:
        assert "responses" in _TRANSPORT_BUILDERS

    def test_responses_builder_receives_profile(self) -> None:
        cfg = LLMConfig(
            providers={
                "litellm-responses": ProviderConfig(
                    transport="responses",
                    api_key="sk-proxy",
                    base_url="https://proxy.example/v1",
                )
            },
            profiles={
                "matmaster/gpt-5.5": LLMProfileConfig(
                    provider="litellm-responses",
                    model="matmaster/gpt-5.5",
                    reasoning_effort="xhigh",
                    reasoning_summary="detailed",
                    context_limit=256_000,
                    supports_vision=True,
                    timeout=1200,
                    stream_timeout=120,
                    stream_idle_timeout=60,
                    max_retries=3,
                    retry_delay=1.0,
                )
            },
            default="matmaster/gpt-5.5",
        )

        provider = build_provider(cfg)

        assert isinstance(provider, ResponsesTransport)
        assert provider._model == "matmaster/gpt-5.5"
        assert provider._api_key == "sk-proxy"
        assert provider._base_url == "https://proxy.example/v1"
        assert provider._reasoning_effort == "xhigh"
        assert provider._reasoning_summary == "detailed"
        assert provider._max_tokens is None

    def test_responses_builder_rejects_extra_body(self) -> None:
        from matmaster.config.llm import LLMProfileConfig, ProviderConfig
        from matmaster.providers.llm_factory import _build_responses_transport

        with pytest.raises(ValueError, match="does not support extra_body"):
            _build_responses_transport(
                LLMProfileConfig(
                    provider="litellm-responses",
                    model="matmaster/gpt-5.5",
                    context_limit=256_000,
                ),
                ProviderConfig(transport="responses", api_key="k"),
                extra_body={"x": 1},
            )
```

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_responses_chat.py::TestConstruction \
  tests/matmaster/providers/test_llm_factory.py::TestDispatch \
  -q
```

Expected: FAIL，`responses.py` 还不存在 / builder 未注册。

- [ ] **Step 4: 新增 transport 骨架**

Create `matmaster/providers/transports/responses.py`:

```python
"""Native OpenAI Responses transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import openai

from matmaster.providers.transport import Transport
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import LLMResponse, Message, StreamChunk


class ResponsesTransport(Transport):
    """Native OpenAI Responses API transport (stateless encrypted reasoning replay)."""

    transport_tag = "responses"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        reasoning_summary: str | None = None,
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
        self._reasoning_summary = reasoning_summary

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
from matmaster.providers.transports.responses import ResponsesTransport
```

在 `_build_anthropic_messages_transport` 后加入：

```python
def _build_responses_transport(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> ResponsesTransport:
    """profile 平铺字段 + provider 连接到 Responses transport。"""
    if extra_body is not None:
        raise ValueError("responses transport does not support extra_body")
    return ResponsesTransport(
        model=profile.model,
        api_key=provider.api_key,
        base_url=provider.base_url,
        max_tokens=profile.max_tokens,
        reasoning_effort=profile.reasoning_effort,
        reasoning_summary=profile.reasoning_summary,
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
    "responses": _build_responses_transport,
}
```

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_responses_chat.py::TestConstruction \
  tests/matmaster/providers/test_llm_factory.py \
  -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add \
  matmaster/providers/transports/responses.py \
  matmaster/providers/llm_factory.py \
  tests/matmaster/providers/test_responses_chat.py \
  tests/matmaster/providers/test_llm_factory.py
git commit -m "feat(providers): register responses transport skeleton"
```

---

## Task 3: `convert_messages` 与 `build_kwargs`（方案 A）

**Files:**
- Modify: `matmaster/providers/transports/responses.py`
- Create: `tests/matmaster/providers/test_responses_convert.py`

- [ ] **Step 1: 写转换测试**

Create `tests/matmaster/providers/test_responses_convert.py`:

```python
from __future__ import annotations

import pytest

from matmaster.providers.transports.responses import ResponsesTransport
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


def _provider(**kwargs) -> ResponsesTransport:
    return ResponsesTransport(
        model="matmaster/gpt-5.5",
        api_key="sk-test",
        reasoning_effort="xhigh",
        reasoning_summary="detailed",
        **kwargs,
    )


def test_build_kwargs_extracts_instructions_and_sets_stateless_flags() -> None:
    kwargs = _provider().build_kwargs(
        [SystemMessage(content="sys"), UserMessage(content="hi")],
        tools=None,
    )

    assert kwargs["model"] == "matmaster/gpt-5.5"
    assert kwargs["instructions"] == "sys"
    assert kwargs["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
    assert kwargs["reasoning"] == {"effort": "xhigh", "summary": "detailed"}
    assert kwargs["include"] == ["reasoning.encrypted_content"]
    assert kwargs["store"] is False
    assert "temperature" not in kwargs
    assert "max_output_tokens" not in kwargs
    assert "stream" not in kwargs


def test_multiple_system_messages_join_with_blank_lines() -> None:
    kwargs = _provider().build_kwargs(
        [SystemMessage(content="a"), SystemMessage(content="b"), UserMessage(content="hi")],
        tools=None,
    )

    assert kwargs["instructions"] == "a\n\nb"


def test_max_output_tokens_only_when_set() -> None:
    kwargs = _provider(max_tokens=4096).build_kwargs(
        [UserMessage(content="hi")], tools=None
    )

    assert kwargs["max_output_tokens"] == 4096


def test_user_text_and_image_convert_to_input_parts_with_detail() -> None:
    msg = UserMessage(
        content="look",
        images=[
            ImageContentPart(url="https://example.com/a.png", detail="high"),
            ImageContentPart(url="data:image/png;base64,AAAA"),  # detail None -> auto
        ],
    )

    assert _provider().convert_messages([msg]) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {"type": "input_image", "image_url": "https://example.com/a.png", "detail": "high"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "auto"},
            ],
        }
    ]


def test_assistant_replays_reasoning_then_easy_message_then_function_call() -> None:
    state = ProviderState(
        transport="responses",
        payload={
            "reasoning": [
                {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"},
            ]
        },
    )
    msg = AssistantMessage(
        content="thinking done",
        provider_state=state,
        tool_calls=[ToolCallData(id="call_1", name="search", arguments={"q": "x"})],
    )

    assert _provider().convert_messages(
        [msg, ToolMessage(content="result", tool_call_id="call_1", tool_name="search")]
    ) == [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"},
        {"role": "assistant", "content": "thinking done"},
        {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": '{"q": "x"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": "result"},
    ]


def test_empty_assistant_turn_drops_reasoning_to_avoid_orphan() -> None:
    state = ProviderState(
        transport="responses",
        payload={"reasoning": [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"}]},
    )
    msg = AssistantMessage(content="", provider_state=state)

    assert _provider().convert_messages([msg]) == []


def test_mismatched_provider_state_is_discarded_but_content_and_tools_remain() -> None:
    msg = AssistantMessage(
        content="visible",
        provider_state=ProviderState(
            transport="anthropic_messages",
            payload={"thinking": [{"type": "thinking", "thinking": "bad", "signature": "x"}]},
        ),
        tool_calls=[ToolCallData(id="call_1", name="search", arguments={})],
    )

    assert _provider().convert_messages(
        [msg, ToolMessage(content="r", tool_call_id="call_1", tool_name="search")]
    ) == [
        {"role": "assistant", "content": "visible"},
        {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "r"},
    ]


def test_parallel_tool_calls_and_outputs_map_in_order() -> None:
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallData(id="call_a", name="a", arguments={}),
                ToolCallData(id="call_b", name="b", arguments={}),
            ],
        ),
        ToolMessage(content="A", tool_call_id="call_a", tool_name="a"),
        ToolMessage(content="B", tool_call_id="call_b", tool_name="b"),
        UserMessage(content="next"),
    ]

    assert _provider().convert_messages(messages) == [
        {"type": "function_call", "call_id": "call_a", "name": "a", "arguments": "{}"},
        {"type": "function_call", "call_id": "call_b", "name": "b", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_a", "output": "A"},
        {"type": "function_call_output", "call_id": "call_b", "output": "B"},
        {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
    ]


def test_tools_convert_to_flat_function_with_strict_false() -> None:
    kwargs = _provider().build_kwargs(
        [UserMessage(content="hi")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "do search",
                    "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                },
            },
            {"type": "function", "function": {"name": "noargs"}},
        ],
    )

    assert kwargs["tools"] == [
        {
            "type": "function",
            "name": "search",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            "strict": False,
            "description": "do search",
        },
        {
            "type": "function",
            "name": "noargs",
            "parameters": {"type": "object", "properties": {}},
            "strict": False,
        },
    ]
    assert kwargs["tool_choice"] == "auto"


@pytest.mark.parametrize(
    "tool_choice,expected",
    [
        (None, "auto"),
        ("auto", "auto"),
        ("required", "required"),
        ("any", "required"),
        ({"type": "function", "function": {"name": "search"}}, {"type": "function", "name": "search"}),
    ],
)
def test_tool_choice_maps_without_fail_fast(tool_choice, expected) -> None:
    kwargs = _provider().build_kwargs(
        [UserMessage(content="hi")],
        tools=[{"type": "function", "function": {"name": "search"}}],
        tool_choice=tool_choice,
    )

    assert kwargs["tool_choice"] == expected


def test_tool_choice_none_without_tools_omits_tools_and_tool_choice() -> None:
    kwargs = _provider().build_kwargs(
        [UserMessage(content="hi")], tools=None, tool_choice="none"
    )

    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


def test_tool_choice_none_with_tools_sends_none_alongside_tools() -> None:
    kwargs = _provider().build_kwargs(
        [UserMessage(content="hi")],
        tools=[{"type": "function", "function": {"name": "search"}}],
        tool_choice="none",
    )

    assert kwargs["tool_choice"] == "none"
    assert kwargs["tools"][0]["name"] == "search"


def test_orphan_tool_result_fails_fast() -> None:
    with pytest.raises(LLMError) as exc_info:
        _provider().convert_messages(
            [ToolMessage(content="x", tool_call_id="call_1", tool_name="s")]
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.error_category == "bad_request"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/providers/test_responses_convert.py -q
```

Expected: FAIL，convert 返回空列表 / build_kwargs 未实现。

- [ ] **Step 3: 加入 import 与 convert helper**

把 `responses.py` 顶部 import 块替换为：

```python
"""Native OpenAI Responses transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import openai

from matmaster.providers.transport import Transport
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
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

> `ProviderState` / `parse_tool_arguments` 留到 Task 4 再 import（Task 3 用不到，避免 ruff F401）。

在 `class ResponsesTransport` 前加入 convert helper：

```python
def _input_image_part(image: ImageContentPart) -> dict[str, Any]:
    return {
        "type": "input_image",
        "image_url": image.url,
        "detail": image.detail or "auto",
    }


def _user_input_item(message: UserMessage) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if message.content:
        content.append({"type": "input_text", "text": message.content})
    content.extend(_input_image_part(image) for image in message.images)
    return {"role": "user", "content": content}


def _function_call_items(tool_calls: list[ToolCallData] | None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function_call",
            "call_id": tc.id,
            "name": tc.name,
            "arguments": tc.arguments_json,
        }
        for tc in (tool_calls or [])
    ]


def _function_call_output_item(message: ToolMessage) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": message.tool_call_id,
        "output": message.content or "",
    }


def _reasoning_items_from_payload(
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not payload:
        return []
    raw = payload.get("reasoning")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", {})
        item: dict[str, Any] = {
            "type": "function",
            "name": function["name"],
            "parameters": function.get("parameters")
            or {"type": "object", "properties": {}},
            "strict": False,
        }
        if function.get("description"):
            item["description"] = function["description"]
        converted.append(item)
    return converted


def _map_tool_choice(tool_choice: str | dict | None) -> str | dict:
    if tool_choice is None or tool_choice == "auto":
        return "auto"
    if tool_choice == "none":
        return "none"
    if tool_choice in ("required", "any"):
        return "required"
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        name = tool_choice.get("function", {}).get("name") or tool_choice.get("name")
        return {"type": "function", "name": name}
    return "auto"
```

- [ ] **Step 4: 实现 assistant 重建与 `convert_messages`**

在 `ResponsesTransport` 内加入 assistant 重建 helper：

```python
    def _assistant_to_items(self, message: AssistantMessage) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        # 空回合（无 content 且无 tool_calls）丢弃 reasoning，避免孤儿 400（§7.4）
        if message.content or message.tool_calls:
            items.extend(
                _reasoning_items_from_payload(self._claim_provider_state(message))
            )
        if message.content:
            items.append({"role": "assistant", "content": message.content})
        items.extend(_function_call_items(message.tool_calls))
        return items
```

替换 `convert_messages`（per-message 顺序映射，无 scan-ahead，§7.5）：

```python
    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        validate_tool_turn_sequence(messages)
        out: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, SystemMessage):
                continue
            if isinstance(message, UserMessage):
                out.append(_user_input_item(message))
                continue
            if isinstance(message, AssistantMessage):
                out.extend(self._assistant_to_items(message))
                continue
            if isinstance(message, ToolMessage):
                out.append(_function_call_output_item(message))
                continue
        return out
```

- [ ] **Step 5: 实现 `build_kwargs`**

替换 `build_kwargs`（注意：`stream` 形参对本 transport 是 no-op，不写 `kwargs["stream"]`，§8）：

```python
    def build_kwargs(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        tool_choice: str | dict | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        # stream 形参对 Responses 是 no-op：chat()/chat_stream() 都走
        # client.responses.stream()，该方法无 stream 形参（§8）。
        system_messages = [m for m in messages if isinstance(m, SystemMessage)]
        instructions = "\n\n".join(m.content or "" for m in system_messages).strip()
        kwargs: dict[str, Any] = {
            "model": self._model,
            "input": self.convert_messages(messages),
            "include": ["reasoning.encrypted_content"],
            "store": False,
        }
        if instructions:
            kwargs["instructions"] = instructions
        reasoning: dict[str, str] = {}
        if self._reasoning_effort:
            reasoning["effort"] = self._reasoning_effort
        if self._reasoning_summary:
            reasoning["summary"] = self._reasoning_summary
        if reasoning:
            kwargs["reasoning"] = reasoning
        if self._max_tokens is not None:
            kwargs["max_output_tokens"] = self._max_tokens
        converted_tools = _convert_tools(tools)
        if converted_tools:
            kwargs["tools"] = converted_tools
            kwargs["tool_choice"] = _map_tool_choice(tool_choice)
        # 无 tools 时省略 tools/tool_choice（含 compaction 的 tool_choice="none"
        # 纯 summary 调用，§7.8）——不像 anthropic 在无 tools 时仍发 none。
        return kwargs
```

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/providers/test_responses_convert.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add \
  matmaster/providers/transports/responses.py \
  tests/matmaster/providers/test_responses_convert.py
git commit -m "feat(providers): convert messages and build kwargs for responses"
```

---

## Task 4: 共享 output 抽取 helper + `normalize_response()` + `chat()`

**Files:**
- Modify: `matmaster/providers/transports/responses.py`
- Modify: `tests/matmaster/providers/test_responses_chat.py`

- [ ] **Step 1: 写非流式响应测试**

在 `tests/matmaster/providers/test_responses_chat.py` 追加（顶部补 import）：

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

from matmaster.types.messages import ProviderState, ToolCallData, UserMessage


def _part(**kwargs):
    return SimpleNamespace(**kwargs)


class _FakeReasoning:
    """模拟 SDK ResponseReasoningItem：同时支持 `.summary[].text`（取
    reasoning_content）与 `.model_dump(mode="json")`（取 provider_state）。
    **必须带 model_dump**——`_dump_model` 的属性抓取兜底不会递归转换嵌套
    SimpleNamespace summary part，真实 SDK 是 Pydantic model 走 model_dump
    递归降解为 dict，这里如实模拟。不要简化成裸 SimpleNamespace。"""

    type = "reasoning"

    def __init__(self, item_id: str, summary_texts: list[str], encrypted_content: str) -> None:
        self.id = item_id
        self.summary = [SimpleNamespace(type="summary_text", text=t) for t in summary_texts]
        self.encrypted_content = encrypted_content

    def model_dump(self, mode=None, exclude_none=False):
        return {
            "type": "reasoning",
            "id": self.id,
            "summary": [{"type": "summary_text", "text": p.text} for p in self.summary],
            "encrypted_content": self.encrypted_content,
        }


class TestNormalizeResponse:
    def test_extracts_content_reasoning_tools_state_usage_finish(self) -> None:
        raw = SimpleNamespace(
            output=[
                _FakeReasoning("rs_1", ["planning"], "enc"),
                SimpleNamespace(
                    type="message",
                    content=[_part(type="output_text", text="hello")],
                ),
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="search",
                    arguments='{"q": "x"}',
                ),
            ],
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                input_tokens_details=SimpleNamespace(cached_tokens=2),
                output_tokens_details=SimpleNamespace(reasoning_tokens=4),
            ),
        )

        result = ResponsesTransport(
            model="matmaster/gpt-5.5", api_key="sk-test"
        ).normalize_response(raw)

        assert result.content == "hello"
        assert result.reasoning_content == "planning"
        assert result.tool_calls == [
            ToolCallData(id="call_1", name="search", arguments={"q": "x"})
        ]
        assert result.finish_reason == "tool_calls"
        assert result.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 2,
            "reasoning_tokens": 4,
        }
        assert result.provider_state == ProviderState(
            transport="responses",
            payload={
                "reasoning": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [{"type": "summary_text", "text": "planning"}],
                        "encrypted_content": "enc",
                    }
                ]
            },
        )
        assert result.usage_vendor is not None

    def test_completed_without_function_call_is_stop(self) -> None:
        raw = SimpleNamespace(
            output=[SimpleNamespace(type="message", content=[_part(type="output_text", text="hi")])],
            status="completed",
            incomplete_details=None,
            usage=None,
        )

        result = ResponsesTransport(
            model="matmaster/gpt-5.5", api_key="sk-test"
        ).normalize_response(raw)

        assert result.finish_reason == "stop"
        assert result.provider_state is None

    def test_incomplete_max_output_tokens_is_length(self) -> None:
        raw = SimpleNamespace(
            output=[],
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            usage=None,
        )

        result = ResponsesTransport(
            model="matmaster/gpt-5.5", api_key="sk-test"
        ).normalize_response(raw)

        assert result.finish_reason == "length"


class TestChat:
    async def test_chat_uses_stream_get_final_response(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        final = SimpleNamespace(
            output=[], status="completed", incomplete_details=None, usage=None
        )
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        stream_cm.get_final_response = AsyncMock(return_value=final)
        mock_client = MagicMock()
        mock_client.responses.stream.return_value = stream_cm
        provider._client = mock_client

        result = await provider.chat([UserMessage(content="hi")], tool_choice="none")

        assert result.finish_reason == "stop"
        # compaction 纯 summary 调用：无 tools 时省略 tool_choice（§7.8）
        assert "tool_choice" not in mock_client.responses.stream.call_args.kwargs
        assert "stream" not in mock_client.responses.stream.call_args.kwargs
        stream_cm.get_final_response.assert_awaited_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/providers/test_responses_chat.py -q
```

Expected: FAIL，`normalize_response` / `chat` 尚未实现。

- [ ] **Step 3: 扩展 import（`ProviderState` / `parse_tool_arguments`）**

把 `responses.py` 的 `from matmaster.types.messages import (...)` 块补成：

```python
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    LLMResponse,
    Message,
    ProviderState,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
    parse_tool_arguments,
)
```

- [ ] **Step 4: 加入共享 helper（output 抽取 / usage / finish_reason）**

在 `responses.py` 的 `class ResponsesTransport` 前加入：

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


def _reasoning_items_from_output(output: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in output or []:
        if getattr(item, "type", None) == "reasoning":
            dumped = _dump_model(item)
            if isinstance(dumped, dict):
                items.append(dumped)
    return items


def _provider_state_from_reasoning(
    items: list[dict[str, Any]],
) -> ProviderState | None:
    if not items:
        return None
    return ProviderState(transport="responses", payload={"reasoning": items})


def _responses_usage_to_scalar_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    total = getattr(usage, "total_tokens", None)
    out = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(total) if isinstance(total, int) else prompt + completion,
    }
    input_details = getattr(usage, "input_tokens_details", None)
    cached = getattr(input_details, "cached_tokens", None) if input_details else None
    if isinstance(cached, int) and cached > 0:
        out["cache_read_tokens"] = cached
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning = getattr(output_details, "reasoning_tokens", None) if output_details else None
    if isinstance(reasoning, int) and reasoning > 0:
        out["reasoning_tokens"] = reasoning
    return out


def _finish_reason_from_response(response: Any) -> str | None:
    output = getattr(response, "output", None) or []
    has_function_call = any(
        getattr(item, "type", None) == "function_call" for item in output
    )
    has_refusal = any(
        getattr(part, "type", None) == "refusal"
        for item in output
        if getattr(item, "type", None) == "message"
        for part in (getattr(item, "content", None) or [])
    )
    if has_function_call:
        return "tool_calls"
    if has_refusal:
        return "content_filter"
    if getattr(response, "status", None) == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) if details is not None else None
        if reason == "max_output_tokens":
            return "length"
        return reason or "stop"
    return "stop"
```

- [ ] **Step 5: 实现 `normalize_response()`**

替换 `normalize_response`：

```python
    def normalize_response(self, raw: Any) -> LLMResponse:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCallData] = []
        for item in getattr(raw, "output", None) or []:
            item_type = getattr(item, "type", None)
            if item_type == "reasoning":
                for part in getattr(item, "summary", None) or []:
                    text = getattr(part, "text", "") or ""
                    if text:
                        reasoning_parts.append(text)
            elif item_type == "message":
                for part in getattr(item, "content", None) or []:
                    if getattr(part, "type", None) == "output_text":
                        text_parts.append(getattr(part, "text", "") or "")
            elif item_type == "function_call":
                tool_calls.append(
                    ToolCallData(
                        id=getattr(item, "call_id"),
                        name=getattr(item, "name"),
                        arguments=parse_tool_arguments(
                            getattr(item, "arguments", "") or ""
                        ),
                    )
                )
        reasoning_items = _reasoning_items_from_output(getattr(raw, "output", None))
        usage = getattr(raw, "usage", None)
        return LLMResponse(
            content="".join(text_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=tool_calls or None,
            finish_reason=_finish_reason_from_response(raw),
            usage=_responses_usage_to_scalar_dict(usage),
            usage_vendor=_dump_model(usage) if usage is not None else None,
            provider_state=_provider_state_from_reasoning(reasoning_items),
        )
```

- [ ] **Step 6: 实现 `chat()`（stream + get_final_response，规避长 timeout guard，§10.2）**

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
        async with client.responses.stream(**kwargs) as stream:
            final = await stream.get_final_response()
        return self.normalize_response(final)
```

- [ ] **Step 7: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/providers/test_responses_chat.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add \
  matmaster/providers/transports/responses.py \
  tests/matmaster/providers/test_responses_chat.py
git commit -m "feat(providers): normalize responses chat and provider_state"
```

---

## Task 5: `normalize_stream()` + `chat_stream()`

**Files:**
- Modify: `matmaster/providers/transports/responses.py`
- Create: `tests/matmaster/providers/test_responses_stream.py`

- [ ] **Step 1: 写 stream 测试**

Create `tests/matmaster/providers/test_responses_stream.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

import pytest

from matmaster.providers.transports.responses import ResponsesTransport
from matmaster.types.errors import LLMError
from matmaster.types.messages import ProviderState, UserMessage


async def _aiter(items):
    for item in items:
        yield item


def _event(event_type: str, **kwargs):
    return SimpleNamespace(type=event_type, **kwargs)


def _part(**kwargs):
    return SimpleNamespace(**kwargs)


class _FakeStream:
    def __init__(self, items):
        self._items = iter(items)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeResponses:
    def __init__(self, items):
        self._items = items
        self.called_kwargs = None

    def stream(self, **kwargs):
        self.called_kwargs = kwargs
        return _FakeStream(self._items)


class TestNormalizeStream:
    async def test_stream_emits_content_reasoning_tool_state_usage_finish(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        # 此 reasoning item 仅被 _dump_model dump（reasoning_content 来自下面的
        # reasoning_summary_text.delta 事件，不读这个 item 的 .text），故 summary
        # 用纯 dict 的 SimpleNamespace 即可（属性抓取兜底对纯 dict 无损）。
        reasoning_item = SimpleNamespace(
            type="reasoning",
            id="rs_1",
            summary=[{"type": "summary_text", "text": "plan"}],
            encrypted_content="enc",
        )
        completed_response = SimpleNamespace(
            output=[reasoning_item, SimpleNamespace(type="function_call", call_id="call_1", name="search", arguments="{}")],
            status="completed",
            incomplete_details=None,
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                input_tokens_details=SimpleNamespace(cached_tokens=2),
                output_tokens_details=SimpleNamespace(reasoning_tokens=4),
            ),
        )
        events = [
            _event("response.reasoning_summary_text.delta", delta="plan"),
            _event("response.output_text.delta", delta="hello"),
            _event(
                "response.output_item.added",
                item=SimpleNamespace(type="function_call", id="fc_1", call_id="call_1", name="search"),
            ),
            _event("response.function_call_arguments.delta", item_id="fc_1", delta='{"q"'),
            _event("response.function_call_arguments.delta", item_id="fc_1", delta=':"x"}'),
            _event("response.completed", response=completed_response),
        ]

        chunks = [c async for c in provider.normalize_stream(_aiter(events))]

        assert chunks[0].reasoning_content == "plan"
        assert chunks[1].content == "hello"
        assert chunks[2].tool_call_deltas == [{"index": 0, "id": "call_1", "name": "search"}]
        assert chunks[3].tool_call_deltas == [{"index": 0, "arguments": '{"q"'}]
        assert chunks[4].tool_call_deltas == [{"index": 0, "arguments": ':"x"}'}]
        # 流末顺序：provider_state -> finish_reason -> usage
        assert chunks[5].provider_state == ProviderState(
            transport="responses",
            payload={
                "reasoning": [
                    {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "plan"}], "encrypted_content": "enc"}
                ]
            },
        )
        assert chunks[6].finish_reason == "tool_calls"
        assert chunks[7].usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cache_read_tokens": 2,
            "reasoning_tokens": 4,
        }
        assert chunks[7].usage_vendor is not None

    async def test_reasoning_text_delta_maps_to_reasoning_content(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        events = [_event("response.reasoning_text.delta", delta="raw")]

        chunks = [c async for c in provider.normalize_stream(_aiter(events))]

        assert chunks[0].reasoning_content == "raw"

    async def test_refusal_delta_becomes_content_and_completed_sets_content_filter(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        completed = SimpleNamespace(
            output=[SimpleNamespace(type="message", content=[_part(type="refusal", refusal="no")])],
            status="completed",
            incomplete_details=None,
            usage=None,
        )
        events = [
            _event("response.refusal.delta", delta="I refuse"),
            _event("response.completed", response=completed),
        ]

        chunks = [c async for c in provider.normalize_stream(_aiter(events))]

        assert chunks[0].content == "I refuse"
        assert any(c.finish_reason == "content_filter" for c in chunks)

    async def test_failed_event_raises_classified_server_error(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        failed = SimpleNamespace(error=SimpleNamespace(code="server_error", message="boom"))
        events = [_event("response.failed", response=failed)]

        with pytest.raises(LLMError) as exc_info:
            [c async for c in provider.normalize_stream(_aiter(events))]

        assert exc_info.value.error_category == "server"

    async def test_failed_bad_request_reasoning_replay_is_non_retryable(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        failed = SimpleNamespace(
            error=SimpleNamespace(
                code="bad_request",
                message="reasoning item rs_1 without its required following item",
            )
        )
        events = [_event("response.failed", response=failed)]

        with pytest.raises(LLMError) as exc_info:
            [c async for c in provider.normalize_stream(_aiter(events))]

        assert exc_info.value.retryable is False
        assert exc_info.value.error_category == "bad_request"


class TestChatStream:
    async def test_chat_stream_uses_responses_stream_without_stream_kwarg(self) -> None:
        provider = ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")
        completed = SimpleNamespace(output=[], status="completed", incomplete_details=None, usage=None)
        responses = _FakeResponses([_event("response.completed", response=completed)])
        provider._client = SimpleNamespace(responses=responses)

        chunks = [
            c
            async for c in provider.chat_stream([UserMessage(content="hi")], timeout=12.5)
        ]

        assert any(c.finish_reason == "stop" for c in chunks)
        assert "stream" not in responses.called_kwargs
        assert responses.called_kwargs["timeout"] == 12.5
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/providers/test_responses_stream.py -q
```

Expected: FAIL，`normalize_stream` / `chat_stream` 尚未实现。

- [ ] **Step 3: 加入 failed-response 错误 helper**

在 `responses.py` 的 `class ResponsesTransport` 前加入（Task 6 的 `classify_error()` 复用同一个非重试 400 判定）：

```python
def _is_non_retryable_responses_bad_request(text: str) -> bool:
    lowered = text.lower()
    patterns = (
        "reasoning",
        "encrypted",
        "without its required following item",
        "previous_response_id",
        "store",
        "function_call",
    )
    return any(pattern in lowered for pattern in patterns)


def _llm_error_from_failed_response(response: Any) -> LLMError:
    error = getattr(response, "error", None)
    code = str(getattr(error, "code", "") or "").lower()
    message = getattr(error, "message", None) or "responses stream failed"
    text = f"{code} {message}".lower()
    if "context" in text and ("length" in text or "token" in text):
        return LLMError(message, retryable=False, error_category="context_overflow")
    if "rate" in code and "limit" in code:
        return LLMError(message, retryable=True, error_category="rate_limit")
    if code in ("authentication_error", "permission_denied", "auth_error"):
        return LLMError(message, retryable=False, error_category="auth")
    bad_request_like = code in ("bad_request", "invalid_request_error")
    non_retryable_bad_request = _is_non_retryable_responses_bad_request(text)
    if bad_request_like or non_retryable_bad_request:
        return LLMError(
            message,
            retryable=not non_retryable_bad_request,
            error_category="bad_request",
        )
    return LLMError(message, retryable=True, error_category="server")
```

- [ ] **Step 4: 实现 `normalize_stream()`**

替换 `normalize_stream`（流末顺序：provider_state → finish_reason → usage；§10.1）：

```python
    async def normalize_stream(self, raw_iter: Any) -> AsyncIterator[StreamChunk]:
        item_logical_index: dict[str, int] = {}
        next_index = 0
        buffered_reasoning: list[dict[str, Any]] = []

        async for event in raw_iter:
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                yield StreamChunk(content=getattr(event, "delta", "") or "")
                continue
            if event_type in (
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            ):
                yield StreamChunk(reasoning_content=getattr(event, "delta", "") or "")
                continue
            if event_type == "response.refusal.delta":
                yield StreamChunk(content=getattr(event, "delta", "") or "")
                continue
            if event_type == "response.output_item.added":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "function_call":
                    item_id = getattr(item, "id", None)
                    if item_id not in item_logical_index:
                        item_logical_index[item_id] = next_index
                        next_index += 1
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                "index": item_logical_index[item_id],
                                "id": getattr(item, "call_id", None),
                                "name": getattr(item, "name", None),
                            }
                        ]
                    )
                continue
            if event_type == "response.function_call_arguments.delta":
                item_id = getattr(event, "item_id", None)
                if item_id not in item_logical_index:
                    item_logical_index[item_id] = next_index
                    next_index += 1
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": item_logical_index[item_id],
                            "arguments": getattr(event, "delta", "") or "",
                        }
                    ]
                )
                continue
            if event_type == "response.output_item.done":
                # 缓冲 reasoning item，仅作 completed 缺 output 时的 defensive
                # 备选（§10.1）；正常路径用终态 response.output。勿删此分支。
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "reasoning":
                    dumped = _dump_model(item)
                    if isinstance(dumped, dict):
                        buffered_reasoning.append(dumped)
                continue
            if event_type in ("response.completed", "response.incomplete"):
                response = getattr(event, "response", None)
                reasoning_items = (
                    _reasoning_items_from_output(getattr(response, "output", None))
                    or buffered_reasoning
                )
                provider_state = _provider_state_from_reasoning(reasoning_items)
                if provider_state is not None:
                    yield StreamChunk(provider_state=provider_state)
                finish_reason = _finish_reason_from_response(response)
                if finish_reason:
                    yield StreamChunk(finish_reason=finish_reason)
                usage = getattr(response, "usage", None)
                if usage is not None:
                    yield StreamChunk(
                        usage=_responses_usage_to_scalar_dict(usage),
                        usage_vendor=_dump_model(usage),
                    )
                continue
            if event_type == "response.failed":
                response = getattr(event, "response", None)
                raise _llm_error_from_failed_response(response)
```

- [ ] **Step 5: 实现 `chat_stream()`**

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
        kwargs = self.build_kwargs(messages, tools)
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            async with client.responses.stream(**kwargs) as stream:
                async for chunk in self.normalize_stream(stream):
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            err = self.classify_error(exc)
            if err is not None:
                raise err from exc
            raise
```

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/providers/test_responses_stream.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add \
  matmaster/providers/transports/responses.py \
  tests/matmaster/providers/test_responses_stream.py
git commit -m "feat(providers): normalize responses stream and reasoning replay state"
```

---

## Task 6: `classify_error()`

**Files:**
- Modify: `matmaster/providers/transports/responses.py`
- Create: `tests/matmaster/providers/test_responses_errors.py`

- [ ] **Step 1: 写错误分类测试**

Create `tests/matmaster/providers/test_responses_errors.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

import openai

from matmaster.providers.transports.responses import ResponsesTransport
from matmaster.types.errors import LLMError


def _provider() -> ResponsesTransport:
    return ResponsesTransport(model="matmaster/gpt-5.5", api_key="sk-test")


def _bad_request(message: str) -> openai.BadRequestError:
    return openai.BadRequestError(
        message=message,
        response=MagicMock(status_code=400, headers={}),
        body=None,
    )


def test_existing_llm_error_is_not_rewrapped() -> None:
    assert (
        _provider().classify_error(
            LLMError("x", retryable=False, error_category="bad_request")
        )
        is None
    )


def test_timeout_is_retryable() -> None:
    err = _provider().classify_error(openai.APITimeoutError(request=MagicMock()))
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "timeout"


def test_rate_limit_is_retryable() -> None:
    err = _provider().classify_error(
        openai.RateLimitError(
            message="slow down", response=MagicMock(status_code=429, headers={}), body=None
        )
    )
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "rate_limit"


def test_auth_is_non_retryable() -> None:
    err = _provider().classify_error(
        openai.AuthenticationError(
            message="bad key", response=MagicMock(status_code=401, headers={}), body=None
        )
    )
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "auth"


def test_context_overflow_bad_request_is_non_retryable() -> None:
    err = _provider().classify_error(_bad_request("context length exceeds token limit"))
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "context_overflow"


def test_reasoning_replay_bad_request_is_non_retryable() -> None:
    err = _provider().classify_error(
        _bad_request("reasoning item rs_1 without its required following item")
    )
    assert err is not None
    assert err.retryable is False
    assert err.error_category == "bad_request"


def test_generic_bad_request_is_retryable() -> None:
    err = _provider().classify_error(_bad_request("temporary gateway hiccup"))
    assert err is not None
    assert err.retryable is True
    assert err.error_category == "bad_request"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/providers/test_responses_errors.py -q
```

Expected: FAIL，`classify_error` 还是 stub（全返回 None）。

- [ ] **Step 3: 实现 `classify_error()` 并复用非重试 400 判定**

Task 5 已加入 `_is_non_retryable_responses_bad_request()`，这里直接复用它，替换 `classify_error`：

```python
    def classify_error(self, exc: Exception) -> LLMError | None:
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
                return LLMError(
                    err_str, retryable=False, error_category="context_overflow"
                )
            if _is_non_retryable_responses_bad_request(err_text):
                return LLMError(err_str, retryable=False, error_category="bad_request")
            return LLMError(err_str, retryable=True, error_category="bad_request")
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/providers/test_responses_errors.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add \
  matmaster/providers/transports/responses.py \
  tests/matmaster/providers/test_responses_errors.py
git commit -m "feat(providers): classify responses transport errors"
```

---

## Task 7: provider_state round-trip 聚合 + 三 transport tag-丢弃矩阵

**Files:**
- Modify: `tests/matmaster/core/test_provider_state_aggregation.py`
- Modify: `tests/matmaster/providers/test_responses_convert.py`

> 本 task 是纯测试（无源码改动）：验证 responses 真实 provider_state 经 3a 通道聚合 round-trip，并补全三 transport tag-丢弃矩阵（spec §9.4 / §14）。

- [ ] **Step 1: 写聚合 round-trip 测试**

在 `tests/matmaster/core/test_provider_state_aggregation.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_responses_style_provider_state_round_trips_through_aggregation():
    from types import SimpleNamespace

    from matmaster.core.agent_llm_stream import stream_llm_items

    reasoning_payload = {
        "reasoning": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "plan"}],
                "encrypted_content": "enc",
            }
        ]
    }

    class _Provider:
        stream_timeout = 30.0
        stream_idle_timeout = 30.0
        max_retries = 1
        retry_delay = 0.0

        async def chat_stream(self, messages, tools=None, *, timeout=None):
            yield StreamChunk(reasoning_content="plan")
            yield StreamChunk(content="answer")
            yield StreamChunk(
                provider_state=ProviderState(transport="responses", payload=reasoning_payload)
            )
            yield StreamChunk(finish_reason="stop")

    final = None
    async for item in stream_llm_items(SimpleNamespace(llm_provider=_Provider()), [], None):
        if item.llm_response is not None:
            final = item.llm_response

    assert final is not None
    state = final.provider_state
    assert state == ProviderState(transport="responses", payload=reasoning_payload)
    # JSON-compatible：持久化 round-trip（model_dump(mode="json") -> 重建）一致
    dumped = state.model_dump(mode="json")
    assert dumped == {"transport": "responses", "payload": reasoning_payload}
    assert ProviderState.model_validate(dumped) == state
    # encrypted_content 存在（§16 断言）
    assert dumped["payload"]["reasoning"][0]["encrypted_content"] == "enc"
```

- [ ] **Step 2: 写三 transport tag-丢弃矩阵测试**

在 `tests/matmaster/providers/test_responses_convert.py` 顶部 import 区补充：

```python
from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
```

在同文件末尾追加：

```python
def test_responses_discards_chat_completions_tag_keeps_content_and_tools() -> None:
    msg = AssistantMessage(
        content="visible",
        provider_state=ProviderState(transport="chat_completions", payload={"x": 1}),
        tool_calls=[ToolCallData(id="call_1", name="s", arguments={})],
    )

    assert _provider().convert_messages(
        [msg, ToolMessage(content="r", tool_call_id="call_1", tool_name="s")]
    ) == [
        {"role": "assistant", "content": "visible"},
        {"type": "function_call", "call_id": "call_1", "name": "s", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "r"},
    ]


def test_responses_claims_only_its_own_tag() -> None:
    own = AssistantMessage(
        content="hi",
        provider_state=ProviderState(
            transport="responses",
            payload={"reasoning": [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "e"}]},
        ),
    )
    foreign = AssistantMessage(
        content="hi",
        provider_state=ProviderState(transport="anthropic_messages", payload={"thinking": [{"type": "thinking"}]}),
    )

    assert _provider()._claim_provider_state(own) == own.provider_state.payload
    assert _provider()._claim_provider_state(foreign) is None


def test_existing_transports_discard_responses_tag() -> None:
    msg = AssistantMessage(
        content="hi",
        provider_state=ProviderState(
            transport="responses",
            payload={
                "reasoning": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [],
                        "encrypted_content": "e",
                    }
                ]
            },
        ),
    )

    chat_transport = ChatCompletionsTransport(model="m", api_key="sk-test")
    anthropic_transport = AnthropicMessagesTransport(
        model="claude-opus-4-6",
        api_key="sk-test",
    )

    assert chat_transport._claim_provider_state(msg) is None
    assert anthropic_transport._claim_provider_state(msg) is None
```

- [ ] **Step 3: 运行测试确认通过**

Run:

```bash
uv run pytest \
  tests/matmaster/core/test_provider_state_aggregation.py \
  tests/matmaster/providers/test_responses_convert.py \
  -q
```

Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add \
  tests/matmaster/core/test_provider_state_aggregation.py \
  tests/matmaster/providers/test_responses_convert.py
git commit -m "test(providers): responses provider_state round-trip and tag matrix"
```

---

## Task 8: config 迁移（litellm-responses provider + gpt-5.5 翻 provider）+ loader 回归

**Files:**
- Modify: `config/llm_config.yaml`
- Modify: `tests/matmaster/config/test_loader.py`
- `.env`（仓库外，手动）：新增 `LITELLM_PROXY_RESPONSES_BASE`

- [ ] **Step 1: 写 loader 回归测试**

在 `tests/matmaster/config/test_loader.py` 顶部 import 区确认有：

```python
from pathlib import Path

from matmaster.config.loader import load_llm_config
```

在文件末尾追加：

```python
class TestRealLlmConfigResponsesMigration:
    def test_litellm_responses_provider_and_gpt_profile_migrated(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")

        assert cfg.providers["litellm-responses"].transport == "responses"

        gpt = cfg.profiles["matmaster/gpt-5.5"]
        assert gpt.provider == "litellm-responses"
        assert gpt.model == "matmaster/gpt-5.5"
        assert gpt.reasoning_effort == "xhigh"
        assert gpt.reasoning_summary == "detailed"

        resolved = cfg.resolve(model_override="matmaster/gpt-5.5")
        assert resolved.provider.transport == "responses"

        # default 不变；其它 profile 仍指向 litellm
        assert cfg.default == "matmaster/qwen3.7-max"
        assert cfg.profiles["matmaster/qwen3.7-max"].provider == "litellm"

    def test_migrated_config_builds_responses_transport(self) -> None:
        from matmaster.providers.llm_factory import build_provider
        from matmaster.providers.transports.responses import ResponsesTransport

        repo_root = Path(__file__).resolve().parents[3]
        cfg = load_llm_config(repo_root / "config" / "llm_config.yaml")

        provider = build_provider(cfg, model_override="matmaster/gpt-5.5")
        assert isinstance(provider, ResponsesTransport)
        assert provider._model == "matmaster/gpt-5.5"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
uv run pytest tests/matmaster/config/test_loader.py::TestRealLlmConfigResponsesMigration -q
```

Expected: FAIL，`litellm-responses` provider 还没加 / gpt-5.5 还在 litellm。

- [ ] **Step 3: 加 `litellm-responses` provider 连接**

在 `config/llm_config.yaml` 的 `providers:` 段，`anthropic:` provider 后加入：

```yaml
  litellm-responses:                       # 经 litellm responses-passthrough 网关
    transport: responses
    api_key: "${LITELLM_PROXY_API_KEY}"
    base_url: "${LITELLM_PROXY_RESPONSES_BASE}"   # OpenAI SDK base_url 根；SDK 在其后拼 /responses
```

- [ ] **Step 4: 翻 gpt-5.5 profile 的 provider**

在 `config/llm_config.yaml` 中，把 `matmaster/gpt-5.5` profile 的：

```yaml
  matmaster/gpt-5.5:
    provider: litellm
```

改为：

```yaml
  matmaster/gpt-5.5:
    provider: litellm-responses
```

（该 profile 其余字段——`model: matmaster/gpt-5.5`、`reasoning_effort: xhigh`、`reasoning_summary: detailed`、`context_limit`、`supports_vision`、timeout 系列、retries——全部不变。）

- [ ] **Step 5: 在 `.env` 加 `LITELLM_PROXY_RESPONSES_BASE`（仓库外，手动）**

在项目 `.env`（不进仓库）加入运维提供的 Responses 协议根，例如：

```bash
LITELLM_PROXY_RESPONSES_BASE=https://<litellm-host>/v1
```

（`load_llm_config` 在 env 缺失时把 `${...}` 展开为空串，单测仍可解析；运行期需真实值。不要把该变量设成 `https://<litellm-host>/v1/responses` 或 `https://<litellm-host>/responses`，否则 OpenAI SDK 会再拼一次 `/responses`。）

- [ ] **Step 6: 运行测试确认通过**

Run:

```bash
uv run pytest tests/matmaster/config/test_loader.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add config/llm_config.yaml tests/matmaster/config/test_loader.py
git commit -m "feat(config): migrate gpt-5.5 profile to litellm-responses"
```

---

## Task 9: 全量回归 + 完成标准核对

**Files:**
- 无源码改动（仅运行验证）

- [ ] **Step 1: 跑 responses transport 全套**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/test_responses_convert.py \
  tests/matmaster/providers/test_responses_chat.py \
  tests/matmaster/providers/test_responses_stream.py \
  tests/matmaster/providers/test_responses_errors.py \
  -q
```

Expected: PASS（全绿）。

- [ ] **Step 2: 跑 provider / config / core 回归（确认现有路径不受影响）**

Run:

```bash
uv run pytest \
  tests/matmaster/providers/ \
  tests/matmaster/config/ \
  tests/matmaster/core/test_provider_state_aggregation.py \
  tests/matmaster/core/test_provider_state_end_to_end.py \
  tests/matmaster/core/test_natural_finish_provider_state_persist.py \
  -q
```

Expected: PASS。`chat_completions` / `anthropic_messages` 既有测试全部仍绿（行为等价，spec §16）。

- [ ] **Step 3: 对照完成标准（spec §16）逐项核对**

确认：

- `ResponsesTransport` 满足 `LLMProvider`（`isinstance` + `transport_tag=="responses"`）；client 显式 `max_retries=0`。
- wire 形状正确：assistant 文本 EasyInputMessage（bare str content）、`input_image.detail` None→`auto`、function tool `strict:false`、`responses.stream()` 不传 `stream`。
- factory dispatch 加 `responses`；config 加 `litellm-responses` + gpt-5.5 翻 provider；`matmaster/config/llm.py` 未改（`git diff --stat` 确认）。
- reasoning 三类内容分离：content(output_text) / reasoning_content(summary) / provider_state(encrypted reasoning items)。
- 第二次产真实 provider_state，经 3a 通道聚合/持久化 round-trip 一致；`encrypted_content` 存在。
- stateless：`store=false` + `include=["reasoning.encrypted_content"]` 恒发；无 `previous_response_id`。
- 三 transport tag-丢弃实际生效（responses ↔ anthropic_messages ↔ chat_completions）。
- finish_reason 映射使 finish_diagnostics 回归通过；usage scalar+vendor 归一（reasoning_tokens / cached_tokens）。
- 非流式 chat() 经 `stream()`+`get_final_response()`。

Run（确认 `llm.py` 与既有 transport 零改动）：

```bash
BASE_REF="${PROVIDER_STAGE3C_BASE:-origin/codex/provider-stage1}"
git diff --stat "$BASE_REF" -- \
  matmaster/config/llm.py \
  matmaster/providers/transports/chat_completions.py \
  matmaster/providers/transports/anthropic_messages.py \
  matmaster/providers/transport.py
```

Expected: 空输出（这些文件未改）。

- [ ] **Step 4: 确认 Task 1 spike 已通过（合并门）**

确认 Task 1 的 `scripts/spike_responses_roundtrip.py` 已在 live gateway 上 `SPIKE PASS`。未通过则不可合并（§13.1 硬前置）。

- [ ] **Step 5: 最终提交（如有未提交的回归断言）**

```bash
git add -A
git commit -m "test(providers): stage3c responses full regression green" || echo "nothing to commit"
```

---

## Self-Review：spec 覆盖核对

| spec 章节 | 对应 Task |
|---|---|
| §1 基线校正（openai 2.20.0 实测） | 启动前提 §2 + Task 2/3/4/5 wire 形状 |
| §2 接入范围（迁 gpt-5.5） | Task 8 |
| §3 / §7.4 方案 A + spike 验证门 | **Task 1** + Task 3（assistant 重建顺序、空回合丢 reasoning） |
| §4 stateless 回放（store=false + include） | Task 3 build_kwargs（恒发）+ Task 1 spike |
| §5 模块布局 + factory builder（extra_body raise） | Task 2 |
| §6.1/§6.2 config（litellm-responses + 翻 provider） | Task 8 |
| §6.3 `llm.py` 不改 | Task 9 Step 3（git diff 确认） |
| §7.1 instructions 抽取 | Task 3 |
| §7.2 input_text/input_image + detail None→auto | Task 3 |
| §7.3 assistant 重建（reasoning+easy message+function_call） | Task 3 |
| §7.5 function_call_output 顺序映射 | Task 3 |
| §7.6 tag-丢弃 | Task 3 + Task 7 |
| §7.7 tools 扁平 + strict:false | Task 3 |
| §7.8 tool_choice 全映射无 fail-fast | Task 3 |
| §8 build_kwargs（reasoning/include/store/无 stream/无 temperature/max_output_tokens 可选） | Task 3 |
| §9 reasoning 三类内容 + provider_state 结构 | Task 4（response）+ Task 5（stream） |
| §10.1 normalize_stream（事件归一 + 流末顺序） | Task 5 |
| §10.2 chat() stream+get_final_response | Task 4 |
| §10.3 usage 归一 | Task 4（helper）+ Task 4/5 测试 |
| §10.4 finish_reason 映射 | Task 4（helper）+ Task 4/5 测试 |
| §11 classify_error | Task 6 |
| §12 集成点不改 | Task 9 Step 2/3 回归 |
| §13.1 网关透明性硬前置 | Task 1（运维确认 + spike） |
| §13.3 encrypted_content 可得性断言 | Task 1 spike + Task 7 round-trip |
| §13.6 wire 形状硬约束 | 启动前提 §2 + Task 2/3 测试 |
| §14 测试策略 | Task 3–8 各测试文件 |
| §15 明确不做 | 全程不引入（无 previous_response_id / 无 prompt cache / 无直连 openai / 无 verbosity） |
| §16 完成标准 | Task 9 Step 3 逐项核对 |

**Placeholder 扫描**：各 Task 的 code step 均含完整代码，无 TBD/TODO/"类似 Task N"。
**类型一致性**：`ResponsesTransport` 构造签名（Task 2）与 factory builder（Task 2）、convert/build_kwargs（Task 3）、normalize helper（Task 4）一致；`provider_state` payload 形状 `{"reasoning":[...]}` 在 Task 3（回放读取）、Task 4/5（产出）、Task 7（round-trip）一致；`transport_tag=="responses"` 全程一致。
