# 阶段三 a：中立 IR 与 provider_state 地基 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把消息契约从「OpenAI dict」改为「中立 `list[Message]` IR」，让 wire 序列化/校验下沉 `ChatCompletionsTransport`，并建好 `provider_state` 全链路通道（字段/聚合/持久化/契约），仅在 chat_completions 上验证（恒不产 state，证明通道惰性）。

**Architecture:** kernel 只搬运中立 `Message` 与不透明 `provider_state`；协议无关的语义（canonicalize、tool-turn 配对校验）留 kernel/共享层，OpenAI wire 专属的序列化与形状校验下沉到 transport。`provider_state` 作为对 kernel 不透明、带 transport tag 的回放袋，从 transport 流末产出，经 `stream_llm_items` 聚合进 `LLMResponse`、由 agent 写入 `AssistantMessage`，再贯通持久化/resume；3a 不产真实 state，只把通道、聚合、持久化、契约全部建好。

**Tech Stack:** Python 3.10+、Pydantic v2、FastAPI、asyncio、pytest、uv 环境。

---

## 启动前提（硬前置，违反则停止）

本计划的基线是 **阶段二核心已落地且 stage2 残留清干净后的仓库状态**（见 spec §1）。截至 2026-06-08 当前分支，`merge: provider aggregation stage2` 已是祖先，`matmaster/providers/transport.py`、`matmaster/providers/transports/chat_completions.py`、`_TRANSPORT_BUILDERS`、`providers/profiles/default` 新配置结构均已存在；但 evaluation/devshell 仍保留 Bedrock/Claude route/fallback 语义。`Task 1` 是前置门槛核验，未通过禁止往下做；若发现 residual，先回到 stage2 收尾清理，而不是在 3a 内塞兼容或绕过。

3a 真正改造的核心文件（与 stage2 产物的关系）：

| 文件 | 来源 | 3a 角色 |
|---|---|---|
| `matmaster/types/messages.py` | 现存 | 加 `ProviderState` + 三类内容字段；删 `to_api_dict` |
| `matmaster/types/llm_provider.py` | 现存 | `chat/chat_stream` 签名改 `list[Message]` |
| `matmaster/types/message_normalization.py` | 现存 | wire 函数下沉 transport；tool-turn 校验改中立 |
| `matmaster/core/message_pipeline.py` | 现存 | 收窄为只产 canonical `list[Message]` |
| `matmaster/core/agent_llm_stream.py` | 现存 | 聚合 `provider_state` + 参数契约改 `list[Message]` |
| `matmaster/core/agent.py` | 现存 | 写 `provider_state` + 自然完成分支条件发射 |
| `matmaster/context/compaction.py` | 现存 | `estimate_tokens` 中立序列化；summary call 传 `list[Message]` |
| `src/services/chat_history.py` | 现存 | `events_to_messages` tail restore 携带 `provider_state` |
| `src/services/history_checkpoint_codec.py` | 现存 | `validate_base_messages` 改中立校验 |
| `matmaster/providers/transport.py` | **stage2 产物** | 加 `transport_tag` + `_claim_provider_state` helper |
| `matmaster/providers/transports/chat_completions.py` | **stage2 产物** | `convert_messages(list[Message])` 实化 + wire helper 迁入 |

---

## 文件结构与职责（3a 落定后）

划分原则：**协议无关的归 kernel/共享层，wire 专属的归 transport。**

- `matmaster/types/messages.py`：中立 IR（`SystemMessage`/`UserMessage`/`AssistantMessage`/`ToolMessage`）+ `ProviderState` + `LLMResponse`/`StreamChunk`。不再有任何 OpenAI 序列化（`to_api_dict` 删除）。
- `matmaster/types/message_normalization.py`：协议无关的 canonicalize（`canonicalize_messages_for_provider`/`_merge_user_messages`）、中立 tool-turn 配对校验（`validate_tool_turn_sequence(list[Message])`，供 kernel/checkpoint/transport 三方复用）、持久化还原（`restore_persisted_assistant_state`/`_is_assistant_like_payload`）。OpenAI wire 专属函数全部迁出。
- `matmaster/providers/transports/chat_completions.py`：OpenAI wire 知识唯一归属地——`convert_messages` + 各 role 序列化 helper + `validate_openai_messages`/`_validate_user_content`。
- `matmaster/providers/transport.py`：`transport_tag` 声明 + `_claim_provider_state` tag 认领/丢弃 helper。
- `matmaster/core/message_pipeline.py`：只产 canonical `list[Message]`（增量合并连续 `UserMessage`），不再产 dict、不再校验。
- `matmaster/core/agent_llm_stream.py` / `agent.py`：搬运中立 IR 与不透明 `provider_state`，不读 payload。
- 持久化层（`chat_history.py` / `history_checkpoint_codec.py`）：`provider_state` 随 `AssistantMessage.model_dump(mode="json")` 自然贯通，补齐两个还原断点。

---

## Task 1: 前置门槛核验（stage2 核心已落地，residual 已清干净）

**Files:**
- 只读核验，无修改。

- [ ] **Step 1: 核验 stage2 聚合核心已落地**

Run:
```bash
cd /Users/kealdoom/Developer/dp/matmaster/matmaster-evo
ls matmaster/providers/transport.py matmaster/providers/transports/chat_completions.py
grep -n "_TRANSPORT_BUILDERS" matmaster/providers/llm_factory.py
grep -n "transport_tag\|def convert_messages\|def build_kwargs" matmaster/providers/transports/chat_completions.py
```
Expected: 三个文件均存在；`_TRANSPORT_BUILDERS` dispatch 表存在；`ChatCompletionsTransport` 已有 `convert_messages`（stage2 为 identity 直通）。

- [ ] **Step 2: 核验 stage2 残留消费者已清除（spec §1 硬前置）**

Run:
```bash
grep -rn "BedrockProvider\|bedrock_converse\|bedrock_region" matmaster/providers/ matmaster/config/llm.py
grep -rn "prof\.base_url\|profile\.base_url" matmaster/devshell/repl.py
grep -rn "bedrock-claude-opus\|global\.anthropic\.claude-opus-4-6-v1\|claude-sonnet-4-6" \
  evaluation/scripts/devshell evaluation/devshell_agent matmaster/devshell tests/evaluation tests/matmaster/devshell
ls matmaster/providers/bedrock_provider.py 2>&1 | head -1
```
Expected: 前三条**无输出**；`bedrock_provider.py` 报 `No such file`。

说明：`matmaster/devshell/repl.py` 中合法读取 `rr.provider.base_url` 不算残留；连接字段已经迁到 `ProviderConfig`，不能用裸 `base_url` grep 误判。若第三条命中 `evaluation/scripts/devshell/eval_model_routes.py`、`matmaster/devshell/debug_run.py`、`tests/evaluation/test_devshell_agent_subprocess.py` 或 `tests/matmaster/devshell/test_run_devshell_eval_script.py`，说明 stage2 仍未收尾，停止 3a。

- [ ] **Step 3: 核验现状测试基线全绿**

Run: `uv run pytest tests/ -q`
Expected: PASS（stage2 完成后的绿点）。若任一核验失败，**停止本计划**，回到 stage2 收尾。当前仓库已验证过 `uv run pytest tests/ -q` 可用；`ruff`/`mypy` 当前 uv 环境未安装，不能作为前置门槛。

---

## Task 2: `ProviderState` 模型 + 三类内容字段

**Files:**
- Modify: `matmaster/types/messages.py`（在 `LLMResponse` 之前新增 `ProviderState`；`AssistantMessage`/`LLMResponse`/`StreamChunk` 各加字段）
- Test: `tests/matmaster/types/test_provider_state.py`

纯加法，不触发任何现有行为变化（chat_completions 恒不产 state）。

- [ ] **Step 1: 写失败测试（JSON 契约 + round-trip）**

Create `tests/matmaster/types/test_provider_state.py`:
```python
import pytest

from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    ProviderState,
    StreamChunk,
)


def test_provider_state_is_frozen():
    state = ProviderState(transport="fake", payload={"k": "v"})
    with pytest.raises(Exception):
        state.transport = "other"


def test_provider_state_json_round_trip():
    state = ProviderState(transport="anthropic_messages", payload={"sig": "abc", "n": 1})
    dumped = state.model_dump(mode="json")
    assert dumped == {"transport": "anthropic_messages", "payload": {"sig": "abc", "n": 1}}
    assert ProviderState.model_validate(dumped) == state


def test_assistant_message_carries_provider_state_in_json():
    state = ProviderState(transport="fake", payload={"x": [1, 2, 3]})
    msg = AssistantMessage(content="hi", provider_state=state)
    dumped = msg.model_dump(mode="json")
    assert dumped["provider_state"] == {"transport": "fake", "payload": {"x": [1, 2, 3]}}
    assert AssistantMessage.model_validate(dumped).provider_state == state


def test_defaults_none():
    assert AssistantMessage(content="x").provider_state is None
    assert LLMResponse(content="x").provider_state is None
    assert StreamChunk(content="x").provider_state is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/types/test_provider_state.py -q`
Expected: FAIL（`ImportError: cannot import name 'ProviderState'`）。

- [ ] **Step 3: 新增 `ProviderState` 模型**

在 `matmaster/types/messages.py` 中 `class LLMResponse(BaseModel):`（当前 301 行）之前插入：
```python
class ProviderState(BaseModel):
    """Provider 回放状态：对 kernel 不透明、transport 私有、带 transport tag。

    kernel 原样存取、不解读 payload；只有 tag 匹配的 transport 在 convert 时认领。
    payload 必须只含 JSON-serializable 值（dict/list/str/int/float/bool/None）；
    持久化统一走 model_dump(mode="json")，非 JSON 值会在持久化层炸。
    """

    model_config = ConfigDict(frozen=True)

    transport: str
    payload: dict[str, Any]
```
（`BaseModel`/`ConfigDict`/`Any` 已在文件顶部导入，无需新增 import。）

- [ ] **Step 4: 三类内容类各加 `provider_state` 字段**

`AssistantMessage`（当前 254-256 行，`reasoning_content` 之后）加：
```python
    provider_state: ProviderState | None = None
```
`LLMResponse`（当前 `degraded: bool = False` 之后）加：
```python
    provider_state: ProviderState | None = None
```
`StreamChunk`（当前 `usage_vendor: dict[str, Any] | None = None` 之后）加：
```python
    provider_state: ProviderState | None = None
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/types/test_provider_state.py -q`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add matmaster/types/messages.py tests/matmaster/types/test_provider_state.py
git commit -m "feat(types): add ProviderState model and provider_state fields"
```

---

## Task 3: 中立 `validate_tool_turn_sequence(list[Message])`

**Files:**
- Modify: `matmaster/types/message_normalization.py`（新增中立校验函数；本任务先 additive 加入，旧 OpenAI 版本在 Task 6 删除）
- Test: `tests/matmaster/types/test_validate_tool_turn_sequence.py`

读 `Message` 字段（`AssistantMessage.tool_calls[].id` / `ToolMessage.tool_call_id`）而非 OpenAI dict，语义等价于原 `validate_openai_tool_turn_sequence`。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/types/test_validate_tool_turn_sequence.py`:
```python
import pytest

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import (
    AssistantMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _assistant_with_calls(*ids):
    return AssistantMessage(
        content="",
        tool_calls=[ToolCallData(id=i, name="t", arguments={}) for i in ids],
    )


def test_valid_paired_sequence_passes():
    msgs = [
        UserMessage(content="hi"),
        _assistant_with_calls("c1"),
        ToolMessage(content="ok", tool_call_id="c1", tool_name="t"),
    ]
    validate_tool_turn_sequence(msgs)  # no raise


def test_orphan_tool_raises():
    msgs = [UserMessage(content="hi"), ToolMessage(content="x", tool_call_id="c1", tool_name="t")]
    with pytest.raises(LLMError) as exc:
        validate_tool_turn_sequence(msgs)
    assert exc.value.error_category == "bad_request"


def test_duplicate_tool_call_id_raises():
    msgs = [_assistant_with_calls("c1", "c1")]
    with pytest.raises(LLMError):
        validate_tool_turn_sequence(msgs)


def test_missing_tool_result_raises():
    msgs = [_assistant_with_calls("c1"), UserMessage(content="next")]
    with pytest.raises(LLMError):
        validate_tool_turn_sequence(msgs)


def test_tool_result_without_matching_call_raises():
    msgs = [_assistant_with_calls("c1"), ToolMessage(content="x", tool_call_id="c2", tool_name="t")]
    with pytest.raises(LLMError):
        validate_tool_turn_sequence(msgs)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/types/test_validate_tool_turn_sequence.py -q`
Expected: FAIL（`ImportError: cannot import name 'validate_tool_turn_sequence'`）。

- [ ] **Step 3: 实现中立校验函数**

在 `matmaster/types/message_normalization.py` 顶部 import 加 `ToolMessage`：
```python
from matmaster.types.messages import AssistantMessage, Message, ToolMessage, UserMessage
```
在文件末尾（`restore_persisted_assistant_state` 之后）新增：
```python
def validate_tool_turn_sequence(messages: list[Message]) -> None:
    """Protocol-neutral tool_call <-> tool_result pairing validation.

    Reads Message fields (AssistantMessage.tool_calls[].id / ToolMessage.tool_call_id)
    instead of OpenAI wire dicts. Shared by kernel, checkpoint codec, and transports.
    """
    pending_tool_ids: set[str] = set()
    seen_tool_ids: set[str] = set()

    for message in messages:
        if isinstance(message, ToolMessage):
            tool_id = str(message.tool_call_id or "")
            if tool_id in seen_tool_ids:
                raise LLMError(
                    f"duplicate tool_result ids for assistant turn: {tool_id}",
                    retryable=False,
                    error_category="bad_request",
                )
            if not pending_tool_ids and not seen_tool_ids:
                raise LLMError(
                    "orphan tool message after assistant without tool_calls",
                    retryable=False,
                    error_category="bad_request",
                )
            if not tool_id or tool_id not in pending_tool_ids:
                raise LLMError(
                    f"tool_result without matching previous assistant tool_call: {tool_id}",
                    retryable=False,
                    error_category="bad_request",
                )
            seen_tool_ids.add(tool_id)
            pending_tool_ids.remove(tool_id)
            continue

        if pending_tool_ids:
            raise LLMError(
                f"missing tool_result ids for assistant turn: {sorted(pending_tool_ids)}",
                retryable=False,
                error_category="bad_request",
            )

        seen_tool_ids.clear()

        if not isinstance(message, AssistantMessage):
            continue

        declared_ids = [str(tc.id or "") for tc in (message.tool_calls or [])]
        for tool_id in declared_ids:
            if not tool_id:
                raise LLMError(
                    "assistant tool_call missing id",
                    retryable=False,
                    error_category="bad_request",
                )
        if len(declared_ids) != len(set(declared_ids)):
            duplicates = sorted(
                {tool_id for tool_id in declared_ids if declared_ids.count(tool_id) > 1}
            )
            raise LLMError(
                f"duplicate tool_call ids in outbound assistant turn: {duplicates}",
                retryable=False,
                error_category="bad_request",
            )

        seen_tool_ids = set()
        pending_tool_ids = set(declared_ids)

    if pending_tool_ids:
        raise LLMError(
            f"missing tool_result ids for assistant turn: {sorted(pending_tool_ids)}",
            retryable=False,
            error_category="bad_request",
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/types/test_validate_tool_turn_sequence.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add matmaster/types/message_normalization.py tests/matmaster/types/test_validate_tool_turn_sequence.py
git commit -m "feat(types): add protocol-neutral validate_tool_turn_sequence"
```

---

## Task 4: `Transport._claim_provider_state` + `transport_tag`

**Files:**
- Modify: `matmaster/providers/transport.py`（stage2 基类，加 `transport_tag` 声明 + helper）
- Modify: `matmaster/providers/transports/chat_completions.py`（声明 `transport_tag = "chat_completions"`）
- Test: `tests/matmaster/providers/test_provider_state_claim.py`

tag 丢弃契约（spec §4.4）：3a 单 transport 不触发注入，仅以纯函数单测验证认领/丢弃逻辑，为 3b 引入第二个 transport 后自动生效铺路。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/providers/test_provider_state_claim.py`:
```python
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import AssistantMessage, ProviderState


def _make_transport():
    # 复用 stage2 测试里构造 ChatCompletionsTransport 的 fixture/工厂；
    # 若 stage2 测试有 helper，import 之；否则按其构造签名实例化。
    return ChatCompletionsTransport.__new__(ChatCompletionsTransport)


def test_claim_returns_payload_when_tag_matches():
    t = _make_transport()
    msg = AssistantMessage(
        content="x",
        provider_state=ProviderState(transport="chat_completions", payload={"k": 1}),
    )
    assert t._claim_provider_state(msg) == {"k": 1}


def test_claim_returns_none_when_tag_mismatch():
    t = _make_transport()
    msg = AssistantMessage(
        content="x",
        provider_state=ProviderState(transport="anthropic_messages", payload={"k": 1}),
    )
    assert t._claim_provider_state(msg) is None


def test_claim_returns_none_when_no_state():
    t = _make_transport()
    assert t._claim_provider_state(AssistantMessage(content="x")) is None
```
> 注：`__new__` 绕过 `__init__` 仅用于纯函数 helper 测试（`_claim_provider_state` 不依赖实例状态、只读 `self.transport_tag` 类常量）。若 stage2 已提供轻量构造 fixture，优先复用之。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/providers/test_provider_state_claim.py -q`
Expected: FAIL（`AttributeError: ... '_claim_provider_state'` 或 `transport_tag`）。

- [ ] **Step 3: 在 `Transport` 基类加声明与 helper**

在 `matmaster/providers/transport.py` 的 `Transport` 基类中，加类级 tag 声明（子类覆盖）与 helper。import 区加 `from typing import Any` 与 `from matmaster.types.messages import AssistantMessage`（若尚未导入）：
```python
class Transport:
    # ... stage2 既有内容（__init__ / property / 生命周期 / seam 声明）...

    transport_tag: str = ""  # 子类声明的 wire 协议标签；基类不可直接使用

    def _claim_provider_state(self, msg: AssistantMessage) -> dict[str, Any] | None:
        """tag 匹配则返回不透明 payload，否则 None（跨协议丢弃回放状态）。"""
        state = msg.provider_state
        if state is None or state.transport != self.transport_tag:
            return None
        return state.payload
```

- [ ] **Step 4: `ChatCompletionsTransport` 声明 tag**

在 `matmaster/providers/transports/chat_completions.py` 的 `class ChatCompletionsTransport(Transport):` 类体顶部加：
```python
    transport_tag = "chat_completions"
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/providers/test_provider_state_claim.py -q`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add matmaster/providers/transport.py matmaster/providers/transports/chat_completions.py tests/matmaster/providers/test_provider_state_claim.py
git commit -m "feat(providers): add provider_state tag-claim helper to Transport"
```

---

## Task 5: `provider_state` 流式聚合 + `AssistantMessage` 写入

**Files:**
- Modify: `matmaster/core/agent_llm_stream.py`（`stream_llm_items` 捕获 `chunk.provider_state` → 写入 `LLMResponse`）
- Modify: `matmaster/core/agent.py:400-405` 与 `:409-413`（两处 `AssistantMessage` 组装加 `provider_state=response.provider_state`）
- Test: `tests/matmaster/core/test_provider_state_aggregation.py`

纯加法：chat_completions 恒不产 `provider_state`，行为等价。本任务只接通 transport→LLMResponse→AssistantMessage 的搬运。

- [ ] **Step 1: 写失败测试（假 transport 流末产 state → 聚合 → LLMResponse）**

Create `tests/matmaster/core/test_provider_state_aggregation.py`:
```python
import pytest

from matmaster.types.messages import ProviderState, StreamChunk


class _FakeProvider:
    stream_timeout = 30.0
    stream_idle_timeout = 30.0
    max_retries = 1
    retry_delay = 0.0

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="hello")
        yield StreamChunk(finish_reason="stop")
        yield StreamChunk(
            provider_state=ProviderState(transport="fake", payload={"sig": "z"})
        )


@pytest.mark.asyncio
async def test_stream_llm_items_aggregates_provider_state():
    from types import SimpleNamespace

    from matmaster.core.agent_llm_stream import stream_llm_items

    kernel_resources = SimpleNamespace(llm_provider=_FakeProvider())
    final = None
    async for item in stream_llm_items(kernel_resources, [], None):
        if item.llm_response is not None:
            final = item.llm_response
    assert final is not None
    assert final.provider_state == ProviderState(transport="fake", payload={"sig": "z"})


@pytest.mark.asyncio
async def test_chat_completions_style_stream_leaves_provider_state_none():
    from types import SimpleNamespace

    from matmaster.core.agent_llm_stream import stream_llm_items

    class _PlainProvider(_FakeProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            yield StreamChunk(content="hi")
            yield StreamChunk(finish_reason="stop")

    kernel_resources = SimpleNamespace(llm_provider=_PlainProvider())
    final = None
    async for item in stream_llm_items(kernel_resources, [], None):
        if item.llm_response is not None:
            final = item.llm_response
    assert final.provider_state is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/core/test_provider_state_aggregation.py -q`
Expected: FAIL（`LLMResponse.provider_state` 恒 None，第一个测试断言失败）。

- [ ] **Step 3: `stream_llm_items` 聚合 `provider_state`**

`matmaster/core/agent_llm_stream.py`：在累加局部变量区（当前 `usage_vendor: dict[str, Any] | None = None` 之后，约 93 行）加：
```python
    captured_provider_state = None
```
在 chunk 处理循环内（与 `if chunk.usage_vendor is not None:` 同级，约 155-156 行之后）加：
```python
            if chunk.provider_state is not None:
                captured_provider_state = chunk.provider_state
```
最后组装 `LLMResponse`（当前 242-251 行）加字段：
```python
    yield _KernelItem(
        llm_response=LLMResponse(
            content=visible_content,
            reasoning_content=joined_reasoning or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            usage_vendor=usage_vendor,
            provider_state=captured_provider_state,
        )
    )
```

- [ ] **Step 4: `agent.py` 两处组装写入 `provider_state`**

`matmaster/core/agent.py` 自然完成分支（当前 400-405 行）：
```python
                state.messages.append(
                    AssistantMessage(
                        content=response.content,
                        reasoning_content=response.reasoning_content,
                        provider_state=response.provider_state,
                    )
                )
```
tool-call 分支（当前 409-413 行）：
```python
            assistant_msg = AssistantMessage(
                content=response.content,
                tool_calls=response.tool_calls,
                reasoning_content=response.reasoning_content,
                provider_state=response.provider_state,
            )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/core/test_provider_state_aggregation.py -q`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add matmaster/core/agent_llm_stream.py matmaster/core/agent.py tests/matmaster/core/test_provider_state_aggregation.py
git commit -m "feat(core): aggregate provider_state from stream into AssistantMessage"
```

---

## Task 6: 中立 IR 切换（原子改造）

> **为什么这是一个原子任务而非多个独立绿点：** kernel↔transport 之间流动的类型从 `list[dict]` 变为 `list[Message]`。生产端（`message_pipeline`）、传递端（`stream_llm_items`/`agent`）、消费端（`transport.convert_messages`/Protocol 签名/compaction summary call）必须同步翻转——项目「禁止任何兼容兜底」，不允许双形态过渡。因此本任务的 Step 顺序内部存在「中间态测试不全绿」的窗口，**完整测试套件的绿点是 Step 13 的统一门槛**；Step 1-2 的 `convert_messages` 等价性是可独立直测的单元（直接调方法、不依赖 live 接线）。

**Files:**
- Modify: `matmaster/providers/transports/chat_completions.py`（`convert_messages(list[Message])` 实化 + wire helper 迁入）
- Modify: `matmaster/types/messages.py`（删 `to_api_dict` 及子类覆盖）
- Modify: `matmaster/types/message_normalization.py`（删 `normalize_and_validate_openai_messages`；wire 函数迁出；删旧 `validate_openai_tool_turn_sequence`）
- Modify: `matmaster/types/llm_provider.py`（`chat/chat_stream` 签名 → `list[Message]`）
- Modify: `matmaster/core/message_pipeline.py`（收窄为产 `list[Message]`）
- Modify: `matmaster/core/agent_llm_stream.py`（参数 `api_messages` → `canonical_messages: list[Message]`）
- Modify: `matmaster/core/agent.py`（`feed_tail` 返回值改名 + `_call_llm_streaming` 形参）
- Modify: `matmaster/context/compaction.py`（summary call 传 `list[Message]`；`estimate_tokens` 中立序列化）
- Modify: `src/services/history_checkpoint_codec.py`（`validate_base_messages` 改中立校验）
- Test: `tests/matmaster/providers/test_chat_completions_convert_messages.py`（新增）；迁移 `test_message_normalization*` 相关断言

- [ ] **Step 1: 写 `convert_messages` 等价性失败测试**

Create `tests/matmaster/providers/test_chat_completions_convert_messages.py`:
```python
import pytest

from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.errors import LLMError
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _convert(messages):
    t = ChatCompletionsTransport.__new__(ChatCompletionsTransport)
    return t.convert_messages(messages)


def test_system_and_user_text():
    out = _convert([SystemMessage(content="sys"), UserMessage(content="hi")])
    assert out == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]


def test_user_with_image_parts():
    msg = UserMessage(
        content="look",
        images=[ImageContentPart(url="http://x/y.png", detail="high")],
    )
    out = _convert([msg])
    assert out[0]["role"] == "user"
    assert out[0]["content"] == [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "http://x/y.png", "detail": "high"}},
    ]


def test_assistant_with_tool_calls():
    msg = AssistantMessage(
        content=None,
        tool_calls=[ToolCallData(id="c1", name="f", arguments={"a": 1})],
    )
    tool = ToolMessage(content="ok", tool_call_id="c1", tool_name="f")
    out = _convert([msg, tool])
    assert out[0]["content"] == ""  # content=None -> ""
    assert out[0]["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": '{"a": 1}'}}
    ]
    assert out[1] == {"role": "tool", "content": "ok", "tool_call_id": "c1"}


def test_content_none_normalized_to_empty_string():
    out = _convert([AssistantMessage(content=None)])
    assert out[0]["content"] == ""


def test_invalid_tool_turn_raises():
    with pytest.raises(LLMError):
        _convert([ToolMessage(content="x", tool_call_id="c1", tool_name="f")])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/providers/test_chat_completions_convert_messages.py -q`
Expected: FAIL（stage2 的 `convert_messages` 是 dict identity 直通，吃 `Message` 报错或断言不符）。

- [ ] **Step 3: 在 `chat_completions.py` 迁入 wire 序列化 helper + 实化 `convert_messages`**

在 `matmaster/providers/transports/chat_completions.py` 顶部 import 区加（从 `messages.py` / `message_normalization.py` 迁移而来）：
```python
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import (
    AssistantMessage,
    Message,
    ToolMessage,
    UserMessage,
)
```
新增 module 级 helper（逻辑来自原 `Message.to_api_dict()` 各分支 + `normalize_messages_for_openai` 的 content 规范化 + `message_normalization.validate_openai_messages`/`_validate_user_content`）：
```python
_OPENAI_COMPATIBLE_ROLES = {"system", "user", "assistant", "tool"}


def _user_message_to_dict(message: UserMessage) -> dict[str, Any]:
    if not message.images:
        return {"role": message.role.value, "content": message.content}
    parts: list[dict[str, Any]] = []
    if message.content:
        parts.append({"type": "text", "text": message.content})
    for image in message.images:
        image_url: dict[str, Any] = {"url": image.url}
        if image.detail is not None:
            image_url["detail"] = image.detail
        parts.append({"type": "image_url", "image_url": image_url})
    return {"role": message.role.value, "content": parts}


def _assistant_message_to_dict(message: AssistantMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role.value, "content": message.content}
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


def _message_to_openai_dict(message: Message) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        payload = _user_message_to_dict(message)
    elif isinstance(message, AssistantMessage):
        payload = _assistant_message_to_dict(message)
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


def _validate_user_content(content: Any, idx: int) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise LLMError(
            f"Outbound user message content must be string or content parts at index {idx}, "
            f"got {type(content).__name__}",
            retryable=False,
            error_category="payload_validation",
        )
    for part_idx, part in enumerate(content):
        if not isinstance(part, dict):
            raise LLMError(
                f"Outbound user content part must be dict at index {idx}.{part_idx}, "
                f"got {type(part).__name__}",
                retryable=False,
                error_category="payload_validation",
            )
        part_type = part.get("type")
        if part_type == "text":
            if isinstance(part.get("text"), str):
                continue
            raise LLMError(
                f"Outbound user text content part must include string text at index {idx}.{part_idx}",
                retryable=False,
                error_category="payload_validation",
            )
        if part_type == "image_url":
            image_url = part.get("image_url")
            if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                continue
            raise LLMError(
                f"Outbound user image content part must include image_url.url at index {idx}.{part_idx}",
                retryable=False,
                error_category="payload_validation",
            )
        raise LLMError(
            f"Unsupported outbound user content part type at index {idx}.{part_idx}: {part_type!r}",
            retryable=False,
            error_category="payload_validation",
        )


def _validate_openai_messages(messages: list[dict[str, Any]]) -> None:
    for idx, message in enumerate(messages):
        role = message.get("role")
        if role not in _OPENAI_COMPATIBLE_ROLES:
            raise LLMError(
                f"Unsupported outbound message role at index {idx}: {role!r}",
                retryable=False,
                error_category="payload_validation",
            )
        content = message.get("content")
        if role == "user":
            _validate_user_content(content, idx)
            continue
        if not isinstance(content, str):
            raise LLMError(
                f"Outbound message content must be string for {role} message "
                f"at index {idx}, got {type(content).__name__}",
                retryable=False,
                error_category="payload_validation",
            )
```
把 `ChatCompletionsTransport.convert_messages` 从 stage2 的 identity 直通改为：
```python
    def convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """canonical list[Message] -> OpenAI-compatible wire dicts.

        序列化（原 Message.to_api_dict）+ content=None→"" 规范化 + wire 形状校验下沉于此；
        tool-turn 配对复用中立 validate_tool_turn_sequence。
        provider_state 的 tag 认领在 3a 单 transport 下不触发注入（OpenAI wire 无回放块）。
        """
        validate_tool_turn_sequence(messages)
        wire = [_message_to_openai_dict(m) for m in messages]
        _validate_openai_messages(wire)
        return wire
```
同步把 stage2 `build_kwargs` 内 `kwargs["messages"] = self.convert_messages(messages)` 处的 `messages` 形参类型由 `list[dict]` 改 `list[Message]`（如 stage2 在该方法上有类型注解）。

- [ ] **Step 4: 运行 `convert_messages` 等价性测试确认通过**

Run: `uv run pytest tests/matmaster/providers/test_chat_completions_convert_messages.py -q`
Expected: PASS。（此时整体 live 路径尚不一致，后续 Step 恢复。）

- [ ] **Step 5: `message_pipeline` 收窄为产 canonical `list[Message]`**

整体替换 `matmaster/core/message_pipeline.py` 的实现内容为（删除 `_to_normalized_api_dict`、`_ToolTurnValidator`、`revalidate_full`、`_api_cache`、所有 OpenAI 校验 import）：
```python
"""Incremental canonical-message pipeline for the agent main loop.

Caches the canonicalized (merged consecutive UserMessage) Message prefix and
only re-merges the tail between turns. Wire serialization and OpenAI-shape
validation live in the transport (ChatCompletionsTransport.convert_messages),
not here.
"""

from __future__ import annotations

import logging

from matmaster.types.message_normalization import _merge_user_messages
from matmaster.types.messages import Message, UserMessage

logger = logging.getLogger(__name__)


class IncrementalMessagePipeline:
    """Stateful canonical-Message builder for the agent main loop."""

    def __init__(self) -> None:
        self._canonical_cache: list[Message] = []
        self._source_len = 0
        self._prefix_fingerprint: tuple[int, int, int] | None = None

    def reset(self) -> None:
        """Drop all caches. Next feed_tail rebuilds from scratch."""
        self._canonical_cache = []
        self._source_len = 0
        self._prefix_fingerprint = None

    def feed_tail(self, messages: list[Message]) -> list[Message]:
        """Process messages tail and return canonical list[Message].

        Reuses prefix cache and only processes messages[self._source_len:].
        Prefix mutation detection is best-effort only; any path that rewrites
        previously processed messages must call reset() explicitly.
        """
        if len(messages) < self._source_len:
            logger.warning(
                "pipeline prefix shrunk; auto-reset",
                extra={
                    "observed_len": len(messages),
                    "expected_source_len": self._source_len,
                },
            )
            self.reset()

        if self._source_len > 0 and self._prefix_fingerprint is not None:
            current = (
                self._source_len,
                id(messages[0]),
                id(messages[self._source_len - 1]),
            )
            if current != self._prefix_fingerprint:
                logger.warning(
                    "pipeline prefix mutation detected; auto-reset",
                    extra={"observed": current, "expected": self._prefix_fingerprint},
                )
                self.reset()

        tail = messages[self._source_len :]
        if not tail:
            return list(self._canonical_cache)

        for msg in tail:
            if (
                self._canonical_cache
                and isinstance(self._canonical_cache[-1], UserMessage)
                and isinstance(msg, UserMessage)
            ):
                self._canonical_cache[-1] = _merge_user_messages(
                    self._canonical_cache[-1], msg
                )
                continue
            self._canonical_cache.append(msg)

        self._source_len = len(messages)
        self._prefix_fingerprint = (
            self._source_len,
            id(messages[0]),
            id(messages[self._source_len - 1]),
        )
        return list(self._canonical_cache)
```
> 注：spec §5 提到「若收窄后管线复杂度不值当，可进一步塌缩为每轮直调 `canonicalize_messages_for_provider` 并删模块」。本计划保留收窄后的增量缓存（避免长历史每轮重合并）；是否进一步塌缩留实施者依实测决定，不在本计划强制。

- [ ] **Step 6: `stream_llm_items` / `call_llm_streaming` 参数契约改 `list[Message]`**

`matmaster/core/agent_llm_stream.py`：
- 顶部 import 加 `from matmaster.types.messages import Message`（与既有 messages import 合并）。
- `stream_llm_items` 形参 `api_messages: list[dict[str, Any]]` → `canonical_messages: list[Message]`（73-80 行）。
- 函数体内引用：`stream_id = f"turn-{len(canonical_messages)}"`（91 行）、`chat_stream(canonical_messages, tool_defs, ...)`（107-109 行）、日志 `len(canonical_messages)`（约 221 行）。
- `call_llm_streaming` 形参 `api_messages` → `canonical_messages: list[Message]`（254-260 行）、内部传给 `stream_llm_items(kernel_resources, canonical_messages, ...)`（275 行）。

- [ ] **Step 7: `agent.py` `feed_tail` 返回值改名 + `_call_llm_streaming` 形参**

`matmaster/core/agent.py`：
- 328 行：`api_messages = state.pipeline.feed_tail(state.messages)` → `canonical_messages = state.pipeline.feed_tail(state.messages)`。
- 332-334 行：`self._call_llm_streaming(kernel_resources, api_messages, tool_defs, ...)` → 传 `canonical_messages`。
- `_call_llm_streaming`（487-502 行）形参 `api_messages: list[dict[str, Any]]` → `canonical_messages: list[Message]`，内部传 `call_llm_streaming(kernel_resources, canonical_messages, tool_defs, ...)`。
- 确认 `agent.py` 顶部已 import `Message`（若未，则加）。

- [ ] **Step 8: `LLMProvider` Protocol 签名改 `list[Message]`**

`matmaster/types/llm_provider.py`：
- import 加 `Message`：`from matmaster.types.messages import LLMResponse, Message, StreamChunk`。
- `chat`（47-53 行）与 `chat_stream`（55-61 行）的 `messages: list[dict[str, Any]]` → `messages: list[Message]`。
- docstring 行 `Retry logic lives in Kernel._call_llm_streaming()...` 不变。

- [ ] **Step 9: compaction summary call 传 `list[Message]` + `estimate_tokens` 中立序列化**

`matmaster/context/compaction.py`：
- import 区删 `normalize_and_validate_openai_messages`（30 行），保留 `canonicalize_messages_for_provider`。
- summary call（339-347 行）改为：
```python
    summary_messages = [*prep.messages, compact_request]
    canonical_messages = canonicalize_messages_for_provider(summary_messages)
    return await llm_provider.chat(
        canonical_messages,
        tools=tool_definitions,
        tool_choice="none",
    )
```
- `estimate_tokens`（114-125 行）改中立序列化，新增 module 级 helper：
```python
def _message_size_text(msg: Message) -> str:
    parts: list[str] = [msg.content or ""]
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        parts.append(reasoning)
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            parts.append(tc.name)
            parts.append(tc.arguments_json)
    return "\n".join(parts)


def estimate_tokens(messages: list[Message], safety_margin: float = 1.0) -> int:
    """Estimate token count for a list of messages (heuristic, protocol-neutral)."""
    total = 0
    enc = _get_encoder()
    for msg in messages:
        text = _message_size_text(msg)
        if enc is not None:
            total += len(enc.encode(text))
        else:
            total += max(len(text) // 4, 1)
        total += 4
    return int(total * safety_margin)
```
- 若 `json` import 在 compaction.py 中此后无其它用途，删除 `import json`（用 `grep -n "json\." matmaster/context/compaction.py` 确认；当前仅 estimate_tokens 用到）。

- [ ] **Step 10: `history_checkpoint_codec.validate_base_messages` 改中立校验**

`src/services/history_checkpoint_codec.py`：
- import（6-8 行）`normalize_and_validate_openai_messages` → `validate_tool_turn_sequence`：
```python
from matmaster.types.message_normalization import validate_tool_turn_sequence
```
- `validate_base_messages` 内 `normalize_and_validate_openai_messages(messages)`（74 行）→ `validate_tool_turn_sequence(messages)`。其余 try/except `LLMError` → `ValueError` 翻译与下游 `ChatHistoryConverter.validate_dialog_messages_for_llm`、`UserMessage`/`SystemMessage`/marker 校验全部不变。

- [ ] **Step 11: 删 `to_api_dict` 与 `message_normalization` 中 wire 专属函数**

`matmaster/types/messages.py`：删除 `Message.to_api_dict`（208-210 行）、`UserMessage.to_api_dict`（231-244 行）、`AssistantMessage.to_api_dict`（258-278 行）、`ToolMessage.to_api_dict`（292-298 行）；同步把模块 docstring（6 行 `via to_api_dict()`、201 行、251 行、284 行）对 `to_api_dict` 的描述删除/改写。

`matmaster/types/message_normalization.py`：删除 wire 专属函数——`_message_to_api_dict`（42-45 行）、`normalize_messages_for_openai`（56-75 行）、`normalize_and_validate_openai_messages`（78-84 行）、`_validate_user_content`（87-125 行）、`validate_openai_messages`（128-148 行）、`validate_openai_tool_turn_sequence`（151-230 行）。保留 `canonicalize_messages_for_provider`、`_merge_user_messages`、`validate_tool_turn_sequence`（Task 3 新增）、`restore_persisted_assistant_state`、`_is_assistant_like_payload`。删除随之无用的 `_OPENAI_COMPATIBLE_ROLES`（13 行）。确认 `LLMError` import 仍被 `validate_tool_turn_sequence` 使用（保留）。

> 这些函数体已在 Step 3 迁入 `chat_completions.py`（移动非复制，净代码不增）。

- [ ] **Step 12: 迁移/更新受影响的现有测试桩**

- 删除/迁移 `tests/.../test_message_normalization*` 中针对 `normalize_messages_for_openai` / `validate_openai_messages` / `validate_openai_tool_turn_sequence` / `normalize_and_validate_openai_messages` 的断言：role/content shape 校验用例迁到 `tests/matmaster/providers/test_chat_completions_convert_messages.py`（对 `convert_messages` 断言）；tool-turn 用例已由 `tests/matmaster/types/test_validate_tool_turn_sequence.py` 覆盖。
- 现有传 `list[dict]` 给 `chat`/`chat_stream`/`stream_llm_items`/`call_llm_streaming` 的测试桩与集成测试改传 `list[Message]`（用 `SystemMessage`/`UserMessage`/`AssistantMessage`/`ToolMessage` 构造）。
- `message_pipeline` 测试：删除针对 `_ToolTurnValidator` / `_to_normalized_api_dict` / `revalidate_full` / dict 输出的断言；保留/改写「合并连续 UserMessage、返回 canonical `list[Message]`」断言。
- 用 `uv run pytest tests/ -q 2>&1 | grep -i "fail\|error" | head -40` 定位需改的测试，逐个改传中立 IR。

- [ ] **Step 13: 运行完整套件 + 当前环境可用的静态门槛**

Run:
```bash
uv run pytest tests/ -q
git diff --check
uv run python -m compileall matmaster src
```
Expected: pytest PASS（全绿）；`git diff --check` 无输出；`compileall` 退出码 0。

说明：当前 `pyproject.toml` 的 `dev` extra 只包含 pytest/pytest-asyncio，`uv run ruff ...` 与 `uv run mypy ...` 在当前 uv 环境会直接 `Failed to spawn`。若后续仓库重新把 ruff/mypy 加回环境，可在本步骤之后额外运行，但不能把当前不可用命令写成硬门槛。

- [ ] **Step 14: 提交**

```bash
git add matmaster/ src/ tests/
git commit -m "refactor(providers): switch kernel<->transport contract to neutral list[Message] IR"
```

---

## Task 7: 持久化必改点一——自然完成分支条件发射 assistant_state

**Files:**
- Modify: `matmaster/core/agent.py`（自然完成分支 400-407 行，在 `provider_state` 非 None 时发 internal-only `AssistantStateEvent`）
- Test: `tests/matmaster/core/test_natural_finish_provider_state_persist.py`

spec §6.2：`AssistantStateEvent` 当前只在 tool-call 分支发；普通文本回复（无 tool_calls）的 `provider_state` 重启/跨 worker resume 后丢失。条件发射（仅 `provider_state` 非 None）保证 chat_completions 在 3a 行为等价（不新增事件量）。

- [ ] **Step 1: 写失败测试**

Create `tests/matmaster/core/test_natural_finish_provider_state_persist.py`:
```python
import pytest

from matmaster.core.agent import AgentKernel
from matmaster.types.events import AssistantStateEvent
from matmaster.types.messages import ProviderState, StreamChunk
from tests.matmaster.core.agent_kernel_test_helpers import (
    StreamingProvider,
    make_kernel_runtime,
    make_kernel_turn,
)


async def _run_with_chunks(chunks: list[StreamChunk]) -> list:
    kernel = AgentKernel()
    runtime = make_kernel_runtime(provider=StreamingProvider(chunks))
    events = []
    async for item in kernel.run_stream(
        runtime,
        make_kernel_turn("question"),
    ):
        if item.event is not None:
            events.append(item.event)
    return events


@pytest.mark.asyncio
async def test_natural_finish_with_provider_state_emits_assistant_state():
    events = await _run_with_chunks(
        [
            StreamChunk(content="final answer"),
            StreamChunk(finish_reason="stop"),
            StreamChunk(
                provider_state=ProviderState(transport="fake", payload={"sig": "z"})
            ),
        ]
    )
    state_events = [e for e in events if isinstance(e, AssistantStateEvent)]
    assert len(state_events) == 1
    assert state_events[0].state["provider_state"] == {
        "transport": "fake",
        "payload": {"sig": "z"},
    }


@pytest.mark.asyncio
async def test_natural_finish_without_provider_state_emits_no_assistant_state():
    events = await _run_with_chunks(
        [
            StreamChunk(content="plain answer"),
            StreamChunk(finish_reason="stop"),
        ]
    )
    assert [e for e in events if isinstance(e, AssistantStateEvent)] == []
```
> 该测试直接复用当前仓库已有 helper：`tests/matmaster/core/agent_kernel_test_helpers.py` 中的 `StreamingProvider`、`make_kernel_runtime`、`make_kernel_turn`。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/core/test_natural_finish_provider_state_persist.py -q`
Expected: FAIL（第一个测试：自然完成分支当前不发 `AssistantStateEvent`）。

- [ ] **Step 3: 自然完成分支条件发射**

`matmaster/core/agent.py` 自然完成分支（当前 400-407 行，Task 5 已加 `provider_state` 入参）改为：
```python
                natural_msg = AssistantMessage(
                    content=response.content,
                    reasoning_content=response.reasoning_content,
                    provider_state=response.provider_state,
                )
                state.messages.append(natural_msg)
                if response.provider_state is not None:
                    yield _KernelItem(
                        event=AssistantStateEvent(
                            source="agent",
                            state=natural_msg.model_dump(mode="json"),
                            turn_index=turn_index,
                            turn_usage=dict(state.turn_usage),
                            total_usage=dict(state.total_usage),
                            model=state.llm_model,
                            model_profile=state.llm_model_profile,
                            model_route=state.llm_model_route,
                        )
                    )
                yield self._terminal(state, "natural", final_content=response.content)
                return
```
确认 `AssistantStateEvent` 已在 `agent.py` 顶部导入（tool-call 分支 431 行已用，应已导入）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/core/test_natural_finish_provider_state_persist.py -q`
Expected: PASS（两个测试均过：产 state 时发一条 internal-only assistant_state；不产时不发）。

- [ ] **Step 5: 回归 resume 合并不重复（spec §6.2）**

确认 `events_to_dialog_messages`（`src/services/chat_history.py:543-548`）在遇到 assistant_state 时 pop 同回合前面那条 response 文本消息、替换为 assistant_state 还原消息——即「无 tool_calls 时该 pop-替换路径命中、合并为一条 assistant 消息」。

Run:
```bash
uv run pytest tests/ -q -k "chat_history or dialog or resume or assistant_state"
```
Expected: PASS（无重复 assistant 消息）。

- [ ] **Step 6: 提交**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_natural_finish_provider_state_persist.py
git commit -m "feat(core): emit internal-only assistant_state on natural finish with provider_state"
```

---

## Task 8: 持久化必改点二/三——tail restore 携带 provider_state

**Files:**
- Modify: `src/services/chat_history.py`（`events_to_messages` assistant 分支 667-681 行，携带 `provider_state`；import 加 `ProviderState`）
- Test: `tests/services/test_events_to_messages_provider_state.py`

spec §6.3：恢复链路第二层 `events_to_messages`（扁平 dict → `Message`）按 role 手写重建 `AssistantMessage`，只拷 `content`/`reasoning_content`/`tool_calls`，丢 `provider_state`。checkpoint 之后的 assistant_state tail 在 resume 时丢状态。（必改点三 §6.4 已在 Task 6 Step 10 完成。）

- [ ] **Step 1: 写失败测试**

Create `tests/services/test_events_to_messages_provider_state.py`:
```python
from src.services.chat_history import ChatHistoryConverter
from matmaster.types.messages import AssistantMessage, ProviderState


def _assistant_state_event(state: dict) -> dict:
    return {
        "type": "assistant_state",
        "source": "agent",
        "content": state,
    }


def test_events_to_messages_restores_provider_state_no_tool_calls():
    msg = AssistantMessage(
        content="hi",
        provider_state=ProviderState(transport="fake", payload={"sig": "z"}),
    )
    events = [
        {"type": "user", "source": "user", "content": "q"},
        _assistant_state_event(msg.model_dump(mode="json")),
    ]
    restored = ChatHistoryConverter.events_to_messages(events)
    assistants = [m for m in restored if isinstance(m, AssistantMessage)]
    assert assistants, "expected an assistant message"
    assert assistants[-1].provider_state == ProviderState(
        transport="fake", payload={"sig": "z"}
    )
```
> 注：事件构造形状需对齐本仓 `events_to_dialog_messages` 期望的真实事件结构（`type`/`source`/`content`/`task_id`/`session_id` 等）。实现者落地时参照 `tests/services/` 下既有 `events_to_messages` / `events_to_dialog_messages` 测试的事件构造 helper，套同一形状（含 `assistant_state` 经 `restore_persisted_assistant_state` → `model_dump()` 的真实路径）。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/services/test_events_to_messages_provider_state.py -q`
Expected: FAIL（还原后的 `AssistantMessage.provider_state` 为 None）。

- [ ] **Step 3: `events_to_messages` assistant 分支携带 `provider_state`**

`src/services/chat_history.py` import（9-15 行）加 `ProviderState`：
```python
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    ProviderState,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
```
assistant 分支（667-681 行）加 `provider_state` 拷贝：
```python
            elif role == "assistant":
                msg_kwargs: dict = {"content": d.get("content")}
                reasoning_content = d.get("reasoning_content")
                if reasoning_content is not None:
                    msg_kwargs["reasoning_content"] = reasoning_content
                if d.get("tool_calls"):
                    msg_kwargs["tool_calls"] = [
                        ToolCallData(
                            id=tc.get("id", ""),
                            name=tc.get("name", ""),
                            arguments=tc.get("arguments", {}),
                        )
                        for tc in d["tool_calls"]
                    ]
                provider_state = d.get("provider_state")
                if provider_state is not None:
                    msg_kwargs["provider_state"] = ProviderState.model_validate(
                        provider_state
                    )
                messages.append(AssistantMessage(**msg_kwargs))
```
> 选「显式拷贝 provider_state」而非整体 `AssistantMessage.model_validate(d)`：避免 `d` 中可能存在的非模型字段/role 形态差异引入回归，最小改动、行为可控。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/services/test_events_to_messages_provider_state.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/services/chat_history.py tests/services/test_events_to_messages_provider_state.py
git commit -m "feat(persistence): carry provider_state through events_to_messages tail restore"
```

---

## Task 9: 全链路 round-trip 验证 + 完成标准核对

**Files:**
- Test: `tests/matmaster/core/test_provider_state_end_to_end.py`（新增，覆盖 spec §8「provider_state 通道核心新增」与「持久化真实链路」端到端）

- [ ] **Step 1: 写端到端 round-trip 测试**

Create `tests/matmaster/core/test_provider_state_end_to_end.py`:
```python
from matmaster.types.message_normalization import restore_persisted_assistant_state
from matmaster.types.messages import AssistantMessage, ProviderState


def test_assistant_state_dump_restore_round_trip_preserves_provider_state():
    msg = AssistantMessage(
        content="answer",
        reasoning_content="because",
        provider_state=ProviderState(transport="fake", payload={"sig": "z", "n": 1}),
    )
    dumped = msg.model_dump(mode="json")
    restored = restore_persisted_assistant_state(dumped)
    assert isinstance(restored, AssistantMessage)
    assert restored.provider_state == ProviderState(
        transport="fake", payload={"sig": "z", "n": 1}
    )


def test_restore_payload_json_serializable_round_trip():
    state = ProviderState(transport="fake", payload={"a": [1, 2], "b": {"c": "d"}})
    # 持久化层统一 model_dump(mode="json") —— 不抛 + round-trip 一致
    assert ProviderState.model_validate(state.model_dump(mode="json")) == state
```

- [ ] **Step 2: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/core/test_provider_state_end_to_end.py -q`
Expected: PASS（`restore_persisted_assistant_state` 走 `model_validate`，`provider_state` 为可选字段自动还原）。

- [ ] **Step 3: 完整套件 + 当前环境可用的静态终检**

Run:
```bash
uv run pytest tests/ -q
git diff --check
uv run python -m compileall matmaster src
```
Expected: pytest PASS；`git diff --check` 无输出；`compileall` 退出码 0。若 ruff/mypy 后续重新进入 uv 环境，可额外运行，但不要把当前不可用命令作为完成标准。

- [ ] **Step 4: 完成标准逐条核对（spec §10）**

逐条确认（无需改码，仅核对，缺口回到对应 Task 补）：
- `LLMProvider.chat/chat_stream`（及 `stream_llm_items`/`call_llm_streaming` 参数）收 `list[Message]`；`to_api_dict` 已从发送路径移除；OpenAI 序列化+校验下沉 `convert_messages`。 → Task 6
- `Message.to_api_dict()` 删除；`estimate_tokens` 中立序列化；compaction summary call 传 `list[Message]`；`message_normalization` wire 函数迁入 transport；tool-turn 校验改中立 `validate_tool_turn_sequence`。 → Task 3/6
- `message_pipeline` 收窄为产 canonical `list[Message]`，不再产 dict / 不再做 OpenAI 校验。 → Task 6
- `ProviderState` 落地（payload JSON 契约 + 测试）；三类内容字段分离（`content`/`reasoning_content`/`provider_state`）。 → Task 2
- provider_state 全链路通道贯通：transport 流末产出 → 聚合 → `LLMResponse` → `AssistantMessage` → 持久化 → resume round-trip，kernel 全程不透明搬运。 → Task 5/7/8/9
- 持久化真实链路补齐：自然完成分支条件发 assistant_state；`events_to_messages` tail restore 携带 `provider_state`；`validate_base_messages` 改中立校验。无 tool_calls 的 provider_state 持久化/resume 有测试覆盖。 → Task 6/7/8
- tag 丢弃 helper + 契约就位（3a 惰性、单 transport 不触发）。 → Task 4
- chat_completions 路径行为等价（恒不产 provider_state、不新增 assistant_state 事件）；openai 风格 profile + BYOK 行为等价。 → Task 5/6/7（laziness 回归测试）
- 各接缝有独立测试（`convert_messages` / 中立 tool-turn 校验 / provider_state 聚合 / tag helper / estimate_tokens / 持久化真实链路）。 → Task 2-9

- [ ] **Step 5: 提交**

```bash
git add tests/matmaster/core/test_provider_state_end_to_end.py
git commit -m "test(core): end-to-end provider_state round-trip and JSON contract"
```

---

## 3a 明确不做（spec §9，实施时勿越界）

- 不引入 native `anthropic_messages` / `responses` transport（3b/3c）。
- 不产真实 provider_state（chat_completions 恒 None）。
- 不搬 prompt cache 断点策略、不做 native signed thinking / encrypted reasoning。
- 不做 inline thinking 剥离（3a 只立 `content` 已剥离的字段语义）。
- 不做 automatic fallback、不做手动切协议的**实际**丢弃触发（契约+helper 就位，3b 生效）。
- 不做 Gemini native。
- 不引入持久化迁移脚本（`provider_state` 为可选新增字段，旧记录缺它时 `model_validate` 默认 None）。
- 不改 kernel 主循环**控制流**（自然完成分支的 assistant_state 条件发射是新增一条 internal-only 发射、非控制流重构）。

---

## Self-Review 记录

- **Spec 覆盖**：§3 契约重构 → Task 6；§4 ProviderState/三类内容/聚合/tag 丢弃 → Task 2/4/5；§5 pipeline 收窄 → Task 6 Step 5；§6 持久化真实链路（§6.2/6.3/6.4）→ Task 7/8/6 Step 10；§7 装饰器与集成点（`stream_llm_items`/`call_llm_streaming` 参数、compaction summary call）→ Task 6；§8 测试策略 → 分散于各 Task 测试 + Task 9；§10 完成标准 → Task 9 Step 4。`BillingLLMProvider`/`UsageCollectingProvider` 透传无感（§7）——`__getattr__` 透传，3a 无需改，已在 Task 6 pytest/compileall 终检间接覆盖；如其显式声明了 `chat/chat_stream` 形参注解，Task 6 Step 8 的签名统一应一并核对（实施者注意）。
- **Placeholder 扫描**：各 code step 均给出完整代码；持久化端到端测试（Task 7/8）的 harness 因依赖本仓既有集成测试 fixture 形状，给出明确复用指引而非空壳。
- **类型一致性**：`canonical_messages: list[Message]` 在 pipeline/stream/agent/protocol/transport 全链路统一；`ProviderState(transport, payload)`、`_claim_provider_state(msg) -> dict | None`、`validate_tool_turn_sequence(list[Message])`、`convert_messages(list[Message]) -> list[dict]` 命名与签名跨 Task 一致。
